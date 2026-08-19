"""`v_screen`'s `config_hash` and `arm` predicates (Session 12.5).

ADR 100 and ADR 105. Two defects this file exists to keep out:

1. `cell_stats` is keyed `(cell_id, config_hash)` since ADR 096, so it holds
   one row per cell **per config**. Without a `config_hash` predicate the
   join fans out and every screener row duplicates once per config. Session
   9's sweep already wrote 18 `config_hash` values into `events`, so this
   is live rather than hypothetical.
2. Phase 4 measures controls and benchmarks it will never recommend. A row
   holding `p_hit = 0.61` looks identical whether it describes a signal or
   a random-entry null.

**Writes only its own rows and deletes them.** No `TRUNCATE` anywhere,
deliberately: `events` holds 683k+ production rows. Scoping is by a
`config_hash` and tickers generated per run, so a cleanup failure still
cannot reach a production row.

    uv run pytest capitalscan/tests/integration/test_v_screen_predicates.py

**Why the fixture leaves `signal_strength` NULL.** ADR 107. The view pins
`c.signal_strength IS NULL` — "this cell is pooled over strength" — rather
than joining it to the event's own value, exactly as it already pins
`c.era IS NULL` for the pooled era row. Session 12's real rows carry NULL
there because ADR 102 removed strength as a grid dimension, so the fixture
matches production shape.

`test_a_strength_conditioned_row_does_not_match` is the other half: the
predicate has to reject a populated row, or it is not selecting the pooled
row, it is selecting any row.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.jobs import db_io

GUC = "capitalscan.default_config_hash"


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

_EVENT_SQL = text("""
    INSERT INTO events (run_id, config_hash, ticker, signal_date, signal_type,
                        signal_strength, side, entry_kind, split_key,
                        dd_bucket, is_cluster_head)
    VALUES (:run_id, :config_hash, :ticker, :signal_date, :signal_type,
            :signal_strength, :side, 'next_open', 'train', :dd_bucket, true)
""")

_CELL_SQL = text("""
    INSERT INTO cell_stats (cell_id, run_id, config_hash, signal_type, side,
                            dd_bucket, signal_strength, entry_kind, split_key,
                            era, horizon_days, target_pct, arm, p_hit,
                            n_events, n_eff, suppressed, computed_at, git_sha)
    VALUES (:cell_id, :run_id, :config_hash, :signal_type, :side, :dd_bucket,
            :signal_strength, 'next_open', 'validate', NULL, 5, 0.03, :arm,
            0.42, 100, 50, false, :computed_at, 'test')
