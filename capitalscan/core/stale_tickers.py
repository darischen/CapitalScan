"""Active tickers that have stopped receiving daily bars: a report, never a rule.

On 2026-11-08 ten tickers (EA, WBD, AVB and seven more) had stopped trading
between 2026-08-04 and 10-22 and stayed `is_active = true`, because nothing
in the code retires a ticker. The nightly fetched all ten every night and
the poller skipped two of them as stale every session. They were retired by
hand on 2026-11-17 (BACKLOG "Ten tickers stopped trading and stayed active").

**Retirement stays manual.** A long trading halt looks the same as a
delisting from here, and a retired ticker is never fetched again, so a
wrong automatic retirement would be silent and permanent. This module only
classifies; the nightly logs and records what it finds.

**The CIK check comes first** (ADR 207). A company keeps one symbol, and
the new one carries the history. A stale ticker that shares its CIK with an
active ticker whose bars are current is most likely a symbol change (PSKY
to SKYD, 2026-10), which wants `cscan add-ticker` history and the ADR 207
retirement rather than a plain delisting. Share classes (GOOG and GOOGL)
share a CIK too, but both stay current, so neither is reported.

**Invariant 1.** No IO, no clock. The caller supplies the rows, the
calendar and the as-of session.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from capitalscan.core.config import StaleTickerParams


@dataclass(frozen=True)
class LastBar:
    """One active ticker's newest daily bar, as the caller read it."""

    ticker: str
    cik: str | None
    last_bar: date | None  # None: the ticker has no daily bar at all


@dataclass(frozen=True)
class StaleTicker:
    ticker: str
    cik: str | None
    last_bar: date | None
    sessions_since: int | None  # None when there is no bar to count from
    cik_peers: tuple[str, ...]  # active tickers on the same CIK with current bars

    @property
    def possible_symbol_change(self) -> bool:
        return bool(self.cik_peers)


def sessions_since(last_bar: date, sessions: Sequence[date], as_of: date) -> int:
    """Sessions strictly after `last_bar`, up to and including `as_of`.

    Counted on the exchange calendar, not calendar days, so a weekend or a
    holiday never ages a ticker. A bar dated on `as_of` is zero sessions old.
    """
    ordered = sorted(sessions)
    return max(0, bisect_right(ordered, as_of) - bisect_right(ordered, last_bar))


def _norm_cik(cik: str | None) -> str | None:
    """SEC CIKs arrive zero-padded from some sources and bare from others."""
    if cik is None:
        return None
    stripped = cik.strip().lstrip("0")
    return stripped or None


def find_stale(
    rows: Sequence[LastBar],
    sessions: Sequence[date],
    as_of: date,
    params: StaleTickerParams,
) -> list[StaleTicker]:
    """Every ticker in `rows` more than `params.stale_after_sessions` old.

    `rows` are the active tickers only; the caller filters on `is_active`.
    Possible symbol changes sort first, because they need a different
    action, then the oldest last bar first.
    """
    ordered = sorted(sessions)
    limit = params.stale_after_sessions

    def _age(r: LastBar) -> int | None:
        if r.last_bar is None:
            return None
        # Inlined `sessions_since` against the pre-sorted calendar.
        return max(0, bisect_right(ordered, as_of) - bisect_right(ordered, r.last_bar))

    ages = {r.ticker: _age(r) for r in rows}

    def _is_stale(r: LastBar) -> bool:
        age = ages[r.ticker]
        return age is None or age > limit

    fresh_by_cik: dict[str, list[str]] = {}
    for r in rows:
        key = _norm_cik(r.cik)
        if key is not None and not _is_stale(r):
            fresh_by_cik.setdefault(key, []).append(r.ticker)

    out = []
    for r in rows:
        if not _is_stale(r):
            continue
        key = _norm_cik(r.cik)
        peers = tuple(sorted(fresh_by_cik.get(key, []))) if key is not None else ()
        out.append(StaleTicker(r.ticker, r.cik, r.last_bar, ages[r.ticker], peers))

    def _order(s: StaleTicker) -> tuple[bool, date, str]:
        return (not s.possible_symbol_change, s.last_bar or date.min, s.ticker)

    return sorted(out, key=_order)


def describe(s: StaleTicker) -> str:
    """One log line for one stale ticker."""
    if s.last_bar is None:
        head = f"{s.ticker}: no daily bar"
    else:
        head = f"{s.ticker}: last bar {s.last_bar.isoformat()}, {s.sessions_since} sessions ago"
    if s.possible_symbol_change:
        tail = f"CIK {s.cik} shared with {', '.join(s.cik_peers)}: possible symbol change"
    elif _norm_cik(s.cik) is None:
        tail = "no CIK"
    else:
        tail = f"CIK {s.cik}, no active CIK peer"
    return f"{head} ({tail})"


def _short(s: StaleTicker) -> str:
    last = s.last_bar.isoformat() if s.last_bar is not None else "none"
    peer = f" ->{'/'.join(s.cik_peers)}" if s.possible_symbol_change else ""
    return f"{s.ticker} {last}{peer}"


def summarize(stale: Sequence[StaleTicker], params: StaleTickerParams) -> str:
    """The `runs.notes` line: a count, then the first `notes_limit` tickers."""
    head = f"{len(stale)} stale active ticker(s) (> {params.stale_after_sessions} sessions)"
    if not stale:
        return head
    renames = sum(1 for s in stale if s.possible_symbol_change)
    shown = ", ".join(_short(s) for s in stale[: params.notes_limit])
    more = len(stale) - params.notes_limit
    suffix = f", +{more} more" if more > 0 else ""
    return f"{head}, {renames} possible symbol change(s): {shown}{suffix}"
