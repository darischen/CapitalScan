"""`v_positions` against the live database (ADR 095, ADR 115, session 15.4).

Three things the unit tests cannot show, because they read the DDL text
rather than run it:

1. The view returns what the settings row says, and **moves when the row
   moves**. That is the whole claim: sweep `exit_stoch_threshold` and the
   position page follows the backtest instead of contradicting it.
2. The view and `core/exits.py` agree on the same state. Two
   implementations of one rule in two languages, compared on real data.
3. The row-existence dependency degrades to NULL rather than to zero rows.

Skips when `DATABASE_URL_RESEARCH` is unreachable, like every other module
here. Unlike them it does **not** truncate - see the note below the imports.
`serving_config` is restored in a finally block, because leaving a swept
threshold behind would silently change every later read of the view.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core.config import Config, ExitParams
from capitalscan.core.types import Side
from capitalscan.jobs import db_io, positions
from capitalscan.jobs.views import SERVING_CONFIG_COLUMNS, SERVING_CONFIG_UPSERT

# **This module does not truncate.** `test_positions.py` truncates
# `positions` and `order_intents` around every test, and on 2026-08-18 the
# live research database held 748 `order_intents` rows - a `TRUNCATE
# positions CASCADE` would have taken all of them. That convention is safe
# on a CI container built from migrations and is not safe here, so this
# module creates its own rows and deletes exactly those.


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture()
def engine():
    """The live engine, with every position this test opened removed after.

    Tracked by id rather than by ticker or by a truncate: a truncate is a
    blunt instrument against a table someone else's data shares a foreign
    key with, and matching on ticker would delete a real position in the
    same name.
    """
    eng = db_io.get_engine()
    before = _position_ids(eng)
    yield eng
    added = _position_ids(eng) - before
    if added:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM positions WHERE id = ANY(:ids)"), {"ids": sorted(added)})


def _position_ids(engine) -> set[int]:
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(text("SELECT id FROM positions"))}


@contextmanager
def _conn(engine):
    """A connection with parallel gather disabled, for every read here.

    `v_positions` LEFT JOINs `v_ticker_state`, which is a `DISTINCT ON
    (ticker)` over the whole `indicators` table - 4.5M+ rows on the
    developer database. Postgres plans a parallel gather and it fails with
    `could not resize shared memory segment ... No space left on device`
    (CLAUDE.md). The GUC is a *session* setting, so setting it on one
    connection does not help the next one out of the pool; every read in
    this module goes through here.

    **The cost this used to carry is gone (ADR 116).** `v_ticker_state`
    took 26.5 s to materialize and every read here paid it; it was rewritten
    the same day as a loose index scan and now takes 27 ms, with a
    `v_positions` row at 23.5 ms.

    The GUC stays. The old view is still rebuilt by
    `test_v_ticker_state_rewrite.py` to prove the rewrite changed no output,
    and that one genuinely needs parallelism off.
    """
    with engine.connect() as conn:
        conn.execute(text("SET max_parallel_workers_per_gather = 0"))
        yield conn


@pytest.fixture()
def settings(engine):
    """Restores `serving_config` afterwards, whatever the test did to it.

    A test that swept a threshold and died would otherwise leave the
    serving surface reporting exits at 70 forever, with nothing pointing at
    the test that did it.
    """
    with engine.connect() as conn:
        before = (
            conn.execute(text(f"SELECT {', '.join(SERVING_CONFIG_COLUMNS)} FROM serving_config"))
            .mappings()
            .first()
        )
    try:
        yield engine
    finally:
        if before is not None:
            with engine.begin() as conn:
                conn.execute(text(SERVING_CONFIG_UPSERT), dict(before))


def _write_settings(engine, **over):
    from capitalscan.jobs.views import serving_config_values

    values = serving_config_values(Config(exits=ExitParams(**over)), config_hash="test")
    with engine.begin() as conn:
        conn.execute(text(SERVING_CONFIG_UPSERT), values)


def _state_row(engine, side_hint: str):
    """A real `v_ticker_state` row, chosen so the test has something to say.

    Picking the most extreme %K in the trade universe rather than a fixed
    ticker: a hardcoded name eventually leaves the universe or gets
    delisted, and the test then passes vacuously on a NULL join.
    """
    order = "DESC" if side_hint == "long" else "ASC"
    with _conn(engine) as conn:
        row = (
            conn.execute(
                text(
                    "SELECT ticker, close, k_full, k_fast, bb_upper, bb_mid, bb_lower "
                    "FROM v_ticker_state "
                    "WHERE k_full IS NOT NULL AND close IS NOT NULL "
                    f"ORDER BY k_full {order} LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        pytest.skip("no v_ticker_state rows; run `cscan indicators` first")
    return row


def _view_row(engine, position_id: int):
    with _conn(engine) as conn:
        return (
            conn.execute(text("SELECT * FROM v_positions WHERE id = :id"), {"id": position_id})
            .mappings()
            .first()
        )


# ---------------------------------------------------------------------------
# 1. The view follows config
# ---------------------------------------------------------------------------


def test_moving_the_threshold_moves_the_view(settings):
    """ADR 095's failure mode, reproduced and then fixed, in one test.

    The old view compared `k_full >= 80` as a constant. A sweep to a
    threshold on the far side of the ticker's current %K has to flip the
    flag; against the old view it could not, because the number was not in
    the database at all.
    """
    engine = settings
    state = _state_row(engine, "long")
    k = float(state["k_full"])
    opened = positions.open_position(
        engine, state["ticker"], "long", date(2026, 7, 1), float(state["close"])
    )

    _write_settings(engine, exit_stoch_threshold=max(0.0, k - 1.0))
    assert _view_row(engine, opened["id"])["exit_signal_stoch"] is True

    _write_settings(engine, exit_stoch_threshold=min(100.0, k + 1.0))
    assert _view_row(engine, opened["id"])["exit_signal_stoch"] is False


def test_the_stochastic_source_follows_config(settings):
    """ADR 110 moved the entry trigger to `k_fast`.

    Whichever column the exit policy reads, the view must read the same
    one, or the position page and the backtest disagree about what
    "overbought" means.
    """
    engine = settings
    state = _state_row(engine, "long")
    opened = positions.open_position(
        engine, state["ticker"], "long", date(2026, 7, 1), float(state["close"])
    )

    _write_settings(engine, exit_stoch_source="k_full")
    assert float(_view_row(engine, opened["id"])["exit_stoch_k"]) == pytest.approx(
        float(state["k_full"])
    )
    _write_settings(engine, exit_stoch_source="k_fast")
    assert float(_view_row(engine, opened["id"])["exit_stoch_k"]) == pytest.approx(
        float(state["k_fast"])
    )


def test_the_mid_band_signal_is_null_when_the_policy_is_off(settings):
    """ADR 046 defaults `exit_on_mid_band` False.

    The old view published the signal regardless, with nothing saying it
    was inactive. NULL rather than false, because "this rule is not in
    force" and "this rule is in force and has not fired" are different
    facts and a reader acts differently on each.
    """
    engine = settings
    state = _state_row(engine, "long")
    opened = positions.open_position(
        engine, state["ticker"], "long", date(2026, 7, 1), float(state["close"])
    )

    _write_settings(engine, exit_on_mid_band=False)
    assert _view_row(engine, opened["id"])["exit_signal_mid_band"] is None
    _write_settings(engine, exit_on_mid_band=True)
    assert _view_row(engine, opened["id"])["exit_signal_mid_band"] is not None


# ---------------------------------------------------------------------------
# 2. The view and core/exits.py agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("side", ["long", "short"])
def test_the_view_and_core_exits_agree_on_the_stochastic_rule(settings, side):
    """Two implementations of one rule, in two languages, on real state.

    `core/exits.py` reads `k >= exit_stoch_threshold` for a long and
    `k <= exit_stoch_threshold_short` for a short - the short is not a
    mirror (ADR 016) and the two sweep independently (ADR 092). The old
    view applied the long threshold to every row, so an open short read its
    exit off the wrong number and was wrong in the direction that
    *suppresses* the exit.
    """
    engine = settings
    ep = ExitParams()
    _write_settings(engine)

    state = _state_row(engine, side)
    opened = positions.open_position(
        engine, state["ticker"], side, date(2026, 7, 1), float(state["close"])
    )

    k = float(state[ep.exit_stoch_source])
    expected = (
        k >= ep.exit_stoch_threshold
        if side == Side.LONG.value
        else k <= ep.exit_stoch_threshold_short
    )
    assert _view_row(engine, opened["id"])["exit_signal_stoch"] is expected


@pytest.mark.parametrize("side", ["long", "short"])
def test_the_view_and_core_exits_agree_on_the_far_band(settings, side):
    """`core/exits.py`: the far band is `bb_upper` for a long and
    `bb_lower` for a short. The old view used `bb_upper` for both."""
    engine = settings
    _write_settings(engine)
    state = _state_row(engine, side)
    opened = positions.open_position(
        engine, state["ticker"], side, date(2026, 7, 1), float(state["close"])
    )

    close = float(state["close"])
    expected = (
        close >= float(state["bb_upper"])
        if side == Side.LONG.value
        else close <= float(state["bb_lower"])
    )
    assert _view_row(engine, opened["id"])["exit_signal_upper_band"] is expected


def test_days_held_counts_sessions_not_calendar_days(settings):
    """`max_hold_days` counts bars; the old view counted calendar days.

    A Thursday entry reads 4 calendar days and 2 sessions on the following
    Monday, so the old timeout flag fired early over every weekend. Not a
    defect ADR 095 named - it surfaced while writing this fixture.
    """
    engine = settings
    _write_settings(engine)
    state = _state_row(engine, "long")
    entry = date(2026, 7, 1)
    opened = positions.open_position(engine, state["ticker"], "long", entry, float(state["close"]))

    with engine.connect() as conn:
        sessions = conn.execute(
            # `market_date()`, not `CURRENT_DATE` (ADR 119). The database runs
            # Etc/UTC, so between 00:00 UTC and midnight ET the two differ by a
            # day and this assertion is off by one session -- which is the defect
            # the view was changed to fix, and how this test caught the change.
            text("SELECT count(*) FROM trading_days WHERE d > :entry AND d <= market_date()"),
            {"entry": entry},
        ).scalar_one()
    row = _view_row(engine, opened["id"])
    assert row["days_held"] == sessions
    # Sessions never exceed calendar days. `date.today()` is the *client's*
    # day, which is Pacific and can trail the market's by one in the other
    # direction, so the bound is generous by a day rather than exact.
    assert row["days_held"] <= (date.today() - entry).days + 1


# ---------------------------------------------------------------------------
# 3. The cost of the settings row, bounded
# ---------------------------------------------------------------------------


def test_a_missing_settings_row_leaves_the_position_visible(settings):
    """ADR 095 named the row-existence dependency as this option's cost.

    Bounded here: the position still renders, its exit flags read NULL, and
    nobody is told they have no positions.
    """
    engine = settings
    state = _state_row(engine, "long")
    opened = positions.open_position(
        engine, state["ticker"], "long", date(2026, 7, 1), float(state["close"])
    )
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM serving_config"))

    row = _view_row(engine, opened["id"])
    assert row is not None
    assert row["ticker"] == state["ticker"]
    assert row["exit_signal_stoch"] is None
    assert row["exit_signal_timeout"] is None


def test_the_settings_table_can_hold_only_one_row(engine):
    """A settings table that can hold two rows eventually does, and the
    view would then multiply every position by the row count."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO serving_config (only_row, config_hash, exit_stoch_source, "
                    "exit_stoch_threshold, exit_stoch_threshold_short, exit_on_stoch_80, "
                    "exit_on_upper_band, exit_on_mid_band, max_hold_days) "
                    "VALUES (false, 'x', 'k_full', 80, 20, true, true, false, 5)"
                )
            )


def test_the_stored_row_matches_the_live_config(engine):
    """The staleness guard the settings-row design needs to be honest.

    A migration is applied once. Without this, the row goes stale the first
    time `ExitParams` moves and the view quietly reports yesterday's policy
    - which is the same defect ADR 095 found, one indirection further out.
    Fix by running `cscan db sync-config`.
    """
    from capitalscan.jobs.views import serving_config_values

    with engine.connect() as conn:
        stored = (
            conn.execute(text(f"SELECT {', '.join(SERVING_CONFIG_COLUMNS)} FROM serving_config"))
            .mappings()
            .first()
        )
    if stored is None:
        pytest.skip("serving_config is empty; run `cscan db sync-config`")

    wanted = serving_config_values(config_hash=stored["config_hash"])
    for name, value in wanted.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert float(stored[name]) == float(value), (
                f"serving_config.{name} is {stored[name]} and ExitParams says {value}. "
                "Run `cscan db sync-config`."
            )
        else:
            assert stored[name] == value, f"serving_config.{name}: run `cscan db sync-config`"