""")


@pytest.fixture()
def scoped():
    """Two events and their `cell_stats` rows, cleaned up after.

    `signal` event: one cell row under `config_a`, a second identical cell
    row under `config_b`. That pair is what makes the duplication defect
    reachable.

    `control` event: a single cell row whose `arm` is overridden per test.
    """
    engine = db_io.get_engine()
    token = uuid.uuid4().hex[:10]
    run_id = f"test_vscreen_{token}"
    config_a = f"cfgA{token}"
    config_b = f"cfgB{token}"
    signal_ticker = f"ZZS{token[:6]}".upper()
    other_ticker = f"ZZC{token[:6]}".upper()
    signal_date = date(2023, 6, 15)
    computed_at = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runs (run_id, job, started_at, status, git_sha, params) "
                "VALUES (:run_id, 'test', now(), 'ok', 'test', '{}'::jsonb)"
            ),
            {"run_id": run_id},
        )
        for ticker, signal_type, dd_bucket in (
            (signal_ticker, "bb_lower_touch", "0-10"),
            (other_ticker, "stoch_oversold", "10-20"),
        ):
            conn.execute(
                _EVENT_SQL,
                {
                    "run_id": run_id,
                    "config_hash": config_a,
                    "ticker": ticker,
                    "signal_date": signal_date,
                    "signal_type": signal_type,
                    "signal_strength": 1,
                    "side": "long",
                    "dd_bucket": dd_bucket,
                },
            )

    context = {
        "engine": engine,
        "run_id": run_id,
        "config_a": config_a,
        "config_b": config_b,
        "signal_ticker": signal_ticker,
        "other_ticker": other_ticker,
        "computed_at": computed_at,
    }
    try:
        yield context
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM cell_stats WHERE config_hash IN (:a, :b)"),
                {"a": config_a, "b": config_b},
            )
            conn.execute(
                text("DELETE FROM events WHERE config_hash IN (:a, :b)"),
                {"a": config_a, "b": config_b},
            )
            conn.execute(text("DELETE FROM runs WHERE run_id = :run_id"), {"run_id": run_id})


def _add_cell(
    conn,
    ctx,
    *,
    config_hash,
    arm,
    signal_type="bb_lower_touch",
    dd_bucket="0-10",
    signal_strength=None,
):
    """A `cell_stats` row in production shape: `signal_strength` NULL,
    meaning pooled over strength (ADR 102, ADR 107). Tests that need a
    strength-conditioned row pass one explicitly."""
    slot = "all" if signal_strength is None else str(signal_strength)
    conn.execute(
        _CELL_SQL,
        {
            "cell_id": f"{signal_type}|long|{dd_bucket}|{slot}|next_open|validate|pooled|h5|t0.03",
            "run_id": ctx["run_id"],
            "config_hash": config_hash,
            "signal_type": signal_type,
            "side": "long",
            "dd_bucket": dd_bucket,
            "signal_strength": signal_strength,
            "arm": arm,
            "computed_at": ctx["computed_at"],
        },
    )


def _screen_rows(conn, ticker):
    return conn.execute(
        text("SELECT ticker, cell_id, p_hit FROM v_screen WHERE ticker = :ticker"),
        {"ticker": ticker},
    ).fetchall()


def _set_guc(conn, value):
    conn.execute(text("SELECT set_config(:name, :value, false)"), {"name": GUC, "value": value})


class TestConfigHashPredicate:
    def test_a_signal_row_under_the_default_config_reaches_the_screen(self, scoped):
        """The join must actually fire, or every assertion below is vacuous."""
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal")
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert len(rows) == 1
        assert rows[0].cell_id is not None
        assert rows[0].p_hit is not None

    def test_a_second_config_adds_no_duplicate_row(self, scoped):
        """ADR 100. Two configs holding the same cell must still yield one
        screener row per event."""
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal")
            _add_cell(conn, scoped, config_hash=scoped["config_b"], arm="signal")
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert len(rows) == 1

    def test_without_the_predicate_the_same_data_duplicates(self, scoped):
        """Proves the test above is not passing for an unrelated reason.

        Runs the view's own join with the `config_hash` predicate removed,
        against the identical two rows. Two results here and one above is
        the whole content of ADR 100. If this ever returns 1, the fixture
        has stopped reproducing the defect and the test above is worthless.

        **Scoped by `c.run_id` since 2026-08-19.** Without it the join had
        no `config_hash` *and* no ticker predicate on `cell_stats`, so it
        counted every production cell sharing the fixture's signature --
        measured, 6 of them locally -- and the assertion held only on an
        empty database. It passed in CI and failed on any developer machine
        with real statistics, which is the worst place for a test to be
        selective about.

        `run_id` rather than `config_hash`: the absent config predicate is
        the thing under test and must stay absent.
        """
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal")
            _add_cell(conn, scoped, config_hash=scoped["config_b"], arm="signal")
            unguarded = conn.execute(
                text("""
                    SELECT count(*) FROM events e
                    LEFT JOIN cell_stats c
                           ON c.signal_type     = e.signal_type
                          AND c.side            = e.side
                          AND c.dd_bucket       = e.dd_bucket
                          AND c.signal_strength IS NULL
                          AND c.entry_kind      = e.entry_kind
                          AND c.split_key       = 'validate'
                          AND c.era             IS NULL
                          AND c.horizon_days    = 5
                          AND c.target_pct      = 0.03
                          AND c.arm             = 'signal'
                          AND c.run_id          = :run_id
                    WHERE e.is_cluster_head AND e.entry_kind = 'next_open'
                      AND e.ticker = :ticker
                """),
                {"ticker": scoped["signal_ticker"], "run_id": scoped["run_id"]},
            ).scalar_one()
        assert unguarded == 2

    def test_an_unset_config_hash_serves_nothing(self, scoped):
        """**Changed 2026-08-19 by ADR 119, and the old behaviour was the bug.**

        This asserted the opposite: that an unset GUC kept the event and
        nulled only the statistics, because "the LEFT JOIN is what stops an
        unconfigured database from serving an empty screener".

        It stopped it by serving a *wrong* one. The predicate was on the
        `cell_stats` join only and never on `events`, so an unconfigured
        database did not show one config's events without statistics -- it
        showed **every** config's events at once. Measured on the live
        database before the fix: 23 distinct `config_hash` values and
        799,455 rows, of which 46 were on the newest date and 17 of those
        belonged to a superseded generation.

        Empty is the answer the rest of the system already gives.
        `v_events` filters `e.config_hash = current_setting(...)`.
        `compute.scan` returns an empty frame and says why: "guessing a
        config is worse than admitting there is nothing to show".
        `handlers/_db.py::resolve_config_hash` goes further and raises, so
        the misconfiguration is loud rather than silent.
        """
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal")
            conn.execute(text("SELECT set_config(:name, '', true)"), {"name": GUC})
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert rows == []

    def test_only_the_default_configs_events_are_served(self, scoped):
        """The other half of the same predicate, and the defect itself.

        An event belonging to a superseded config must not appear at all,
        rather than appearing with null statistics beside a live-config row.
        """
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal")
            _set_guc(conn, scoped["config_b"])
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert rows == []

    def test_a_non_default_config_does_not_leak_its_statistics(self, scoped):
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_b"], arm="signal")
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert len(rows) == 1
        assert rows[0].cell_id is None


class TestStrengthPooling:
    """ADR 107. The view selects the cell pooled over `signal_strength`,
    the same way it already selects the row pooled over `era`."""

    def test_a_pooled_row_matches(self, scoped):
        """Production shape: `signal_strength` NULL. Before ADR 107 the
        view joined `c.signal_strength = e.signal_strength`, so this row
        could never match and every Session 12 statistic was invisible."""
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal")
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert len(rows) == 1
        assert rows[0].cell_id is not None

    def test_a_strength_conditioned_row_does_not_match(self, scoped):
        """The other half of the predicate. `IS NULL` has to *reject* a
        populated row, or the view is not selecting the pooled cell, it is
        selecting whichever cell happens to exist.

        This is the anti-fan-out guard: `cell_id` embeds strength, so a
        strength-split row and a pooled row are distinct rows for one
        cell. A view that accepted both would duplicate every screener row,
        which is the ADR 100 defect wearing a different hat.
        """
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal", signal_strength=1)
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert len(rows) == 1
        assert rows[0].cell_id is None

    def test_a_pooled_and_a_split_row_together_yield_one_row(self, scoped):
        """Both rows present, which is the state that would exist if
        strength ever became a real dimension alongside the pooled cell.
        Exactly one must reach the screen."""
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal")
            _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signal", signal_strength=1)
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["signal_ticker"])
        assert len(rows) == 1
        assert rows[0].cell_id is not None
        assert rows[0].cell_id.split("|")[3] == "all"


class TestArmPredicate:
    @pytest.mark.parametrize("arm", ["control", "benchmark"])
    def test_a_non_signal_arm_never_reaches_the_screen(self, scoped, arm):
        """ADR 105. Asserting only that `signal` rows appear would pass on a
        view with no predicate at all, so this asserts the negative."""
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(
                conn,
                scoped,
                config_hash=scoped["config_a"],
                arm=arm,
                signal_type="stoch_oversold",
                dd_bucket="10-20",
            )
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["other_ticker"])
        assert len(rows) == 1
        assert rows[0].cell_id is None, f"{arm} statistics reached the screener"

    def test_the_same_row_as_signal_does_reach_it(self, scoped):
        """The control for the two tests above: identical row, `arm`
        flipped. Without this, they would also pass on a join that never
        matches for some unrelated reason."""
        engine = scoped["engine"]
        with engine.begin() as conn:
            _add_cell(
                conn,
                scoped,
                config_hash=scoped["config_a"],
                arm="signal",
                signal_type="stoch_oversold",
                dd_bucket="10-20",
            )
            _set_guc(conn, scoped["config_a"])
            rows = _screen_rows(conn, scoped["other_ticker"])
        assert len(rows) == 1
        assert rows[0].cell_id is not None


class TestSchemaGuarantees:
    def test_the_view_carries_both_predicates(self, scoped):
        engine = scoped["engine"]
        with engine.connect() as conn:
            definition = conn.execute(
                text("SELECT pg_get_viewdef('v_screen'::regclass)")
            ).scalar_one()
        assert "default_config_hash" in definition
        assert "arm = 'signal'" in definition

    def test_the_arm_check_constraint_rejects_an_unknown_value(self, scoped):
        """A free-text `arm` would let a typo silently vanish from the
        screener rather than fail at write time."""
        engine = scoped["engine"]
        with pytest.raises(Exception, match="cell_stats_arm_check"):
            with engine.begin() as conn:
                _add_cell(conn, scoped, config_hash=scoped["config_a"], arm="signl")

    def test_arm_defaults_to_signal(self, scoped):
        """Every row Session 12 wrote predates the column and must read as
        `signal` without a backfill."""
        engine = scoped["engine"]
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO cell_stats (cell_id, run_id, config_hash, computed_at)
                    VALUES (:cell_id, :run_id, :config_hash, now())
                """),
                {
                    "cell_id": f"default_probe_{scoped['config_a']}",
                    "run_id": scoped["run_id"],
                    "config_hash": scoped["config_a"],
                },
            )
            arm = conn.execute(
                text(
                    "SELECT arm FROM cell_stats "
                    "WHERE config_hash = :c AND cell_id LIKE 'default_probe%'"
                ),
                {"c": scoped["config_a"]},
            ).scalar_one()
        assert arm == "signal"
