"""`run_sync --incremental` ships what changed; the bare command copies all.

**Two commands on purpose (user's call, 2026-08-26).** `cscan sync` still
means "copy the serving subset", unbounded, because that is what you reach
for after a rebuild, a reflash or a config change. Nightly passes
`incremental=True`, and it is the only caller that does.

**Measured 2026-08-26.** A full sync copied **7,469,519 rows in 114.2
minutes** to deliver about **3,875** that had changed -- a 1,900x
amplification, and two thirds of a nightly.

Two causes compounding, neither wrong alone. `run_sync` selected by
`cutoff_date` and had no notion of "since last time". And
`ServingParams.history_years` is **30**: it was 3, sized against Neon's
512 MB free tier, and raising it when the Pi replaced Neon quietly turned
`cutoff` into "the beginning of time". The predicate still ran, still
filtered, and selected everything.

**The bound comes from the target, not from a constant.** A fixed "last 7
days" is wrong exactly when it matters: a serving store that has been off
for a fortnight would get seven days of rows and keep a permanent hole,
with nothing reporting it. Reading the target's own newest row makes the
window however far behind it actually is, plus `SYNC_OVERLAP_DAYS`.

No database here: both engines are stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError

from capitalscan.jobs import sync as sync_mod

CONFIG = "a38d3ca6b58295e8"
BOUND_KEYS = ("bars_from", "indicators_from", "events_from", "reports_from")


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Conn:
    def __init__(self, answers: dict[str, object]):
        self._answers = answers

    def execution_options(self, **kwargs):
        """`run_sync` opens its read transaction with
        `.execution_options(isolation_level="REPEATABLE READ")` so every
        table sees one snapshot of the source (2026-08-28). The double
        records the level rather than ignoring it, so a test can assert the
        isolation actually asked for."""
        self.isolation_level = kwargs.get("isolation_level")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        for needle, value in self._answers.items():
            if needle in sql:
                return _Result(value)
        return _Result(None)


class _Engine:
    def __init__(self, answers: dict[str, object] | None = None, raises: bool = False):
        self._answers = answers or {}
        self._raises = raises

    def connect(self):
        if self._raises:
            raise OperationalError("select 1", {}, Exception("target unreachable"))
        return _Conn(self._answers)

    def begin(self):
        """`run_sync` opens a transaction on the target to reset its
        sequences (2026-08-28). Serving's rows arrive from the sync with
        their own ids, and an insert with an explicit id does not advance
        the sequence -- so without the reset the Pi's poller collides on
        the next insert, which is how a live session died eleven minutes
        in."""
        if self._raises:
            raise OperationalError("select 1", {}, Exception("target unreachable"))
        return _Conn(self._answers)


# ---------------------------------------------------------------------------
# The watermark
# ---------------------------------------------------------------------------


def test_the_bound_is_the_targets_own_watermark_minus_the_overlap():
    target = _Engine(
        {
            "FROM bars": date(2026, 8, 25),
            "FROM indicators": date(2026, 8, 25),
            "FROM events": date(2026, 8, 26),
            "FROM signal_reports": date(2026, 8, 26),
        }
    )
    got = sync_mod._incremental_bounds(target, CONFIG)
    assert got["bars_from"] == date(2026, 8, 25) - timedelta(days=sync_mod.SYNC_OVERLAP_DAYS)
    assert got["events_from"] == date(2026, 8, 26) - timedelta(days=sync_mod.SYNC_OVERLAP_DAYS)


def test_a_target_far_behind_gets_a_correspondingly_wide_window():
    """The case a fixed 7-day window silently breaks. A Pi off for a
    fortnight must be caught up, not handed a hole."""
    stale = _Engine({"FROM bars": date(2026, 8, 1)})
    fresh = _Engine({"FROM bars": date(2026, 8, 25)})
    assert (
        sync_mod._incremental_bounds(stale, CONFIG)["bars_from"]
        < sync_mod._incremental_bounds(fresh, CONFIG)["bars_from"]
    )


def test_an_empty_table_means_no_incremental_floor():
    """A first sync, or a rebuilt serving store. `None` is what makes the
    full `cutoff` pass happen without anyone passing a flag."""
    got = sync_mod._incremental_bounds(_Engine({}), CONFIG)
    assert all(got[k] is None for k in BOUND_KEYS)


def test_an_unreadable_target_falls_back_to_a_full_pass():
    """Being slow is recoverable. Guessing a watermark and shipping a
    subset is not."""
    got = sync_mod._incremental_bounds(_Engine(raises=True), CONFIG)
    assert all(got[k] is None for k in BOUND_KEYS)


def test_the_overlap_is_wide_enough_to_absorb_a_failed_night():
    """The sync upserts, so re-shipping costs bandwidth and nothing else.
    One day would lose a restated bar or a night that died halfway."""
    assert sync_mod.SYNC_OVERLAP_DAYS >= 3


# ---------------------------------------------------------------------------
# The predicates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table,param",
    [
        ("bars", ":bars_from"),
        ("indicators", ":indicators_from"),
        ("events", ":events_from"),
        ("signal_reports", ":reports_from"),
    ],
)
def test_each_large_table_takes_an_incremental_bound(table, param):
    sql = {t.name: t.sql for t in sync_mod._tables(date(2020, 1, 1), CONFIG)}[table]
    assert param in sql


@pytest.mark.parametrize("table", ["bars", "indicators", "events", "signal_reports"])
def test_a_null_bound_degrades_to_the_cutoff_not_to_nothing(table):
    """`COALESCE(..., :cutoff)` inside `GREATEST` is what makes a NULL bound
    safe. Without it a NULL would make the comparison NULL and the table
    would ship zero rows -- an empty serving store that looks like a quiet
    day, which is the failure ADR 115 already warns about for the GUC."""
    sql = {t.name: t.sql for t in sync_mod._tables(date(2020, 1, 1), CONFIG)}[table]
    assert "COALESCE" in sql and "GREATEST" in sql


def test_the_small_tables_are_not_bounded():
    """`tickers`, the calendars and `universe` are reference data. Trimming
    them trades a rounding error in size for a class of bug where a chart
    resolves a date the calendar no longer knows."""
    for name in ("tickers", "trading_days", "market_days", "universe", "serving_config"):
        sql = {t.name: t.sql for t in sync_mod._tables(date(2020, 1, 1), CONFIG)}[name]
        assert not any(p in sql for p in BOUND_KEYS)


def test_signal_reports_still_has_its_where_clause():
    """Regression: an edit once produced `SELECT * FROM signal_reports AND
    fired_at >= ...`, which is a syntax error rather than a wrong answer --
    but only at execution time, and this table is ninth of fourteen."""
    sql = {t.name: t.sql for t in sync_mod._tables(date(2020, 1, 1), CONFIG)}["signal_reports"]
    assert " WHERE " in sql


# ---------------------------------------------------------------------------
# run_sync wiring
# ---------------------------------------------------------------------------


def _capture(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def _read_sql(statement, con, params=None):
        seen.append(dict(params or {}))
        return pd.DataFrame()

    monkeypatch.setattr(sync_mod.pd, "read_sql", _read_sql)
    monkeypatch.setattr(sync_mod.db_io, "upsert", lambda *a, **k: 0)
    monkeypatch.setattr(sync_mod, "_refuse_self_sync", lambda s, t: None)
    monkeypatch.setattr(sync_mod, "_pin_config_hash", lambda t, c: None)

    import contextlib

    class _Report:
        run_id = "t"
        rows_written = 0
        notes = None

    @contextlib.contextmanager
    def _run_job(engine, job, params):
        yield _Report()

    monkeypatch.setattr(sync_mod, "run_job", _run_job)
    return seen


def test_incremental_passes_the_bounds_through(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setattr(
        sync_mod,
        "_incremental_bounds",
        lambda t, c, **k: {k2: date(2026, 8, 18) for k2 in BOUND_KEYS},
    )
    sync_mod.run_sync(source=_Engine(), target=_Engine(), config_hash=CONFIG, incremental=True)
    assert seen and all(p["bars_from"] == date(2026, 8, 18) for p in seen)


def test_the_default_is_a_full_copy(monkeypatch):
    """`cscan sync` means "copy the serving subset". It is the command you
    reach for after a rebuild or a reflash, so it must never quietly ship a
    window -- and must not even consult the target's watermark."""
    seen = _capture(monkeypatch)
    monkeypatch.setattr(
        sync_mod,
        "_incremental_bounds",
        lambda t, c, **k: pytest.fail("a full sync must not consult the target"),
    )
    sync_mod.run_sync(source=_Engine(), target=_Engine(), config_hash=CONFIG)
    assert seen and all(p[k] is None for p in seen for k in BOUND_KEYS)


def test_nightly_opts_into_incremental():
    """The one caller that cannot afford a full pass. If this regresses the
    nightly silently grows by ~114 minutes and still reports ok."""
    import inspect

    from capitalscan.jobs import cli

    src = inspect.getsource(cli.nightly)
    assert "run_sync(incremental=True)" in src
