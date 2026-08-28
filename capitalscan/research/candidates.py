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
from collections.abc import Sequence
from datetime import date
from types import SimpleNamespace
from typing import cast

import numpy as np
import pandas as pd

from capitalscan.core import signals as core_signals
from capitalscan.core import universe as core_universe
from capitalscan.core.config import SignalParams, SplitParams
from capitalscan.core.signals import debounce_key
from capitalscan.core.types import Side, SignalHit

# The three fields `detect()` needs to evaluate any condition (compute.py's
# own required-field list, compute.py:741). A null in any of these means the
# bar cannot be evaluated at all, so the whole bar is dropped rather than
# risking a partial signal built on two good fields and one silently
# missing one (invariant 4 — never fill, never partially trust).
_REQUIRED_INDICATOR_FIELDS = ("bb_lower", "bb_upper", "k_full")

# **The only indicator fields `detect` reads off the t-1 row.** Two are read
# directly (`bb_pctb`, `k_full`), two through `_types_fired` and the
# `level_field` branch (`bb_lower`, `bb_upper`).
#
# Carried so the row handed to `detect` can be narrowed to four columns
# instead of twenty-eight. `iloc`'s cost scales with column count: measured
# 1.89s -> 1.02s on AAPL's 5,252 bars, 1.85x, for values that were
# materialised per bar and never read.
#
# **This narrows what `detect` can reach, which is the direction the
# look-ahead guarantee wants** (TESTS.md §3.1b -- one row, never a frame).
# The hazard runs the other way: a field read but not carried arrives as
# NaN and the signal silently does not fire. `test_candidates_indicator_
# columns.py` asserts this list against `core/signals.py`'s own source so
# that cannot happen quietly.
# `k_fast` is read only when `sp.require_fast_agreement` is set, which is a
# sweepable flag -- so omitting it passed every default-config test and would
# have changed the event set only in an arm that enables it. The test caught
# it; that is the whole reason the list is asserted against the source.
_DETECT_INDICATOR_FIELDS = ("bb_lower", "bb_upper", "bb_pctb", "k_full", "k_fast")

# ADR 108's allowlist. Imported from `core/`, never restated: `jobs.compute
# .run_events` runs detection too, and the two callers drifting apart on
# which fields cross from row t is the failure the allowlist guards.
CLOSE_CONFIRMED_FIELDS = core_signals.CLOSE_CONFIRMED_FIELDS

_CANDIDATE_COLUMNS = [
    "ticker",
    "signal_date",
    "signal_type",
    "signal_types_all",
    "signal_strength",
    "side",
    "touch_level",
]


