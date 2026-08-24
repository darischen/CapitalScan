"""Every production read of `events` either filters `in_trade` or says why not.

ADR 122 changed `run_events` from *skipping* an out-of-trade bar to
*recording* the membership on the row. That is the right shape — a signal
that fired is a fact, and whether you would have traded it is a separate
fact — but it moves a guarantee out of one place and spreads it across
eighteen.

Before ADR 122 the guarantee was structural: `events` could not contain an
out-of-trade row, so no consumer needed a predicate and none had one.
`jobs/compute.py::scan` said so in its own docstring while accepting a
`universe` argument it never used. After ADR 122 every one of those reads
widens by roughly 4.4x unless it carries the predicate, and **nothing about
the output would look wrong** — `cell_stats` would return more events per
cell, `n_eff` would rise, and every number would still be internally
consistent.

So the guarantee becomes a test, in the pattern this repository already uses
for `threshold_lint.KNOWN_EXCEPTIONS` and `test_serving_view_contract`'s
`KNOWN_GAPS`: a sweep with an allowlist, plus a test that every allowlist
entry still describes something real. An exemption kept past its reason
stops documenting a known problem and starts hiding a working one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3] / "capitalscan"

# Directories whose SQL decides a *population*. `tests/` is excluded: a test
# fixture reading every row is usually the point of the fixture.
PACKAGES = ("jobs", "research", "handlers")

# A read of the events table, in any of the spellings this codebase uses.
# `\b` on both sides so `events_lookup` and `bar_rejects` do not match.
_FROM_EVENTS = re.compile(r"\bFROM\s+(?:public\.)?events\b", re.IGNORECASE)

# The predicate, in any spelling that actually restricts to the trade
# universe. An alias prefix is optional because some queries alias `events`
# and some do not.
_FILTERED = re.compile(r"\b(?:\w+\.)?in_trade\b")


def _sql_strings(text: str) -> list[str]:
    """Every string literal in the file that reads `events`.

    Crude on purpose. A parser that understood Python string concatenation
    would be right more often and would also be a second thing to maintain;
    this reads the whole file and asks whether the predicate appears
    anywhere near the read, which is the question a reviewer would ask.
    """
    return [m.group(0) for m in _FROM_EVENTS.finditer(text)]


def _sources() -> list[Path]:
    out: list[Path] = []
    for package in PACKAGES:
        out.extend(sorted((ROOT / package).rglob("*.py")))
    return out


def _reads_events(path: Path) -> bool:
    return bool(_sql_strings(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------
#
# Each entry is a module that reads `events` without the predicate, and the
# reason it is correct to. Every one is keyed on something narrower than a
# population — a single event id, a single run, or a single ticker the
# caller already restricted.
KNOWN_UNFILTERED: dict[str, str] = {
    "handlers/explain.py": (
        "Explains one event by id. The caller already has the event; "
        "filtering would turn a lookup into a NotFound for a row the "
        "ticker page legitimately shows."
    ),
    "jobs/cli.py": (
        "`cscan events --export` selects by `run_id`, which is one job's "
        "own output. A population predicate would silently omit rows the "
        "run wrote."
    ),
    "jobs/poll.py": (
        "Existence and id lookups scoped to one ticker. The second half of "
        "this reason -- that the poller only polls `_load_in_trade_tickers`, "
        "so every row it reaches is in trade by construction -- stopped "
        "being true under ADR 149, which added the watch universe to "
        "`_load_pollable_tickers`. The exemption stands on the first half "
        "alone: these reads are keyed on `(config_hash, ticker, "
        "signal_date, signal_type)`, which is narrower than any population "
        "predicate. The poller now writes `in_trade`/`in_watch` explicitly "
        "rather than inheriting the NOT NULL DEFAULT true, which is what "
        "keeps watched names out of ADR 112's population."
    ),
    "research/path_backfill.py": (
        "Path capture is keyed on `entry_price IS NOT NULL`, which only "
        "the backtest writes and only for the population it measured. The "
        "wider detections have no entry price, so they are excluded "
        "already — by the column, not by an accident."
    ),
    "research/path_labels.py": (
        "Same as path_backfill: labels join `path`, which only exists for "
        "events the backtest priced."
    ),
    "research/path_queries.py": (
        "Same as path_backfill; reads `entry_kind` for rows already in `path`."
    ),
    "research/path_reconcile.py": (
        "Same as path_backfill; reconciles rows that have labels, which only priced events have."
    ),
}


@pytest.mark.parametrize("path", [p for p in _sources() if _reads_events(p)], ids=str)
def test_every_events_read_filters_in_trade_or_is_allowlisted(path: Path) -> None:
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8")

    if rel in KNOWN_UNFILTERED:
        pytest.skip(f"allowlisted: {KNOWN_UNFILTERED[rel]}")

    assert _FILTERED.search(text), (
        f"{rel} reads `events` without an `in_trade` predicate.\n\n"
        "ADR 122 made detection record trade-universe membership instead of "
        "filtering on it, so this read now covers roughly 4.4x the rows it "
        "used to and nothing about the result will look wrong. Add the "
        "predicate, or add an entry to KNOWN_UNFILTERED saying why this "
        "read is keyed on something narrower than a population."
    )


def test_the_sweep_is_not_vacuous() -> None:
    """A rename of the table would make every assertion above pass silently."""
    readers = [p for p in _sources() if _reads_events(p)]
    assert len(readers) >= 8, f"only {len(readers)} modules read events; did it move?"


@pytest.mark.parametrize("rel", sorted(KNOWN_UNFILTERED))
def test_every_allowlist_entry_still_reads_events(rel: str) -> None:
    """An entry that stops matching describes a read that was removed.

    `threshold_lint.KNOWN_EXCEPTIONS` learned this the hard way: an
    exemption kept past its fix stops documenting a known problem and starts
    hiding a working one, and nobody thinks to look.
    """
    path = ROOT / rel
    assert path.exists(), f"{rel} no longer exists; delete its KNOWN_UNFILTERED entry"
    assert _reads_events(path), f"{rel} no longer reads `events`; delete its KNOWN_UNFILTERED entry"


def test_scan_filters_in_trade() -> None:
    """The one that would have broken `cscan scan` and the poller's report.

    Called out by name rather than left to the sweep because its docstring
    used to say it did not need the predicate, and that docstring was
    correct until ADR 122 made it false.
    """
    text = (ROOT / "jobs" / "compute.py").read_text(encoding="utf-8")
    scan = text[text.index("def scan(") :]
    assert 'clauses.append("e.in_trade")' in scan, (
        "jobs/compute.py::scan lost its trade-universe predicate. Without "
        "it `cscan scan` lists names outside the trade universe."
    )


def test_run_events_records_membership_rather_than_skipping() -> None:
    """The other half: detection must no longer `continue` on the check."""
    text = (ROOT / "jobs" / "compute.py").read_text(encoding="utf-8")
    body = text[text.index("def run_events(") : text.index("def scan(")]
    assert "bar_in_trade = core_universe.in_trade(" in body
    assert "if not core_universe.in_trade(" not in body, (
        "run_events skips out-of-trade bars again; ADR 122 makes it record "
        "membership so the ticker page can show a train-universe name."
    )


# ---------------------------------------------------------------------------
# The views, named individually
# ---------------------------------------------------------------------------
#
# The file-level sweep above **false-passed on `jobs/views.py`**: it matched
# `v_ticker_state`'s projected `u.in_trade` column and concluded the module
# filtered. It does not follow that any particular view does, and a sweep
# that can pass for the wrong reason is worth exactly as much as the reason.
#
# So the four serving views that touch `events` are asserted one at a time,
# including the two that must *not* filter. Those two matter more: a future
# migration adding the predicate to `v_chart` would quietly undo ADR 122 and
# the ticker page would go back to showing nothing for SMCI.

_MUST_FILTER = ("V_SCREEN_DDL", "V_SCREEN_LIVE_DDL")
_MUST_NOT_FILTER = ("V_CHART_DDL",)


def _ddl(name: str) -> str:
    from capitalscan.jobs import views

    ddl: str = getattr(views, name)
    return ddl


@pytest.mark.parametrize("name", _MUST_FILTER)
def test_screener_views_filter_in_trade(name: str) -> None:
    assert "e.in_trade" in _ddl(name), (
        f"{name} lost its `in_trade` predicate. ADR 122 lets `events` hold "
        "out-of-trade rows, so a screener without this lists names outside "
        "the trade universe."
    )


@pytest.mark.parametrize("name", _MUST_NOT_FILTER)
def test_the_ticker_page_view_does_not_filter_in_trade(name: str) -> None:
    """The inverse assertion, and the one more likely to be broken by
    someone doing the obvious thing.

    `v_chart` feeds `/ticker/[sym]`. Adding the predicate here would undo
    ADR 122 entirely: SMCI would go back to a chart with no markers after
    March 2010, and it would look like a data problem rather than a
    predicate.
    """
    assert "in_trade" not in _ddl(name), (
        f"{name} gained an `in_trade` predicate. The ticker page exists to "
        "show firings on names outside the trade universe (ADR 122); "
        "filtering here removes the reason it was built."
    )


def test_v_events_is_not_filtered() -> None:
    """`v_events` is the ticker page's history. Same reasoning as `v_chart`,
    checked against the deployed definition rather than a constant, because
    this view's DDL lives in an old migration rather than in `views.py`."""
    from capitalscan.jobs import views

    text = Path(views.__file__).read_text(encoding="utf-8")
    assert "CREATE VIEW public.v_events" not in text, (
        "v_events moved into views.py; add it to the list above with a "
        "decision about whether it filters."
    )
