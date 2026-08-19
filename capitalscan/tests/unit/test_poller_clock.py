"""The poller's clock is timezone-aware, and the CSV script agrees with it.

**These two files must change together, and did not.** ADR 127.

`poll.py::_now_et` returned a naive datetime holding ET wall-clock. It is
written to `signal_reports.fired_at` and `quotes_live.ts`, both
`timestamptz` on a database running `Etc/UTC`, so Postgres read the naive
value as UTC and stored an offset it never had. Every poller timestamp
landed four hours early, five under EST.

One moment, two tables, measured 2026-08-13:

    runs.started_at          13:30:41+00   (SQL wrote it)
    signal_reports.fired_at  09:30:43+00   (the poller wrote it)

`wait_and_poll.ps1` compensated with a three-step conversion — strip the
false label, attach the true one, convert to Pacific — so its CSV was
correct while the screener, which formats in ET, showed a 09:30 market open
as **05:30**. That is how it was noticed.

The compensation means the two are coupled: fixing either alone shifts the
other by four hours in the opposite direction. The script's own comment
said so, and this file makes it a test rather than a note someone has to
find.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from capitalscan.jobs.poll import _now_et

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "wait_and_poll.ps1"


def test_now_et_is_timezone_aware() -> None:
    """The whole bug in one assertion.

    A naive return here is not a style question: it is written straight into
    a `timestamptz` column, and Postgres will read it as UTC.
    """
    now = _now_et()
    assert now.tzinfo is not None, (
        "_now_et returned a naive datetime. It is written to timestamptz "
        "columns on a UTC database, so a naive ET value is stored four "
        "hours early (five under EST). See ADR 127."
    )


def test_now_et_is_actually_eastern() -> None:
    """Aware is necessary and not sufficient — an aware UTC value would pass
    the test above and still break every session-bound comparison, which
    reads `.time()` and expects market hours in ET."""
    now = _now_et()
    expected = datetime.now(ZoneInfo("America/New_York"))
    assert now.utcoffset() == expected.utcoffset()
    # -4 in EDT, -5 in EST. Anything else is a different zone.
    offset_hours = now.utcoffset().total_seconds() / 3600  # type: ignore[union-attr]
    assert offset_hours in (-4, -5), f"offset {offset_hours} is not Eastern"


def test_now_et_agrees_with_the_wall_clock() -> None:
    """The value must be the *same instant* as `datetime.now(timezone)`, not
    an arithmetic reconstruction of it.

    The old implementation computed the offset by hand from `time.daylight`,
    which is true when a zone *observes* DST at all rather than when DST is
    *in effect* — so it was an hour wrong every winter in any DST zone.
    """
    delta = abs((_now_et() - datetime.now(ZoneInfo("America/New_York"))).total_seconds())
    assert delta < 5, f"_now_et is {delta}s from the real ET clock"


def test_the_csv_script_does_one_conversion() -> None:
    """The other half of the coupling, and it landed with the backfill.

    `_now_et` is aware and `scripts/backfill_poller_timestamps.py` shifted
    the 1,752 rows written before it, so `fired_at` is a true instant
    everywhere. One `AT TIME ZONE 'America/Los_Angeles'` is correct.

    It was a three-step chain compensating for naive ET stored in a
    `timestamptz`. Measured before the backfill: stored `09:30:39+00`, the
    chain gave `06:30` PT (right), a single conversion gave `02:30` PT.
    After it: stored `13:30:39+00`, single conversion gives `06:30` PT.

    **The two were coupled and were briefly committed apart**, which is
    what this test now pins.
    """
    text = SCRIPT.read_text(encoding="utf-8-sig")
    query = next(
        (line for line in text.splitlines() if "$query" in line and "SELECT" in line),
        None,
    )
    assert query is not None, "no $query line found in wait_and_poll.ps1"

    conversions = re.findall(r"AT TIME ZONE '([^']+)'", query)
    assert conversions == ["America/Los_Angeles"], (
        f"the CSV query does {conversions} on fired_at. `fired_at` is a "
        "true instant since ADR 127's backfill, so exactly one conversion "
        "is right; a compensating chain would now shift the CSV four hours."
    )


def test_the_script_reads_fired_at_from_the_database() -> None:
    """Guards the test above against passing vacuously if the query stops
    selecting `fired_at` at all — at which point the CSV would carry
    whatever column happened to land in that position."""
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert "s.fired_at AT TIME ZONE" in text
