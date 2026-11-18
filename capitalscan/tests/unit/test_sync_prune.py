"""`sync.prune_serving`: rows research deleted leave serving too.

**The finding, 2026-11-17.** `cscan sync` upserted and never deleted, so
serving kept 13,197 daily bars on 56 tickers research had rejected and 878
superseded slot `events` research had dropped. Both were cleaned by hand.

**The constraint that shapes every test here.** Serving holds rows research
does not on purpose: the poller's `touch` events and the pre-floor
predictions stay serving-only (DECISIONS Open items, owner 2026-11-17). So
the prune deletes by keys research reads out of its own snapshot, never by
"whatever research lacks".

No database: the snapshot, the source and the target are all stubbed.
"""

from __future__ import annotations

import contextlib
import inspect
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from capitalscan.jobs import db_io
from capitalscan.jobs import sync as sync_mod
from capitalscan.tests.conftest import DEFAULT_CONFIG_HASH

CONFIG = DEFAULT_CONFIG_HASH
CUTOFF = date(1996, 8, 27)
LINE = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
TS_A = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
TS_B = datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)


class _Snapshot:
    """Research's read transaction. Answers by which prune query it sees."""

    def __init__(self, rejects=(), slots=()):
        self.rejects = list(rejects)
        self.slots = list(slots)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if sql == sync_mod.PRUNE_REJECT_KEYS_SQL:
            return _Rows(self.rejects)
        if sql == sync_mod.PRUNE_SLOT_KEYS_SQL:
            return _Rows(self.slots)
        raise AssertionError(f"unexpected research read: {sql[:60]}")


class _TargetConn:
    def __init__(self, log):
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, statement, params=None):
        self._log.append((str(statement), dict(params or {})))
        return _Rows(rowcount=len((params or {}).get("tickers", [])))


class _Target:
    def __init__(self):
        self.deletes: list[tuple[str, dict]] = []
        self.transactions = 0

    def begin(self):
        self.transactions += 1
        return _TargetConn(self.deletes)


def _prune(monkeypatch, snapshot, *, incremental=True, line=LINE):
    monkeypatch.setattr(sync_mod, "_events_modified_since", lambda s: line)
    target = _Target()
    out = sync_mod.prune_serving(
        snapshot, object(), target, config_hash=CONFIG, cutoff=CUTOFF, incremental=incremental
    )
    return out, target


# ---------------------------------------------------------------------------
# Bars and indicators
# ---------------------------------------------------------------------------


def test_a_rejected_bar_research_lacks_is_deleted_from_both_chart_tables(monkeypatch):
    snap = _Snapshot(rejects=[("ACME", TS_A, False)])
    out, target = _prune(monkeypatch, snap)
    sqls = [s for s, _ in target.deletes]
    assert sqls == [sync_mod.PRUNE_CHART_SQL["indicators"], sync_mod.PRUNE_CHART_SQL["bars"]]
    for _, params in target.deletes:
        assert params == {"tickers": ["ACME"], "ts": [TS_A]}
    assert out == {"indicators": 1, "bars": 1, "events": 0}


def test_an_indicator_research_still_holds_is_kept(monkeypatch):
    """Indicators at those keys only if research has none. Research keeping an
    indicator row where it dropped the bar is research's call to make."""
    snap = _Snapshot(rejects=[("ACME", TS_A, True), ("ACME", TS_B, False)])
    _, target = _prune(monkeypatch, snap)
    by_sql = {s: p for s, p in target.deletes}
    assert by_sql[sync_mod.PRUNE_CHART_SQL["bars"]]["ts"] == [TS_A, TS_B]
    assert by_sql[sync_mod.PRUNE_CHART_SQL["indicators"]]["ts"] == [TS_B]


def test_incremental_reads_rejects_since_the_last_sync(monkeypatch):
    snap = _Snapshot()
    _prune(monkeypatch, snap, incremental=True)
    reject_params = next(p for s, p in snap.calls if s == sync_mod.PRUNE_REJECT_KEYS_SQL)
    assert reject_params == {"cutoff": CUTOFF, "since": LINE}


def test_a_full_pass_reads_every_reject(monkeypatch):
    """`cscan sync` is the command after a rebuild or a reflash, so it
    catches every rejected bar, not only the recent ones."""
    snap = _Snapshot()
    _prune(monkeypatch, snap, incremental=False)
    reject_params = next(p for s, p in snap.calls if s == sync_mod.PRUNE_REJECT_KEYS_SQL)
    assert reject_params["since"] is None


