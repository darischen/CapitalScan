"""Candidate scan, eligibility filter, debounce — DESIGN §5.2 steps 3-5.

Orchestration, not new logic (Session 9 charter). Every hard decision here
already lives in `core/signals.py`: this module's job is to iterate bars and
indicators correctly, pair them at the right lag, and reduce the raw hit
stream down to one row per (ticker, bound, day) — the same reduction
`jobs/compute.py:run_events` already performs, factored so it is testable
without a database and so it produces reject records instead of silently
dropping rows (invariant 4).

`research/` owns IO in principle, but nothing in this module touches a
database, a socket, or a clock by default — `bars` and `indicators` arrive
as already-loaded frames, and `apply_eligibility`'s `today` bound is an
explicit, injectable argument rather than a `date.today()` read baked into
the function, precisely so a backtest run stays reproducible from its inputs
alone (ADR 060, DESIGN §5.1).

**Bar/indicator pairing is date-based, never positional** (Controller Ruling
C3, session-9 task-3). `indicators` is written only for a target window
while `bars` is read wider (`jobs/compute.py:run_indicators`, DESIGN §4.5's
read-window-vs-write-window rule), and a ticker with under 280 bars gets no
indicator rows at all. Either gap, present in one frame and not the other,
would shift a positional `iloc[i-1]` pairing by one row for every bar after
it — silently, forever. The date rule below is the shipped precedent at
`jobs/compute.py:731-738`: the latest indicator row strictly before the
bar's own date, looked up by date value, not by row position.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from capitalscan.core import signals as core_signals
from capitalscan.core.config import SignalParams, SplitParams
from capitalscan.core.signals import debounce_key
from capitalscan.core.types import Side

# The three fields `detect()` needs to evaluate any condition (compute.py's
# own required-field list, compute.py:741). A null in any of these means the
# bar cannot be evaluated at all, so the whole bar is dropped rather than
# risking a partial signal built on two good fields and one silently
# missing one (invariant 4 — never fill, never partially trust).
_REQUIRED_INDICATOR_FIELDS = ("bb_lower", "bb_upper", "k_full")

_CANDIDATE_COLUMNS = [
    "ticker",
    "signal_date",
    "signal_type",
    "signal_types_all",
    "signal_strength",
    "side",
    "touch_level",
]


def scan_candidates(
    bars: pd.DataFrame, indicators: pd.DataFrame, sp: SignalParams
) -> tuple[pd.DataFrame, list[dict]]:
    """DESIGN §5.2 step 3. Raw signal rows, one per `core.signals.detect` hit.

    Returns `(candidates, rejects)`. The brief's interfaces section pins
    `scan_candidates -> pd.DataFrame`; this widens that to a tuple because
    the null check (Step 1 of the brief) lives here, not in
    `apply_eligibility` — the candidates frame carries no indicator columns
    (only `_CANDIDATE_COLUMNS`), so a null-indicator reject can only be
    produced at the point the indicator row is actually read, which is here.
    See the task-3 report for the full reasoning; this is flagged for the
    controller to rule on rather than assumed settled.

    For each ticker, iterates bars in date order and pairs bar t with the
    latest indicator row strictly before t's date (Ruling C3). Bars with no
    prior indicator row (the earliest bar(s) in a ticker's history) are
    skipped outright — there is no t-1 to read.
    """
    rows: list[dict] = []
    rejects: list[dict] = []

    for ticker in sorted(bars["ticker"].unique()):
        ind_group = indicators.loc[indicators["ticker"] == ticker].sort_values("ts")
        if ind_group.empty:
            continue
        # Index by calendar date, not row position, so the lookup below is
        # immune to either frame having gaps the other doesn't (Ruling C3).
        ind_group = ind_group.set_index(ind_group["ts"].dt.date)

        bar_group = bars.loc[bars["ticker"] == ticker].sort_values("ts")
        # `detect()` reads the bar's date from `bar.name` when set
        # (core.signals._bar_date), falling back to the `ts` column only
        # when `bar.name is None`. Without this, `bar.name` would be the
        # DataFrame's integer position and every signal_date would silently
        # misdate to the epoch (compute.py:724-728 hits the same trap).
        bar_group = bar_group.set_index(bar_group["ts"].dt.date, drop=False)

        for _, bar in bar_group.iterrows():
            bar_date = bar["ts"].date()
            prior_dates = ind_group.index[ind_group.index < bar_date]
            if len(prior_dates) == 0:
                continue
            prior_ind = ind_group.loc[prior_dates.max()]

            missing = [f for f in _REQUIRED_INDICATOR_FIELDS if pd.isna(prior_ind.get(f))]
            if missing:
                rejects.append(
                    {
                        "ticker": ticker,
                        "signal_date": bar_date,
                        "reason": f"null_indicator:{','.join(missing)}",
                    }
                )
                continue

            for hit in core_signals.detect(bar, prior_ind, sp):
                rows.append(
                    {
                        "ticker": hit.ticker,
                        "signal_date": hit.ts,
                        "signal_type": hit.signal_type.value,
                        "signal_types_all": [t.value for t in hit.signal_types_all],
                        "signal_strength": hit.signal_strength,
                        "side": hit.side.value,
                        "touch_level": hit.touch_level,
                    }
                )

    candidates = pd.DataFrame(rows, columns=_CANDIDATE_COLUMNS)
    return candidates, rejects


def _in_trade(universe_flags: pd.DataFrame, ticker: str, signal_date: date) -> bool:
    """True when no universe evaluation exists yet (v1 fail-open, so this
    module works before `run_universe` has ever run), or when the most
    recent evaluation on or before `signal_date` says so.

    This is a second copy of `jobs/compute.py:624`'s `_in_trade` — the brief
    for this task explicitly forbids importing a private from `compute.py`
    and asks that this duplication be flagged, not silently consolidated
    (task-3 instructions). Report to the controller for a ruling on
    consolidating the two into one shared function.
    """
    rows = universe_flags.loc[
        (universe_flags["ticker"] == ticker) & (universe_flags["as_of"] <= signal_date)
    ]
    if rows.empty:
        return True
    return bool(rows.sort_values("as_of").iloc[-1]["in_trade"])


def apply_eligibility(
    candidates: pd.DataFrame,
    universe_flags: pd.DataFrame,
    sp_splits: SplitParams,
    today: date | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """DESIGN §5.2 step 4. Drops rows outside `[event_start, today]` and
    rows whose ticker is not in-trade on that date. The null-indicator check
    named in the brief's Step 1 is applied earlier, in `scan_candidates`
    (see that function's docstring) — the candidates frame here carries no
    indicator columns to check.

    `today` is an explicit argument, not read from the clock, so a backtest
    run stays a pure function of its inputs (ADR 060). Callers that want
    "as of right now" pass `date.today()` themselves; tests pass a fixed
    date.
    """
    today = today or date.today()
    event_start = date.fromisoformat(sp_splits.event_start)

    kept_rows: list[pd.Series] = []
    rejects: list[dict] = []
    for _, row in candidates.iterrows():
        signal_date = row["signal_date"]
        if isinstance(signal_date, pd.Timestamp):
            signal_date = signal_date.date()

        if not (event_start <= signal_date <= today):
            rejects.append(
                {
                    "ticker": row["ticker"],
                    "signal_date": signal_date,
                    "reason": "outside_event_window",
                }
            )
            continue
        if not _in_trade(universe_flags, row["ticker"], signal_date):
            rejects.append(
                {
                    "ticker": row["ticker"],
                    "signal_date": signal_date,
                    "reason": "not_in_trade",
                }
            )
            continue
        kept_rows.append(row)

    kept = (
        pd.DataFrame(kept_rows, columns=candidates.columns)
        if kept_rows
        else candidates.iloc[0:0].copy()
    )
    return kept.reset_index(drop=True), rejects


def debounce(candidates: pd.DataFrame) -> pd.DataFrame:
    """DESIGN §5.2 step 5. One row per `(ticker, bound, signal_date)`.

    Dedupes on `core.signals.debounce_key`, never on the row's own fields —
    that function's docstring explains why (a NaN member hashes stably but
    compares unequal, so a naive dedupe would silently keep duplicates).
    Each row is wrapped in a minimal stand-in exposing only the three
    attributes `debounce_key` reads (`ticker`, `ts`, `side`); `debounce_key`
    only ever touches those three, so a full `SignalHit` reconstruction —
    which would need `pctb` and `k_full`, not carried in `_CANDIDATE_COLUMNS`
    — is unnecessary.
    """
    if candidates.empty:
        return candidates.copy()

    seen: set[tuple] = set()
    keep = pd.Series(False, index=candidates.index)
    for idx, row in candidates.iterrows():
        signal_date = row["signal_date"]
        if isinstance(signal_date, pd.Timestamp):
            signal_date = signal_date.date()
        probe = SimpleNamespace(ticker=row["ticker"], ts=signal_date, side=Side(row["side"]))
        key = debounce_key(probe)
        if key in seen:
            continue
        seen.add(key)
        keep.loc[idx] = True

    return candidates.loc[keep].reset_index(drop=True)
