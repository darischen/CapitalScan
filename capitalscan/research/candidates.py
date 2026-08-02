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

import bisect
import hashlib
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


# ---------------------------------------------------------------------------
# tag_clusters
# ---------------------------------------------------------------------------


def _deterministic_cluster_id(*parts: object) -> int:
    """A stable bigint from `parts` — reruns must produce the same
    `cluster_id` for the same (ticker, side, cluster head), or a downstream
    join across two runs would silently drift (ADR 060).

    Same construction as `jobs/compute.py:_deterministic_id` (Ruling C5):
    sha256 the pipe-joined parts, take the leading 60 bits. Not shared by
    import — `compute.py` is a `jobs/` module with IO elsewhere in it, and
    this one has to stay importable and callable with zero side effects for
    the spawn worker rule (CLAUDE.md "Platform"); duplicating six lines is
    cheaper than adding a cross-package dependency for them. Report to the
    controller if a shared home is wanted later.
    """
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:15], 16)  # 60 bits, comfortably inside bigint


def _trading_bars_between(dates: list[date], start: date, end: date) -> int:
    """Count of entries in the sorted, deduplicated `dates` that fall
    strictly after `start` and up to and including `end`.

    This is a bar count, not a calendar-day count (Ruling C5): the gap that
    matters for clustering is "how many forward bars would a position from
    `start` have been open," which is exactly what `ExitParams.max_hold_days`
    measures, and a weekend or holiday between two signal dates does not
    open or close a position. `bisect` gives an O(log n) lookup per event
    rather than an O(n) scan of the ticker's whole date list; a backtest
    with a mega-cap universe over a decade-plus window means this runs for
    every event, so the asymptotics are the difference between a lookup and
    a linear scan on the read-window's mega-cap ticker history.

    `end <= start` (the head event itself, or a mis-ordered pair) returns 0
    rather than a negative count.
    """
    if end <= start:
        return 0
    lo = bisect.bisect_right(dates, start)
    hi = bisect.bisect_right(dates, end)
    return hi - lo


def _as_date(value: object) -> date:
    """Same defensive Timestamp -> date coercion `apply_eligibility` and
    `debounce` already do above — `signal_date` is a plain `date` when it
    comes straight from `scan_candidates` (`core.signals._bar_date` returns
    one), but nothing enforces that a caller building a candidates frame by
    hand used the same type, and comparing a `date` to a `pandas.Timestamp`
    either raises or silently returns a wrong answer depending on pandas
    version.
    """
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value


def tag_clusters(
    candidates: pd.DataFrame,
    max_hold_days: int,
    trading_dates: dict[str, list[date]],
) -> pd.DataFrame:
    """DESIGN §5.3, ADR 056. Overlapping events on one ticker are tagged,
    never suppressed, so a later question like "does the second touch in a
    run outperform the first?" stays answerable. Primary statistics filter
    `is_cluster_head = true` for a clean, non-overlapping sample; secondary
    statistics use every row.

    Adds four columns without mutating `candidates` (CLAUDE.md "Never
    mutate in place, always return a new object"): `cluster_id` (a
    deterministic hash of `(ticker, side, head_date)`, not a row counter —
    ADR 060 requires reruns to reproduce identical ids), `seq_in_cluster`
    (1-based position within the cluster), `is_cluster_head` (true only for
    seq 1), and `days_since_head` (trading bars since the cluster's first
    event, 0 for the head itself).

    Clusters key on `(ticker, side)`, matching the shipped v1 tagger at
    `jobs/compute.py:_tag_clusters` (compute.py:552-556) — a long cluster
    and a short cluster on one ticker are different positions and must
    never merge, even on identical dates. Two different tickers never
    share a cluster regardless of side or date, because `ticker` is part
    of both the grouping key and the `cluster_id` hash input.

    **Divergence from `jobs/compute.py:_tag_clusters` (Ruling C5, session 9
    task 4).** That function's gap test and its `days_since_head` are both
    calendar days (`compute.py:559,569`, `(event["signal_date"] -
    prev["last_date"]).days`). This function measures both in trading bars
    instead, because `ExitParams.max_hold_days` counts forward *bars*, and
    a calendar-day gap test silently shrinks across a weekend — 5 calendar
    days spanning a weekend is only 3 trading bars, so the v1 tagger would
    close a cluster the backtest's own exit horizon says is still open. The
    two jobs will therefore disagree on cluster boundaries for the same
    events until a later task gives cluster tagging a single writer at the
    database layer (the controller has already ruled this function owns
    the columns for the backtest; `compute.py` is intentionally left
    unchanged here).

    `trading_dates` maps each ticker to its sorted, deduplicated trading
    dates — the set the gap is measured against. It is a parameter, not a
    calendar table this function looks up itself, because `core/`'s "no
    IO" rule (invariant 1) extends here by convention: this module owns IO
    for the rest of `research/`, but this one function is kept pure and
    directly unit-testable with plain dicts, and the caller already has
    the bars frame in hand (build it once with e.g.
    `bars.groupby("ticker")["ts"].apply(lambda s: sorted(s.dt.date.unique()))`
    and pass the same dict across a whole run rather than re-deriving it
    per call).
    """
    if candidates.empty:
        out = candidates.copy()
        out["cluster_id"] = pd.Series(dtype="int64")
        out["seq_in_cluster"] = pd.Series(dtype="int64")
        out["is_cluster_head"] = pd.Series(dtype="bool")
        out["days_since_head"] = pd.Series(dtype="int64")
        return out

    # One sorted date list per ticker actually present, built once rather
    # than per event — see `_trading_bars_between`'s docstring on why the
    # per-event lookup needs to be O(log n), not O(n).
    sorted_dates: dict[str, list[date]] = {
        ticker: sorted(set(trading_dates.get(ticker, [])))
        for ticker in candidates["ticker"].unique()
    }

    working = candidates.copy()
    working["_signal_date_norm"] = working["signal_date"].map(_as_date)

    cluster_id_col: list[int] = [0] * len(working)
    seq_col: list[int] = [0] * len(working)
    head_col: list[bool] = [False] * len(working)
    days_col: list[int] = [0] * len(working)

    # (ticker, side) keying, matching compute.py:552-556 (Ruling C5) — never
    # ticker alone, or a long cluster and a short cluster on one ticker
    # would merge into one position.
    for (ticker, side), group in working.groupby(["ticker", "side"], sort=False):
        dates = sorted_dates.get(ticker, [])
        ordered = group.sort_values("_signal_date_norm")

        head_date: date | None = None
        seq = 0
        for idx, signal_date in ordered["_signal_date_norm"].items():
            gap = None if head_date is None else _trading_bars_between(dates, head_date, signal_date)
            if head_date is None or gap > max_hold_days:
                head_date = signal_date
                seq = 1
            else:
                seq += 1

            pos = working.index.get_loc(idx)
            cluster_id_col[pos] = _deterministic_cluster_id(ticker, side, head_date)
            seq_col[pos] = seq
            head_col[pos] = seq == 1
            days_col[pos] = _trading_bars_between(dates, head_date, signal_date)

    out = candidates.copy()
    out["cluster_id"] = cluster_id_col
    out["seq_in_cluster"] = seq_col
    out["is_cluster_head"] = head_col
    out["days_since_head"] = days_col
    return out