def _close_confirmed_frame(ind_group: pd.DataFrame, bar_dates: Sequence[date]) -> pd.DataFrame:
    """Bar `t`'s own close-confirmed flags, one row per requested date.

    **ADR 108 lets exactly `CLOSE_CONFIRMED_FIELDS` cross from row `t` into
    detection; everything else is read at `t-1` (invariant 3).** This
    computes that crossing once per ticker instead of once per bar.

    It replaces a per-bar expression that wrote into a freshly materialised
    Series:

        own_ind = ind_group.loc[bar_date] if bar_date in ind_group.index else None
        for field in CLOSE_CONFIRMED_FIELDS:
            value = None if own_ind is None else own_ind.get(field)
            bar[field] = False if value is None or pd.isna(value) else bool(value)

    That was 29,389 `Series.__setitem__` calls over 29,509 bars, 20.3s of a
    56.8s pass -- see `test_candidates_close_confirmed.py` for the profile.

    Three semantics are preserved exactly, and each has a test:

    - **A date with no indicator row yields `False`**, never a neighbour's
      value and never a raise. `reindex` gives NaN for a missing date, and
      the fill below turns that into `False` -- the same answer the
      `bar_date in ind_group.index` guard produced.
    - **A null through warmup is `False`**, not NaN and not a dropped row:
      "no band yet" is not "did not fire" (invariant 4).
    - **The result is a real `bool`.** `core.signals._bear_close_flag`
      re-checks the value and a NaN would read as truthy.

    A field absent from `ind_group` entirely is `False` too, matching the
    `.get` default the original relied on.
    """
    index = pd.Index(list(bar_dates), name=None)
    out = pd.DataFrame(index=index)
    for field in CLOSE_CONFIRMED_FIELDS:
        if field in ind_group.columns:
            # `~ind_group.index.duplicated()` because `reindex` refuses a
            # non-unique index. The first row for a date is the one the
            # original's `.loc` scalar lookup would have returned.
            source = ind_group.loc[~ind_group.index.duplicated(keep="first"), field]
            values = source.reindex(index)
        else:
            values = pd.Series(np.nan, index=index)
        out[field] = values.fillna(False).astype(bool)
    return out


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

        # `searchsorted` instead of scanning. The index holds `datetime.date`
        # objects, so `ind_group.index < bar_date` was an object-dtype
        # comparison across every entry, once per bar -- 27.5M comparisons
        # for a 5,248-bar ticker, and the harness runs six passes over 543
        # of them. Profiled 2026-08-21: 1.9s of an 11.3s pass, 125x slower
        # than the search below on the same data.
        #
        # Correct only because the index is sorted, two lines above. "The
        # last entry strictly before `bar_date`" is exactly what a left-side
        # search minus one returns, which is invariant 3's t-1 rule --
        # `side="left"` is what keeps a same-day row from being selected.
        # `test_candidates_prior_lookup.py` pins the equivalence against the
        # original expression as an oracle.
        ind_dates = ind_group.index.to_numpy()

        # Narrowed to what `detect` actually reads (`_DETECT_INDICATOR_FIELDS`).
        # `iloc` materialises every column of the row it returns, so the
        # width of this frame is a per-bar cost. Reindexed rather than
        # subscripted so a frame missing one of the four yields NaN --
        # which the null check below then rejects honestly -- instead of
        # raising KeyError.
        ind_narrow = ind_group.reindex(columns=list(_DETECT_INDICATOR_FIELDS))

        # Bar `t`'s own close-confirmed flags, resolved once for this
        # ticker rather than once per bar (`_close_confirmed_frame`), then
        # **attached as real columns** so the Series `iterrows` yields
        # already carries them.
        #
        # Writing them per bar instead cost 19.3s of a 37s pass, 29,389
        # calls -- one per bar -- of which 16.7s was
        # `_setitem_with_indexer_missing`: `bear_close_above_upper` is not
        # a column of this frame, so `bar[field] = ...` made pandas **grow
        # the Series index** on every row rather than replace a value.
        #
        # Positional assignment is correct because `_close_confirmed_frame`
        # is built from this frame's own dates in order. `bar_group` must
        # not be re-sorted between the two -- a misalignment here would
        # shift every flag by an unknown offset and never raise, which
        # `test_candidates_close_confirmed.py` pins.
        bar_dates_all = [ts.date() for ts in bar_group["ts"]]
        close_flags = _close_confirmed_frame(ind_group, bar_dates_all)
        bar_group = bar_group.assign(
            **{f: close_flags[f].to_numpy() for f in CLOSE_CONFIRMED_FIELDS}
        )

        for _, bar in bar_group.iterrows():
            bar_date = bar["ts"].date()
            prior_pos = int(np.searchsorted(ind_dates, bar_date, side="left")) - 1
            if prior_pos < 0:
                continue
            prior_ind = ind_narrow.iloc[prior_pos]

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

            # ADR 108: attach bar t's close-confirmed flags to the bar
            # itself. This is the one place a value from row t crosses into
            # detection, and `CLOSE_CONFIRMED_FIELDS` is the whole of what
            # may cross. `own_ind` is looked up by date, so a ticker missing
            # its own indicator row yields the `.get` default of False
            # rather than raising or inheriting a neighbour's flag.
            # The close-confirmed flags are already on `bar`, attached as
            # columns above. The guards the per-bar version applied (absent
            # date -> False, NULL through warmup -> False, real `bool` out)
            # live in `_close_confirmed_frame`.
            # `core.signals._bear_close_flag` repeats the null guard
            # regardless, deliberately -- neither layer may assume the
            # other sanitized it.

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
        # ADR 149: the backtest measures `in_trade` **and** `in_watch`, so a
        # name that graduates arrives with measured history instead of
        # starting from zero. Membership is carried on the row rather than
        # decided here (ADR 122), and nothing statistical reads the watch
        # half -- every statistical query hardcodes `in_trade`, enforced by
        # `test_events_in_trade_filter.py`.
        traded = core_universe.in_trade(universe_flags, row["ticker"], signal_date)
        watched = core_universe.in_watch(universe_flags, row["ticker"], signal_date)
        if not traded and not watched:
            rejects.append(
                {
                    "ticker": row["ticker"],
                    "signal_date": signal_date,
                    "reason": "not_in_trade",
                }
            )
            continue
        row = row.copy()
        row["in_trade"] = traded
        row["in_watch"] = watched
        kept_rows.append(row)

    # Columns from the rows, not from `candidates`: `apply_eligibility` now
    # adds `in_trade`/`in_watch`, and pinning to the input's columns would
    # silently drop them.
    kept = (
        pd.DataFrame(kept_rows)
        if kept_rows
        else candidates.iloc[0:0].assign(in_trade=False, in_watch=False)
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
        # `debounce_key` reads only `.ticker`/`.ts`/`.side`, so the probe is a
        # deliberate structural stand-in for a real `SignalHit`.
        key = debounce_key(cast("SignalHit", probe))
        if key in seen:
            continue
        seen.add(key)
        keep.at[idx] = True

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
    return cast("date", value)


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

    Raises `ValueError` if a ticker in `candidates` has no (or an empty)
    entry in `trading_dates`, or if a candidate's `signal_date` is not
    among the dates supplied for its ticker. Both are caller-side input
    mismatches, and both degrade the same way if left unchecked: an empty
    or wrong date list makes every gap test read as "still inside the
    window," so a ticker missing from `trading_dates` would silently
    collapse into one endless cluster instead of raising — the highest-risk
    failure class this codebase tracks (plausible, wrong, and silent), so
    it fails loudly at the boundary instead.
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
    #
    # A ticker with candidate events but no (or an empty) entry in
    # `trading_dates` is a caller-side input mismatch, not a valid empty
    # calendar — raise rather than default to `[]`. An empty list makes
    # `_trading_bars_between` return 0 for every pair (bisect on an empty
    # list gives `lo == hi == 0`), which never exceeds `max_hold_days`, so
    # every candidate for that ticker would silently collapse into one
    # endless cluster with `days_since_head == 0` throughout — a
    # plausible-looking, wrong answer with no error anywhere. That is
    # exactly the silent-collapse failure this check exists to convert into
    # a loud one at the boundary, per CLAUDE.md's highest-risk-failure
    # framing and the project's "assert the check actually ran" standard.
    sorted_dates: dict[str, list[date]] = {}
    for ticker in candidates["ticker"].unique():
        ticker_dates = trading_dates.get(ticker)
        if not ticker_dates:
            raise ValueError(
                f"tag_clusters: ticker {ticker!r} has candidate events but no "
                "trading_dates entry (or an empty one). Every ticker present in "
                "`candidates` must have its trading dates supplied, or the gap "
                "test silently degenerates to 'never break' for that ticker."
            )
        sorted_dates[ticker] = sorted(set(ticker_dates))

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
        dates = sorted_dates[cast("str", ticker)]
        ordered = group.sort_values("_signal_date_norm")

        head_date: date | None = None
        seq = 0
        for idx, signal_date in ordered["_signal_date_norm"].items():
            # A signal_date absent from the ticker's own supplied trading
            # dates means the two inputs describe different histories for
            # this ticker (e.g. `trading_dates` built from a narrower date
            # range than `candidates`) — the same silent-collapse risk as a
            # missing ticker entry, just partial instead of total, so it
            # gets the same loud failure rather than a plausible bar count
            # computed against a date the ticker never actually traded.
            pos_in_dates = bisect.bisect_left(dates, signal_date)
            if pos_in_dates >= len(dates) or dates[pos_in_dates] != signal_date:
                raise ValueError(
                    f"tag_clusters: ticker {ticker!r} has a candidate on "
                    f"{signal_date} that is not in its supplied trading_dates."
                )

            gap = (
                None if head_date is None else _trading_bars_between(dates, head_date, signal_date)
            )
            # `gap is None` exactly when `head_date is None` (the assignment
            # above), so this is the same branch written so the type checker
            # can see `gap` is an `int` in the `else`.
            if gap is None or gap > max_hold_days:
                head_date = signal_date
                seq = 1
                days_since_head = 0
            else:
                seq += 1
                days_since_head = gap  # same pair `gap` already measured

            # The four `*_col` lists below are indexed by `pos`, so this path
            # already requires the scalar return — a duplicated index would
            # hand back a slice/mask and break the assignment regardless of
            # this cast, which changes no behaviour either way.
            pos = cast("int", working.index.get_loc(idx))
            cluster_id_col[pos] = _deterministic_cluster_id(ticker, side, head_date)
            seq_col[pos] = seq
            head_col[pos] = seq == 1
            days_col[pos] = days_since_head

    out = candidates.copy()
    out["cluster_id"] = cluster_id_col
    out["seq_in_cluster"] = seq_col
    out["is_cluster_head"] = head_col
    out["days_since_head"] = days_col
    return out
