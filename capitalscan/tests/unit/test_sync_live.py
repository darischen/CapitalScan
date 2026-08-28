"""The per-tick serving push (ADR 153).

`run_sync` is the nightly cut. This is the other one: the poller's own
footprint, copied after every tick so the deployed site shows what the
workstation shows rather than what it showed last night.

The properties worth pinning are structural, so they need no database:
which tables ship, in what order, scoped to what, and -- the one whose
absence is silent -- that the nightly sweep reaches serving as well as
research.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from capitalscan.jobs import sync

CHASH = "a38d3ca6b58295e8"
D = date(2026, 8, 25)
RUN_ID = "poll_20260825T063000_abc123"
ROOT = Path(__file__).resolve().parents[3] / "capitalscan"

TABLES = sync._live_tables(CHASH, D, RUN_ID, sync.LiveWatermark())
NAMES = [t.name for t in TABLES]


def _source(name: str) -> str:
    return (ROOT / "jobs" / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# What ships
# ---------------------------------------------------------------------------


def test_it_ships_exactly_the_pollers_own_footprint():
    """Five tables the poller writes, plus the `runs` row events point at.

    Anything else is either unchanged intraday (`tickers`, `universe`) or
    the nightly's business (`bars`, `indicators`, `cell_stats`). Shipping
    those per tick would re-read millions of rows every five minutes to
    move a few dozen.
    """
    assert set(NAMES) == {
        "poller_sessions",
        "runs",
        "events",
        "signal_reports",
        "bars_live",
        "quotes_live",
    }


def test_the_heartbeat_ships_first():
    """`poller_sessions` before any signal row.

    It is what separates "quiet session" from "poller died at 07:15", and a
    reader who sees zero signals is exactly the reader who needs it. If the
    push fails partway, the heartbeat is the row that should already have
    landed.
    """
    assert NAMES[0] == "poller_sessions"


def test_runs_precedes_events():
    """`events.run_id` is a foreign key. Order is a constraint here, not a
    preference -- the same reason `_tables()` gives for the nightly."""
    assert NAMES.index("runs") < NAMES.index("events")


def test_it_ships_only_the_touch_grain():
    """`next_open` rows belong to the backtest and are written nightly. A
    live push carrying them would ship half-built rows for a grain the
    poller does not own."""
    events = next(t for t in TABLES if t.name == "events")
    assert "entry_kind = 'touch'" in events.sql


def test_every_query_is_scoped_to_one_session():
    """No live query may reach beyond today.

    A query without a date bound would re-ship the whole table every five
    minutes and, worse, could resurrect rows a previous night's sweep
    deliberately deleted from serving.
    """
    for table in TABLES:
        if table.name == "runs":
            assert ":run_id" in table.sql
        else:
            assert ":d" in table.sql, f"{table.name} is not scoped to the session date"


def test_the_config_hash_scopes_the_event_query():
    """22 hashes live in research and the serving store reads exactly one
    (ADR 115). Shipping another generation is what filled Neon."""
    events = next(t for t in TABLES if t.name == "events")
    assert ":chash" in events.sql


def test_the_nightly_cut_still_excludes_the_live_session():
    """ADR 153 does not widen `_tables()`.

    The nightly exclusion is right for the reason it documents: a
    once-a-day copy of a five-minute table is a frozen price wearing a live
    label. What changed is that a *second*, per-tick path now exists. If
    these ever ship nightly as well, the deployed candle freezes at
    whenever the nightly ran.
    """
    nightly = [t.name for t in sync._tables(date(2000, 1, 1), CHASH)]
    assert "bars_live" not in nightly
    assert "quotes_live" not in nightly


# ---------------------------------------------------------------------------
# The watermark
# ---------------------------------------------------------------------------


def test_the_watermark_bounds_the_append_only_tables():
    """`events` and `signal_reports` only gain rows, so a tick ships what it
    produced rather than the session so far."""
    for name, param in (("events", ":since_event"), ("signal_reports", ":since_report")):
        table = next(t for t in TABLES if t.name == name)
        assert param in table.sql, f"{name} re-ships the whole session every tick"


def test_the_overwritten_table_is_not_watermarked():
    """`bars_live` is keyed `(ticker, session_date)`, so a tick *overwrites*
    each ticker's row -- that is the point of it.

    A watermark would ship each ticker once and never again, so the deployed
    candle would freeze at the first tick of the day while every other
    signal kept moving. That is worse than not shipping it, because it looks
    live.
    """
    table = next(t for t in TABLES if t.name == "bars_live")
    assert "since_" not in table.sql


def test_the_quote_log_is_watermarked_on_its_clock():
    """`quotes_live` is keyed `(ticker, ts)` and therefore **append-only**,
    unlike its neighbour.

    Checked against `pg_constraint` rather than assumed from `bars_live`:
    the first version of this file assumed the two behaved alike, which
    would have re-shipped every quote written so far on every tick, growing
    quadratically across a 78-tick session.

    `ts` rather than `id`, because the table has no id column.
    """
    table = next(t for t in TABLES if t.name == "quotes_live")
    assert ":since_quote" in table.sql
    assert "ts >" in table.sql


def test_the_watermark_starts_below_every_row():
    """`id` is a bigint sequence, so 0 means "ship everything you find"."""
    assert sync.LiveWatermark() == sync.LiveWatermark(0, 0)


def test_the_watermark_is_frozen():
    """The caller holds it across ticks. A mutable one shared between two
    sessions in one process would skip the second day's early rows."""
    with pytest.raises(Exception):
        sync.LiveWatermark().event_id = 5