def test_the_reject_read_names_only_hard_rejects_research_has_no_bar_for():
    sql = sync_mod.PRUNE_REJECT_KEYS_SQL
    assert "r.severity = 'reject'" in sql
    assert "NOT EXISTS (SELECT 1 FROM bars b" in sql
    assert "b.interval = '1d'" in sql
    assert "r.ts >= :cutoff" in sql


def test_the_chart_deletes_touch_daily_rows_at_named_keys_only():
    for table, sql in sync_mod.PRUNE_CHART_SQL.items():
        assert sql.startswith(f"DELETE FROM {table} x USING (")
        assert "x.interval = '1d'" in sql
        assert "x.ticker = k.ticker AND x.ts = k.ts" in sql
    assert "bars_live" not in "".join(sync_mod.PRUNE_CHART_SQL.values())


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


SLOTS = [
    ("HWM", date(2026, 10, 8), "long", "next_open", "bb_lower_touch"),
    ("HWM", date(2026, 10, 8), "long", "touch", "bb_lower_touch"),
]


def test_the_research_slots_reach_the_serving_delete(monkeypatch):
    snap = _Snapshot(slots=SLOTS)
    out, target = _prune(monkeypatch, snap)
    assert target.transactions == 1
    (sql, params), *_ = target.deletes
    assert sql == sync_mod.PRUNE_EVENTS_SQL
    assert params == {
        "config_hash": CONFIG,
        "tickers": ["HWM", "HWM"],
        "dates": [date(2026, 10, 8)] * 2,
        "sides": ["long", "long"],
        "kinds": ["next_open", "touch"],
        "types": ["bb_lower_touch", "bb_lower_touch"],
    }
    assert out["events"] == 2


def test_the_slot_window_is_the_last_sync_line(monkeypatch):
    snap = _Snapshot()
    _prune(monkeypatch, snap)
    slot_params = next(p for s, p in snap.calls if s == sync_mod.PRUNE_SLOT_KEYS_SQL)
    assert slot_params == {"config_hash": CONFIG, "since": LINE, "cutoff": CUTOFF}


def test_with_no_prior_sync_events_are_not_pruned(monkeypatch):
    """No line means the key set would be the whole generation. Skipped,
    not widened: the events prune is never bigger than the sync's own
    `modified_at` arm."""
    snap = _Snapshot(rejects=[("ACME", TS_A, False)])
    out, target = _prune(monkeypatch, snap, line=None)
    assert not any(s == sync_mod.PRUNE_SLOT_KEYS_SQL for s, _ in snap.calls)
    assert sync_mod.PRUNE_EVENTS_SQL not in [s for s, _ in target.deletes]
    assert out["events"] == 0 and out["bars"] == 1


def test_the_slot_read_returns_every_type_research_holds_in_the_slot():
    """A slot can hold two types on research when a referenced superseded
    row stayed. Reading only the rewritten row would delete the other one
    from serving although research keeps it."""
    sql = sync_mod.PRUNE_SLOT_KEYS_SQL
    assert "modified_at >= CAST(:since AS timestamptz)" in sql
    assert "SELECT e.ticker, e.signal_date, e.side, e.entry_kind, e.signal_type" in sql
    assert "JOIN touched t" in sql
    assert sql.count("config_hash = :config_hash") == 2


def test_the_events_delete_keeps_what_research_keeps():
    sql = sync_mod.PRUNE_EVENTS_SQL
    assert sql.count("e.config_hash = :config_hash") == 1
    assert "k.signal_type = e.signal_type" in sql
    assert "AND NOT EXISTS (SELECT 1 FROM k WHERE" in sql


@pytest.mark.parametrize("table", db_io.EVENT_REFERENCING_TABLES)
def test_a_referenced_event_is_never_pruned(table):
    """ADR 207's rule, the same tables `DROP_SUPERSEDED_SLOT_SQL` guards."""
    assert f"NOT EXISTS (SELECT 1 FROM {table} ref WHERE ref.event_id = e.id)" in (
        sync_mod.PRUNE_EVENTS_SQL
    )


def test_a_poller_event_is_never_pruned():
    """Live detections stay serving-only (owner, 2026-11-17)."""
    sql = sync_mod.PRUNE_EVENTS_SQL
    assert "split_part(e.run_id, '_', 1) <> 'poll'" in sql
    assert "r.run_id = e.run_id AND r.job = 'poll'" in sql


