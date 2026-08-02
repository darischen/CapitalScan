"""A hard reject only blocks validation while its bar is still missing.

`bar_rejects` is append-only — it is an audit trail, not a worklist
(invariant 6). So a bar rejected by one run stays recorded even after a later
run fetches it correctly and upserts it. `run_validate` counted every row
ever written, which meant a single transient rejection made validation
permanently dirty with no way back.

Observed against the real database: 10 of 12 outstanding hard rejects had
correct bars on file. A batch fetched across a split boundary shows an
artificial discontinuity (ANET 2024-10-07 read as -75%), the bar is dropped,
and a later batch fetched wholly after the split lands it smoothly — 98.14,
between neighbours at 98.99 and 100.06. The rejection was real when it
happened and is meaningless now.

The remaining two were pre-2009 bars deleted deliberately by the window trim,
which is why `ingest_start` bounds the check.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from capitalscan.jobs.ingest import unresolved_rejects

INGEST_START = date(2009, 1, 1)


def _rejects(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ticker": t, "d": date.fromisoformat(d), "severity": s} for t, d, s in rows],
        columns=["ticker", "d", "severity"],
    )


def _bars(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ticker": t, "d": date.fromisoformat(d)} for t, d in rows],
        columns=["ticker", "d"],
    )


def test_a_reject_whose_bar_was_refetched_is_resolved():
    """The ANET case: rejected once, landed correctly by a later run."""
    rejects = _rejects([("ANET", "2024-10-07", "reject")])
    bars = _bars([("ANET", "2024-10-07")])

    assert unresolved_rejects(rejects, bars, INGEST_START).empty


def test_a_reject_whose_bar_is_still_absent_is_unresolved():
    """A rejection that genuinely cost a bar must still block."""
    rejects = _rejects([("AAA", "2024-10-07", "reject")])
    bars = _bars([("AAA", "2024-10-06"), ("AAA", "2024-10-08")])

    out = unresolved_rejects(rejects, bars, INGEST_START)

    assert list(out["ticker"]) == ["AAA"]


def test_rejects_before_ingest_start_are_not_counted():
    """Their bars are absent because the window trim deleted them on purpose."""
    rejects = _rejects([("AET", "2000-02-08", "reject")])
    bars = _bars([])

    assert unresolved_rejects(rejects, bars, INGEST_START).empty


def test_flags_never_block_regardless_of_the_bar():
    """Only 'reject' severity drops a row; a flag keeps it (DESIGN 2.3)."""
    rejects = _rejects([("AAA", "2024-10-07", "flag")])
    bars = _bars([])

    assert unresolved_rejects(rejects, bars, INGEST_START).empty


def test_one_ticker_does_not_resolve_another_tickers_reject():
    """Matching is on (ticker, date), not date alone."""
    rejects = _rejects([("AAA", "2024-10-07", "reject")])
    bars = _bars([("BBB", "2024-10-07")])

    out = unresolved_rejects(rejects, bars, INGEST_START)

    assert list(out["ticker"]) == ["AAA"]


def test_empty_inputs_are_clean():
    assert unresolved_rejects(_rejects([]), _bars([]), INGEST_START).empty