# ---------------------------------------------------------------------------
# Convergence: the part whose absence is silent
# ---------------------------------------------------------------------------


def test_the_nightly_sweeps_serving_as_well_as_research():
    """ADR 150 deletes this session's poller rows; ADR 153 makes those rows
    externally visible before that happens.

    `run_sync` never deletes, so without a second sweep the serving store
    keeps every row the nightly rejected -- the no-bar rows, the
    wrong-primary-type rows -- accumulating a few a day forever. Nothing
    raises, and the drift is confined to the rows research judged
    unreliable, which is the worst population to be silently wrong about.
    """
    text = _source("cli.py")
    calls = re.findall(r"_sweep_provisional_poll_rows\(\s*([^,]+),", text)
    assert any("engine" == c.strip() for c in calls), "the research sweep is gone"
    assert any("serving_engine" in c for c in calls), (
        "the nightly sweeps research but not serving. Every poller row the "
        "live sync pushed today stays on the deployed site permanently."
    )


def test_the_serving_sweep_follows_a_successful_full_sync():
    """Write the authoritative rows, *then* drop the provisional ones.

    **Reversed on 2026-08-28. This test previously asserted the opposite**,
    with the reasoning "delete the provisional rows, then write the
    authoritative ones -- reversed, a sync that failed halfway would leave
    serving holding provisional rows it had just been told to drop."

    That is right about the steady state and wrong about the failure. On
    2026-08-26 the sweep removed the Pi's poller rows for the session, the
    sync then died 53.8 minutes in, and serving held **zero** events for
    that day against research's 670. Not stale -- empty. The site showed
    nothing.

    The trade is now explicit: superseded provisional rows may survive one
    night. They are stale rather than absent, ADR 140 says they were never
    authoritative, and the next successful sync clears them. Absent rows had
    no such recovery.

    The `sync_ok` guard matters as much as the order -- sweeping after a
    *failed* sync is the original bug with extra steps. See
    `test_nightly_sweep_ordering.py`, which pins both.
    """
    text = _source("cli.py")
    # Matched across a line break: `ruff format` wraps this call, and an
    # assertion that breaks on reformatting tests the formatter rather than
    # the ordering.
    sweep = re.search(
        r"_sweep_provisional_poll_rows\(\s*sync_job\.serving_engine\(\)",
        text,
    )
    assert sweep is not None, "the serving sweep is gone"
    # Also matched loosely. This asserted the literal
    # `sync_report = sync_job.run_sync()` and broke on 2026-08-26 when
    # nightly started passing `incremental=True` -- the ordering it exists
    # to protect had not changed at all. The argument list is not this
    # test's business.
    full_sync = re.search(r"sync_report = sync_job\.run_sync\(", text)
    assert full_sync is not None, "nightly no longer syncs"
    assert full_sync.start() < sweep.start(), (
        "the serving sweep runs before run_sync again; a sync that fails "
        "mid-way then deletes a session from serving with nothing to "
        "replace it (2026-08-26)"
    )


# ---------------------------------------------------------------------------
# The poll must survive a serving store that is not there
# ---------------------------------------------------------------------------


def test_the_push_never_fails_the_poll():
    """A sleeping Pi, a moved DHCP lease, or an unset
    `DATABASE_URL_SERVING` degrade to "the site is behind", never to "the
    session lost a tick". Research is the source of truth and has already
    been written by the time the push runs.
    """
    body = _source("poll.py")
    body = body[body.index("def _push_live") :]
    body = body[: body.index("\ndef ")]
    assert "except Exception" in body, "_push_live lets a serving failure reach the tick loop"


def test_a_failed_push_leaves_the_watermark_alone():
    """So the next tick re-ships what this one could not, rather than
    leaving a permanent hole on serving."""
    from capitalscan.jobs import poll
    from capitalscan.jobs import sync as sync_mod

    class _Report:
        notes = None

    mark = sync.LiveWatermark(41, 7)

    def _boom(*args, **kwargs):
        raise RuntimeError("pi is asleep")

    original = sync_mod.run_live_sync
    sync_mod.run_live_sync = _boom
    try:
        out = poll._push_live(None, CHASH, D, RUN_ID, mark, _Report())
    finally:
        sync_mod.run_live_sync = original

    assert out == mark


# ---------------------------------------------------------------------------
# The heartbeat
# ---------------------------------------------------------------------------


def test_the_session_row_is_written_inside_the_tick_loop():
    """It was one write on exit, which made it a record of a session.

    Written per tick it is also a heartbeat, and `ended_at` then means "last
    tick" rather than "finished": the row is complete at every moment and
    simply stops advancing when the poller stops.
    """
    text = _source("poll.py")
    loop = text[text.index("        tick = 0\n        while True:") :]
    loop = loop[: loop.index("\n        _write_session_row(")]
    assert "_write_session_row(" in loop, (
        "the session row is only written at exit, so a serving reader cannot "
        "tell a quiet session from a dead poller."
    )


def test_the_heartbeat_is_written_before_the_push():
    """A push that fails should still leave a dated tick on serving, so the
    site can say how far behind it is rather than going silent."""
    text = _source("poll.py")
    loop = text[text.index("        tick = 0\n        while True:") :]
    loop = loop[: loop.index("\n        _write_session_row(")]
    assert loop.index("_write_session_row(") < loop.index("_push_live(")