# ---------------------------------------------------------------------------
# What it never does
# ---------------------------------------------------------------------------


def test_nothing_to_prune_opens_no_serving_transaction(monkeypatch):
    _, target = _prune(monkeypatch, _Snapshot())
    assert target.transactions == 0


def test_tickers_are_never_pruned():
    """Research retires a ticker in place (ADR 207) and leaves no record of a
    hand deletion, so there is no key to bound a tickers prune by."""
    src = inspect.getsource(sync_mod)
    assert "DELETE FROM tickers" not in src
    assert set(sync_mod.PRUNE_KEYS) == {"bars", "indicators", "events"}


def test_every_prune_value_is_a_bound_parameter():
    for sql in (
        sync_mod.PRUNE_REJECT_KEYS_SQL,
        sync_mod.PRUNE_SLOT_KEYS_SQL,
        sync_mod.PRUNE_EVENTS_SQL,
        *sync_mod.PRUNE_CHART_SQL.values(),
    ):
        assert "%" not in sql and "{" not in sql


# ---------------------------------------------------------------------------
# run_sync wiring
# ---------------------------------------------------------------------------


class _SnapConn:
    def execution_options(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, statement, params=None):
        return _Rows()


class _Source:
    def connect(self):
        return _SnapConn()


def _wire(monkeypatch) -> tuple[list[str], dict]:
    order: list[str] = []
    seen: dict = {}

    def _read_sql(statement, con, params=None, chunksize=None):
        return iter([pd.DataFrame()])

    def _copy_upsert(target, name, frame, key, **kwargs):
        order.append(name)
        return 0

    def _prune_serving(snapshot, source, target, **kwargs):
        order.append("PRUNE")
        seen.update(kwargs)
        return {"indicators": 1, "bars": 2, "events": 3}

    class _Report:
        run_id = "t"
        rows_written = 0
        notes = None

    report = _Report()

    @contextlib.contextmanager
    def _run_job(engine, job, params):
        yield report

    monkeypatch.setattr(sync_mod.pd, "read_sql", _read_sql)
    monkeypatch.setattr(sync_mod.db_io, "copy_upsert", _copy_upsert)
    monkeypatch.setattr(sync_mod, "_prepare_chunk", lambda chunk, target, table: chunk)
    monkeypatch.setattr(sync_mod, "prune_serving", _prune_serving)
    monkeypatch.setattr(sync_mod, "_refuse_self_sync", lambda s, t: None)
    monkeypatch.setattr(sync_mod, "_reset_sequences", lambda t, serving: None)
    monkeypatch.setattr(sync_mod, "_pin_config_hash", lambda t, c: None)
    monkeypatch.setattr(sync_mod, "run_job", _run_job)
    seen["report"] = report
    return order, seen


def test_the_prune_runs_after_every_table_and_before_the_pin(monkeypatch):
    order, _ = _wire(monkeypatch)
    sync_mod.run_sync(source=_Source(), target=object(), config_hash=CONFIG)
    assert order[-2:] == ["PRUNE", "serving_config"]
    for name in ("bars", "indicators", "events"):
        assert order.index(name) < order.index("PRUNE")


def test_the_counts_reach_the_report_and_the_runs_row(monkeypatch):
    _, seen = _wire(monkeypatch)
    out = sync_mod.run_sync(source=_Source(), target=object(), config_hash=CONFIG)
    assert out.pruned == {"indicators": 1, "bars": 2, "events": 3}
    assert out.total == 0, "pruned rows are not shipped rows"
    assert seen["report"].notes == "pruned indicators=1, bars=2, events=3"


def test_the_prune_sees_the_sync_mode(monkeypatch):
    _, seen = _wire(monkeypatch)
    sync_mod.run_sync(source=_Source(), target=object(), config_hash=CONFIG, incremental=False)
    assert seen["incremental"] is False and seen["config_hash"] == CONFIG


def test_no_prune_skips_it(monkeypatch):
    order, seen = _wire(monkeypatch)
    out = sync_mod.run_sync(source=_Source(), target=object(), config_hash=CONFIG, prune=False)
    assert "PRUNE" not in order and order[-1] == "serving_config"
    assert out.pruned == {}
    assert seen["report"].notes is None


def test_the_cli_exposes_the_flag():
    from capitalscan.jobs import cli

    src = inspect.getsource(cli.sync)
    assert '"--prune/--no-prune"' in src
    assert "prune=prune" in src
