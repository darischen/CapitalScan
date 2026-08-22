"""Validation harness — DESIGN §5.10, Session 9 Task 10, Phase 3 gate.

Five checks against an already-produced `events` frame (the output of
`research.backtest.run_backtest`): no look-ahead, entry sanity, exit
sanity, return identity, non-overlap. Each check returns its own
violations rather than raising, so one check failing does not hide the
other four (task brief, explicit design point) — a caller that stops at
the first `AssertionError` only ever learns about one problem per run.

**Pure, no IO** (CLAUDE.md invariant 1 extends by convention to this
module the same way it does to `research/candidates.py` and
`research/enrich.py`: `research/` owns IO in principle, but nothing here
touches a database, a socket, or a clock — `events` and `bars_by_ticker`
arrive as already-loaded frames, and the caller (a future Task 11 CLI
step) is the one that reads them off Postgres).

**`bars_by_ticker` is bars *and* indicators merged, one frame per
ticker.** `research.backtest._backtest_one_ticker` reads `bars` and
`indicators` as two separate frames because the database stores them in
two tables, but the no-look-ahead check needs both together — it reruns
`research.candidates.scan_candidates`, which takes a bars frame and an
indicators frame and pairs them by date — while the entry/exit sanity and
non-overlap checks need only the OHLC columns. Handing this module one
merged frame per ticker (every OHLCV column plus every indicator column
scan_candidates or a caller would want, sharing `ticker`/`ts`) means a
single input shape serves all five checks and there is exactly one
`bars_by_ticker` to keep synchronized, not two dicts that could silently
drift apart in the caller. The frame is used as *both* the `bars` and the
`indicators` argument to `scan_candidates` — that function reads only the
columns it needs from each, so handing it the same frame twice is
harmless, and it is the only detection implementation invoked here
(invariant 2: never a second band comparison, never a second `detect`
path). Any column not in `_BAR_COLUMNS` is treated as an indicator column
for the shift ladder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import cast

import pandas as pd

from capitalscan.core.config import Config, CostParams
from capitalscan.core.returns import _first_hourly_touch, realized_return
from capitalscan.core.types import EntryKind, Side
from capitalscan.research.candidates import _trading_bars_between, scan_candidates

# ---------------------------------------------------------------------------
# No-look-ahead — TESTS.md §3.1's shift ladder plus shuffled control.
#
# A single Jaccard threshold does not work (TESTS.md §3.1): measured on real
# bars, a one-bar shift lands near 0.59, not below 0.5, because bands drift
# roughly 0.35% of price per day while a bar's own range is roughly 1.59%,
# so a bar touching yesterday's band usually touches the day-before's too.
# TESTS.md anchors four bounds to a shuffled control instead, and this
# module implements exactly those four (not the brief summary's paraphrase,
# per CONSTRAINTS.md: "read TESTS.md §3.1 ... cite it in a comment").
#
# These are judgment-call thresholds for a diagnostic test, not a modelling
# parameter with a natural home in `core/config.py` (invariant 9 exempts
# "no magic numbers outside core/config.py" only insofar as every such
# threshold has to live *somewhere*; these are named module constants,
# cited to the doc section that derived them, per CONSTRAINTS.md's explicit
# allowance for exactly this case).
_SHIFT_LEVELS: tuple[int, ...] = (1, 2, 5, 20)
_JACCARD_CONTROL_FLOOR = 0.15  # TESTS.md §3.1: jc < 0.15
_JACCARD_SHIFT1_CEILING = 0.80  # TESTS.md §3.1: j[1] < 0.80
_JACCARD_SHIFT5_CEILING = 0.50  # TESTS.md §3.1: j[5] < 0.50 (the original threshold)
_JACCARD_CONTROL_CONVERGENCE_MULT = 2.0  # TESTS.md §3.1: j[20] < 2 * jc

# Row-shuffling the control needs a seed to stay deterministic (ADR 060: no
# wall-clock read, and a harness report must reproduce identically against
# identical input). Any fixed value works; this one has no other meaning.
_SHUFFLE_SEED = 20260801

# OHLCV columns `bars_by_ticker`'s frames always carry (module docstring).
# Everything else on the frame is treated as an indicator column for the
# shift ladder and the control shuffle.
_BAR_COLUMNS = frozenset({"ticker", "ts", "open", "high", "low", "close", "adj_close", "volume"})

# Prices are rounded to 4 decimals before any comparison (CLAUDE.md
# Conventions; `core.signals._breach` does the same). Two independently
# rounded prices can differ by up to 2 * 0.00005 before the comparison
# below even begins, so the sanity checks tolerate that much before calling
# a difference a violation, never more.
_PRICE_TOL = 1e-4

# `TOUCH_5M`/`TOUCH_30M` are priced from an hourly bar, never the daily bar
# (`core.returns.entry_price_for` -> `_first_hourly_touch`; DESIGN §5.4).
# `_check_entry_sanity` reads this set to pick the reference frame per row —
# validating either kind against the daily bar compares a fill to a frame it
# was never drawn from, which is exactly the defect this module exists to
# not have (task brief: real production example, PGR 2025-02-25, where the
# hourly feed's range escapes the daily feed's on both sides).
_HOURLY_ENTRY_KINDS = frozenset({EntryKind.TOUCH_5M.value, EntryKind.TOUCH_30M.value})


@dataclass(frozen=True)
class CheckResult:
    """One row of the harness report. `violations` is empty iff `passed`.

    `detail` carries check-specific diagnostics that are not violations by
    themselves (e.g. the no-look-ahead check's measured Jaccard values) —
    useful for a human reading the report even when the check passes.
    """

    name: str
    passed: bool
    violations: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessReport:
    """DESIGN §5.10's five checks, one `CheckResult` each."""

    no_lookahead: CheckResult
    entry_sanity: CheckResult
    exit_sanity: CheckResult
    return_identity: CheckResult
    non_overlap: CheckResult

    @property
    def all_passed(self) -> bool:
        """True only when every one of the five checks passed. A caller
        that only wants the gate verdict reads this; a caller that wants to
        know *which* check failed reads the five fields individually — the
        whole point of returning violations instead of raising."""
        return all(
            c.passed
            for c in (
                self.no_lookahead,
                self.entry_sanity,
                self.exit_sanity,
                self.return_identity,
                self.non_overlap,
            )
        )


def _as_date(value: object) -> date:
    """Same defensive `Timestamp` -> `date` coercion `research/candidates.py`
    and `research/enrich.py` already apply — `events`' date columns are
    plain `date` when they come straight from the backtest engine, but
    nothing enforces that a caller building a frame by hand used the same
    type."""
    if isinstance(value, pd.Timestamp):
        return value.date()
    return cast("date", value)


def _indexed_bars(bars_by_ticker: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """One date-indexed frame per ticker, built once so every check's bar
    lookups are `O(log n)` instead of re-scanning the frame per row."""
    out: dict[str, pd.DataFrame] = {}
    for ticker, frame in bars_by_ticker.items():
        if frame.empty:
            continue
        sorted_frame = frame.sort_values("ts")
        out[ticker] = sorted_frame.set_index(sorted_frame["ts"].dt.date, drop=False)
    return out


def _bar_row(indexed: dict[str, pd.DataFrame], ticker: str, target: date) -> pd.Series | None:
    frame = indexed.get(ticker)
    if frame is None or target not in frame.index:
        return None
    row = frame.loc[target]
    # A duplicate (ticker, date) pair would make `.loc` return a DataFrame,
    # not a Series — a caller-side data problem this function surfaces as
    # "no usable bar" rather than guessing which row is the real one.
    if isinstance(row, pd.DataFrame):
        return None
    return row


def _hourly_bar_for_entry(
    hourly_by_ticker: dict[str, pd.DataFrame] | None,
    ticker: str,
    day: date,
    touch_level: float | None,
    side: Side,
) -> pd.Series | None:
    """The hourly bar a `TOUCH_5M`/`TOUCH_30M` fill was actually priced
    from, or `None` if it cannot be determined.

    Reproduces exactly how `research.enrich.resolve_entries` scopes
    `hourly` before handing it to `core.returns.entry_price_for` — filtered
    to this ticker's own frame, then to rows whose `ts` falls on this
    calendar day, sorted by `ts` — and then calls
    `core.returns._first_hourly_touch` itself (invariant 2: not a second
    copy of that selection logic) to pick the same bar the engine picked.

    `None` covers every case where no such bar can be identified: no
    `hourly_by_ticker` supplied at all, the ticker missing from it, no
    hourly rows on this day, or a `touch_level` that (defensively; should
    not occur when `entry_price` is non-null — see `entry_price_for`) is
    itself NaN. The caller treats all of these as "cannot validate this
    row" and reports a violation rather than skipping it (task brief: a
    row this check cannot check must not silently count as passing).
    """
    if touch_level is None or pd.isna(touch_level):
        return None
    frame = hourly_by_ticker.get(ticker) if hourly_by_ticker else None
    if frame is None or frame.empty:
        return None
    day_hourly = frame.loc[frame["ts"].dt.date == day].sort_values("ts")
    if day_hourly.empty:
        return None
    return _first_hourly_touch(day_hourly, float(touch_level), side)


# ---------------------------------------------------------------------------
# Entry / exit sanity
# ---------------------------------------------------------------------------


def _pre_slippage_price(price: float, side: Side, cp: CostParams) -> float:
    """Reverses the adverse slippage `research.enrich.resolve_entries` bakes
    into `entry_price` (`price = raw ± raw * slippage_bps / 1e4`, adverse to
    `side`) to recover the price that actually traded inside the bar's
    `[low, high]`.

    `exit_price` needs no such reversal — `core.exits.resolve_exit` never
    applies slippage to it; slippage on the exit leg is charged in
    return-space by `core.costs.apply_costs` when `net_ret` is computed,
    not baked into `exit_price` itself (confirmed by reading
    `core/exits.py`: every returned price is `open_`, a `stop`/`target`
    level the bar's own extreme reached, or `bar["close"]` — all
    necessarily inside that bar's range already). Comparing `exit_price`
    to slippage-widened bounds would silently accept a genuinely wrong
    exit price as within tolerance; comparing it exactly is the correct,
    stricter check.
    """
    slip_frac = cp.slippage_bps / 1e4
    return price / (1.0 + slip_frac) if side is Side.LONG else price / (1.0 - slip_frac)


def _check_entry_sanity(
    events: pd.DataFrame,
    indexed_bars: dict[str, pd.DataFrame],
    config: Config,
    hourly_by_ticker: dict[str, pd.DataFrame] | None = None,
) -> CheckResult:
    """`entry_price` (slippage reversed) must fall inside the `[low, high]`
    of the bar it was actually priced from — which bar that is depends on
    `entry_kind` (task brief; the defect this fix addresses):

    - `TOUCH` / `NEXT_OPEN` price off the **daily** bar. `NEXT_OPEN` fills
      on bar t+1, so `entry_date` (not `signal_date`) selects the bar —
      `resolve_entries` already sets `entry_date` to the next session's
      date for that kind, so reading it off the row is correct and
      requires no further branching here.
    - `TOUCH_5M` / `TOUCH_30M` price off an **hourly** bar
      (`core.returns.entry_price_for` -> `_first_hourly_touch`). Checking
      these against the daily bar is comparing a fill to a reference frame
      it was never drawn from: on most days the daily bar's range contains
      the hourly bars', so the mistake is invisible, but the two feeds are
      independently sourced and can disagree (a real production example,
      quantified separately in the harness report, not fixed here — that
      is an ingest-side `bar_rejects` concern). `_hourly_bar_for_entry`
      reproduces `resolve_entries`' own scoping and calls
      `core.returns._first_hourly_touch` to find the same bar the engine
      priced from (invariant 2: not a second copy of that selection).

    Rows with a null `entry_price` (an unfilled position: pre-2024 hourly
    kinds, or a terminal-bar `NEXT_OPEN`) are skipped — there is no price
    to check, and CLAUDE.md invariant 4 forbids fabricating one. A priced
    `TOUCH_5M`/`TOUCH_30M` row whose hourly fill bar cannot be identified
    (no `hourly_by_ticker` supplied, or the identified bar disagrees with
    the engine's own selection) is a violation, not a skip — the check
    must stay capable of failing for exactly the rows it exists to
    protect, not go silent on them (task brief).
    """
    if events.empty:
        return CheckResult("entry_sanity", True, [], {"n_priced": 0, "n_validated": 0})
    violations: list[dict] = []
    priced = events.loc[events["entry_price"].notna()]
    n_validated = 0
    for _, row in priced.iterrows():
        ticker = row["ticker"]
        entry_date = _as_date(row["entry_date"])
        entry_kind = row.get("entry_kind")
        side = Side(row["side"])

        if entry_kind in _HOURLY_ENTRY_KINDS:
            frame_label = "hourly"
            bar = _hourly_bar_for_entry(
                hourly_by_ticker, ticker, entry_date, row.get("touch_level"), side
            )
            no_bar_reason = "no_hourly_bar_for_entry"
        else:
            frame_label = "daily"
            bar = _bar_row(indexed_bars, ticker, entry_date)
            no_bar_reason = "no_bar_for_entry_date"

        if bar is None:
            violations.append(
                {
                    "ticker": ticker,
                    "entry_date": entry_date,
                    "entry_kind": entry_kind,
                    "reason": no_bar_reason,
                }
            )
            continue
        n_validated += 1
        raw_price = _pre_slippage_price(float(row["entry_price"]), side, config.costs)
        low, high = float(bar["low"]), float(bar["high"])
        if not (low - _PRICE_TOL <= raw_price <= high + _PRICE_TOL):
            violations.append(
                {
                    "ticker": ticker,
                    "entry_date": entry_date,
                    "entry_kind": entry_kind,
                    "entry_price": float(row["entry_price"]),
                    "pre_slippage_price": raw_price,
                    "bar_low": low,
                    "bar_high": high,
                    "frame": frame_label,
                    "reason": "entry_price_outside_bar_range",
                }
            )
    # `n_priced` is every row with a non-null `entry_price` (what invariant
    # 4 lets this check look at); `n_validated` is the subset that actually
    # found a fill bar and was compared to it — a row hitting
    # `no_bar_for_entry_date`/`no_hourly_bar_for_entry` counts in the
    # first, not the second, so the two numbers answer different questions
    # ("how many rows could this check have examined" vs "how many did it
    # actually compare") rather than the same count reported twice under
    # one name.
    return CheckResult(
        "entry_sanity",
        len(violations) == 0,
        violations,
        {"n_priced": len(priced), "n_validated": n_validated},
    )


def _check_exit_sanity(
    events: pd.DataFrame, indexed_bars: dict[str, pd.DataFrame], config: Config
) -> CheckResult:
    """`exit_price` must fall inside its exit bar's `[low, high]`, exactly
    (no slippage — see `_pre_slippage_price`'s docstring).

    Rows with a null `exit_price` (an unresolved position — `entry_price`
    was itself null, or the signal fired with no forward bar at all) are
    skipped, same reasoning as `_check_entry_sanity`.
    """
    if events.empty:
        return CheckResult("exit_sanity", True, [], {"n_priced": 0, "n_validated": 0})
    violations: list[dict] = []
    priced = events.loc[events["exit_price"].notna()]
    n_validated = 0
    for _, row in priced.iterrows():
        ticker = row["ticker"]
        exit_date = _as_date(row["exit_date"])
        bar = _bar_row(indexed_bars, ticker, exit_date)
        if bar is None:
            violations.append(
                {
                    "ticker": ticker,
                    "exit_date": exit_date,
                    "exit_reason": row.get("exit_reason"),
                    "reason": "no_bar_for_exit_date",
                }
            )
            continue
        n_validated += 1
        exit_price = float(row["exit_price"])
        low, high = float(bar["low"]), float(bar["high"])
        if not (low - _PRICE_TOL <= exit_price <= high + _PRICE_TOL):
            violations.append(
                {
                    "ticker": ticker,
                    "exit_date": exit_date,
                    "exit_reason": row.get("exit_reason"),
                    "exit_price": exit_price,
                    "bar_low": low,
                    "bar_high": high,
                    "reason": "exit_price_outside_bar_range",
                }
            )
    # See `_check_entry_sanity`'s comment on `n_priced` vs `n_validated`.
    return CheckResult(
        "exit_sanity",
        len(violations) == 0,
        violations,
        {"n_priced": len(priced), "n_validated": n_validated},
    )


# ---------------------------------------------------------------------------
# Return identity
# ---------------------------------------------------------------------------


def _return_tolerance(entry_price: float, recomputed_gross_ret: float) -> float:
    """Derived per-row tolerance for `_check_return_identity`, replacing
    the flat `1e-9` DESIGN §5.10 originally specified — see
    harness-gate-diagnosis.md §1 ("`return_identity`'s 1e-9 tolerance is
    structurally unsatisfiable").

    The harness never sees the engine's original float64 prices. It reads
    `entry_price`/`exit_price` back off Postgres (`jobs/cli.py`'s
    `_load_events_for_run`, a plain `SELECT *`), where they are stored as
    `numeric(12,4)` — each independently rounded from the engine's
    float64 value to the nearest $0.0001, an error `e_E`/`e_X` with
    `|e_E|, |e_X| <= 5e-5`. `gross_ret` is `numeric(12,6)`, itself
    independently rounded from the engine's float64
    `(X_true-E_true)/E_true` to the nearest `1e-6`, error `|e_G| <= 5e-7`.
    Recomputing `gross_ret` from the *already-rounded* `entry_price`/
    `exit_price` and comparing it to the *separately, already-rounded*
    stored `gross_ret` is diffing two independent roundings of two
    different quantities, not one value re-derived from itself — `1e-9`
    agreement between them is unsatisfiable by construction, not a signal
    of anything wrong with the data.

    Exact first-order error propagation, `E`/`X` the stored (rounded)
    prices, `G_true = (X_true-E_true)/E_true` the engine's un-rounded
    return:

        G_recomp - G_true = (e_X - e_E)/(E+e_E) - G_true * e_E/(E+e_E)

    `E+e_E` is replaced with `E` below (the correction is at most
    `5e-5/E`, itself second-order against the terms it would adjust — at
    this dataset's minimum `E` of $6.87 that is a ~7e-6 relative nudge on
    terms already at the 1e-5 scale). Bounding each remaining term:

        |G_recomp - G_true| <= (|e_X|+|e_E|)/E + |G_true|*|e_E|/E
                             <= 1e-4/E + |G_true|*5e-5/E

    Then `gross_ret`'s own independent rounding (`|e_G| <= 5e-7`) adds on
    top, by the triangle inequality against the stored value:

        tolerance(E, G) = 1e-4/E + |G|*5e-5/E + 5e-7

    This differs from the diagnosis's suggested `1e-4/E + 5e-7`: that
    version drops the `|G|*5e-5/E` term as second-order, which holds for
    the *observed* data (G ~ 0.03-0.04 at the dataset's minimum entry
    price, where the term is ~2.9e-7, under half the 5e-7 rounding
    budget) but is not a bound that holds for every possible `G` — a
    later run with a larger realized return at a low entry price could
    make that term non-negligible. Keeping it costs nothing (it is
    computed, not assumed away) and makes the bound a genuine one for any
    `G`, rather than one calibrated to what today's run happens to
    measure (CLAUDE.md invariant 9's spirit: a check must not be
    calibrated to its own current output).

    Verified against harness-gate-diagnosis.md's worst observed row
    (AAPL, entry_price=6.8817, gross_ret=0.04, measured diff=9.881e-6):
    this formula gives tolerance=1.532e-5, comfortably above the diff.
    """
    return 1e-4 / entry_price + abs(recomputed_gross_ret) * 5e-5 / entry_price + 5e-7


def _check_return_identity(events: pd.DataFrame) -> CheckResult:
    """`gross_ret` recomputed from `(entry_price, exit_price, side)` via
    `core.returns.realized_return` — the one return implementation
    (invariant 2) — must match the stored value to within `_return_tolerance`
    (see its docstring for the derivation; DESIGN §5.10's original flat
    `1e-9` cannot be satisfied by a value recomputed from `numeric(12,4)`
    storage, per harness-gate-diagnosis.md §1).

    NaN handling (task brief: "decide how those are treated and justify
    it"): `realized_return` returns NaN whenever either price is NaN — the
    pre-2024 hourly kinds and the terminal-bar `NEXT_OPEN` case both leave
    `gross_ret` legitimately null on `events` (DESIGN §5.4). A row where
    *both* the stored and the recomputed value are NaN is not a violation;
    it is the honest, matching answer for an unresolved position. A row
    where only one of the two is NaN — the recomputation disagrees with
    the engine about whether this position ever resolved at all — is a
    violation, not a pass, and is exactly the case a `<mark NaN as always
    passing>` shortcut would hide.
    """
    if events.empty:
        return CheckResult("return_identity", True, [], {"n_checked": 0})
    violations: list[dict] = []
    for _, row in events.iterrows():
        side = Side(row["side"])
        recomputed = realized_return(row["entry_price"], row["exit_price"], side)
        reported = row["gross_ret"]
        reported_nan, recomputed_nan = pd.isna(reported), pd.isna(recomputed)
        if reported_nan and recomputed_nan:
            continue
        is_mismatch = reported_nan != recomputed_nan
        if not is_mismatch:
            tol = _return_tolerance(float(row["entry_price"]), float(recomputed))
            is_mismatch = abs(float(reported) - float(recomputed)) >= tol
        if is_mismatch:
            violations.append(
                {
                    "ticker": row["ticker"],
                    "signal_date": _as_date(row["signal_date"]),
                    "entry_kind": row.get("entry_kind"),
                    "reported_gross_ret": None if reported_nan else float(reported),
                    "recomputed_gross_ret": None if recomputed_nan else float(recomputed),
                    "reason": "gross_ret_mismatch",
                }
            )
    return CheckResult(
        "return_identity", len(violations) == 0, violations, {"n_checked": len(events)}
    )


# ---------------------------------------------------------------------------
# Non-overlap
# ---------------------------------------------------------------------------


def _check_non_overlap(
    events: pd.DataFrame, indexed_bars: dict[str, pd.DataFrame], config: Config
) -> CheckResult:
    """No two `is_cluster_head` events on one `(ticker, side)` have
    overlapping windows.

    Measured in **trading bars**, not calendar days, and via
    `research.candidates._trading_bars_between` specifically (not a
    reimplementation) — `tag_clusters` (Ruling C5) counts trading bars
    because `ExitParams.max_hold_days` counts forward bars, and a
    calendar-day gap test would disagree with the tagger across every
    weekend or holiday in the data (a 5-calendar-day gap spanning a
    weekend is only 3 trading bars). Reusing the exact function the tagger
    itself calls is what keeps this check from disagreeing with the data
    it is checking (CONSTRAINTS.md: "the overlap test must use the same
    measure or it will disagree with the tagger that produced the data").

    **Grouped by `(ticker, side)`, matching `tag_clusters` exactly**
    (`candidates.py:281-305`, Ruling C5 — "a long cluster and a short
    cluster on one ticker are different positions and must never merge,
    even on identical dates"). An earlier version of this check grouped by
    ticker alone on the theory that a long and a short over the same
    window are not independent observations; that reasoning does not hold
    against DESIGN §5.3's own stated purpose for `is_cluster_head`
    filtering ("non-overlapping, clean standard errors" — avoiding
    double-counting *same-side* observations, not merging opposite-side
    trades into one unit), and the primary-statistics consumer already
    separates cells by `signal_type`, which is itself side-bearing
    (`confluence_low`/`bb_lower_touch` vs `confluence_high`/
    `bb_upper_touch`) — a long head and a short head land in different
    cells, not one over-counted sample. Grouping by ticker alone therefore
    flagged a stock touching both bands in one choppy window — normal,
    expected mega-cap behavior, and exactly what `tag_clusters` itself
    produces — as a false `cluster_head_windows_overlap` violation. The
    code that produced the data settles the question: `tag_clusters`'s own
    key is `(ticker, side)`, so this check uses the same one.

    A ticker with `is_cluster_head` events but **no entry in
    `indexed_bars` at all** cannot be gap-tested (there is no trading
    calendar to measure bars against) and is reported as a violation, not
    silently skipped — matching `_check_entry_sanity`/`_check_exit_sanity`'s
    `no_bar_for_entry_date`/`no_bar_for_exit_date` precedent for the
    identical condition (missing bar data for a row this check needs to
    price). Silently skipping would let `non_overlap.passed` read `True`
    while a subset of the input was never actually checked — the "a check
    that cannot fail" failure mode this task exists to prevent, just
    scoped to a subset of tickers instead of the whole check.

    **Deduped to one row per `(ticker, side, signal_date)` before the gap
    walk** (harness-gate-diagnosis.md §2). `events`' grain is one row per
    `(config_hash, ticker, signal_date, signal_type, entry_kind)` — one
    signal produces four rows (`touch`, `touch_5m`, `touch_30m`,
    `next_open`), all sharing `is_cluster_head`, `cluster_id`, and
    `signal_date`. Without the dedupe, four rows at the identical date
    sort adjacently and the walk reports 3 zero-gap "overlaps" per head
    that are the same signal counted four times, not a genuine
    cross-cluster overlap — verified on the live run as a 100% artifact:
    2128 head rows, 532 distinct head signals, exactly 1596 (= 3 x 532)
    surplus violations, zero left over once deduped. This is the same
    defect class as the `(ticker, side)` grouping fix above, one axis
    over: the checker's grouping didn't match the contract of the thing
    it's checking.
    """
    if events.empty:
        return CheckResult(
            "non_overlap", True, [], {"n_heads": 0, "n_pairs_checked": 0, "n_groups_no_bars": 0}
        )
    violations: list[dict] = []
    heads = events.loc[events["is_cluster_head"] == True]  # noqa: E712 - pandas bool mask
    n_pairs_checked = 0
    n_groups_no_bars = 0
    for (ticker, side), group in heads.groupby(["ticker", "side"]):
        frame = indexed_bars.get(cast("str", ticker))
        if frame is None:
            n_groups_no_bars += 1
            violations.append(
                {
                    "ticker": ticker,
                    "side": side,
                    "n_heads_unchecked": len(group),
                    "reason": "no_bars_for_ticker",
                }
            )
            continue
        dates = sorted(frame.index)
        # Dedupe across entry kinds before the walk (docstring above) — one
        # row per distinct signal_date within this (ticker, side) group.
        # Which of the (up to) four entry-kind rows survives is irrelevant
        # past this point: only ticker/side/signal_date drive the gap test.
        ordered = (
            group.assign(_signal_date=group["signal_date"].map(_as_date))
            .drop_duplicates(subset="_signal_date")
            .sort_values("_signal_date")
        )
        prev_date: date | None = None
        for _, row in ordered.iterrows():
            signal_date = row["_signal_date"]
            if prev_date is not None:
                n_pairs_checked += 1
                gap = _trading_bars_between(dates, prev_date, signal_date)
                if gap <= config.exits.max_hold_days:
                    violations.append(
                        {
                            "ticker": ticker,
                            "side": side,
                            "first_head_date": prev_date,
                            "second_head_date": signal_date,
                            "trading_bar_gap": gap,
                            "max_hold_days": config.exits.max_hold_days,
                            "reason": "cluster_head_windows_overlap",
                        }
                    )
            prev_date = signal_date
    return CheckResult(
        "non_overlap",
        len(violations) == 0,
        violations,
        {
            "n_heads": len(heads),
            "n_pairs_checked": n_pairs_checked,
            "n_groups_no_bars": n_groups_no_bars,
        },
    )


# ---------------------------------------------------------------------------
# No look-ahead
# ---------------------------------------------------------------------------


def _event_set(bars_and_indicators: pd.DataFrame, config: Config) -> set[tuple]:
    """Runs the one detection implementation (`scan_candidates`, which
    itself routes every comparison through `core.signals.detect` —
    invariant 2) over a bars+indicators frame and reduces the result to a
    hashable set for Jaccard comparison: `(ticker, signal_date,
    signal_type)`. The same merged frame is passed as both the `bars` and
    `indicators` argument (module docstring) — `scan_candidates` reads only
    the columns each role needs, so this is not a second detection path,
    just one frame serving two positional roles.
    """
    candidates, _rejects = scan_candidates(bars_and_indicators, bars_and_indicators, config.signals)
    if candidates.empty:
        return set()
    dates = candidates["signal_date"].map(_as_date)
    return set(zip(candidates["ticker"], dates, candidates["signal_type"]))


def _jaccard(a: set, b: set) -> float:
    union = a | b
    if not union:
        return 1.0  # both empty: no measurable disagreement, vacuously "identical"
    return len(a & b) / len(union)


def _shift_indicator_columns(
    frame: pd.DataFrame, k: int, indicator_cols: list[str]
) -> pd.DataFrame:
    """Every indicator column shifted `k` rows forward **within each
    ticker**, sorted by `ts` first so the shift walks chronological order.
    OHLCV columns are untouched — only the indicator side of the pairing
    moves, which is the whole point of the ladder (TESTS.md §3.1)."""
    sorted_frame = frame.sort_values(["ticker", "ts"]).reset_index(drop=True)
    out = sorted_frame.copy()
    out[indicator_cols] = sorted_frame.groupby("ticker")[indicator_cols].shift(k)
    return out


def _shuffled_control(frame: pd.DataFrame, indicator_cols: list[str], seed: int) -> pd.DataFrame:
    """Row-shuffles every indicator column across the **whole** frame
    (TESTS.md §3.1: `indicators.sample(frac=1.0)`), severing any real
    relationship between a bar and the indicator values attached to it.
    This is the floor a genuine signal must clear: a detector reading
    indicators that carry no information about the corresponding bar
    should barely agree with the un-shuffled event set at all."""
    shuffled_values = (
        frame[indicator_cols].sample(frac=1.0, random_state=seed).reset_index(drop=True)
    )
    out = frame.reset_index(drop=True).copy()
    out[indicator_cols] = shuffled_values
    return out


@dataclass(frozen=True)
class LookaheadCounts:
    """Set-overlap counts for one slice of tickers, before any ratio.

    **Workers return counts, never Jaccards.** Tickers are disjoint, so the
    whole-universe value is

        J = sum |Ai n Bi| / sum |Ai u Bi|

    which is reconstructable from these and *not* from per-chunk ratios.
    Averaging ratios is a different number that still lands in [0, 1] and
    still moves in the right direction, so it survives every test that does
    not compare it against the single-process answer.

    The empty chunk is the sharp case and it fails toward a pass:
    `_jaccard` of two empty sets is 1.0 by design ("vacuously identical"),
    so a chunk whose tickers produced no events would contribute a perfect
    agreement score it never measured. `j[1] < 0.80` and `j[5] < 0.50` are
    ceilings, so inflating the Jaccard is exactly what a look-ahead-broken
    engine would do. Counted, such a chunk adds (0, 0) and changes nothing.
    """

    base_n: int
    intersect: dict[int, int]
    union: dict[int, int]
    control_intersect: int
    control_union: int


def _lookahead_counts(
    bars_and_indicators: pd.DataFrame,
    shuffled: pd.DataFrame,
    indicator_cols: list[str],
    config: Config,
) -> LookaheadCounts:
    """The six detection passes over one slice, reduced to overlap counts.

    `shuffled` is supplied rather than computed here: `_shuffled_control`
    mixes indicator values *across* tickers on purpose, so it must be built
    once over the whole universe and only then sliced. Shuffling inside a
    slice mixes fewer tickers, leaves more of the bar-to-indicator
    relationship intact, and pushes `jc` toward its 0.15 floor for reasons
    unrelated to look-ahead.
    """
    base = _event_set(bars_and_indicators, config)
    intersect: dict[int, int] = {}
    union: dict[int, int] = {}
    for k in _SHIFT_LEVELS:
        shifted = _event_set(
            _shift_indicator_columns(bars_and_indicators, k, indicator_cols), config
        )
        intersect[k] = len(base & shifted)
        union[k] = len(base | shifted)
    control = _event_set(shuffled, config)
    return LookaheadCounts(
        base_n=len(base),
        intersect=intersect,
        union=union,
        control_intersect=len(base & control),
        control_union=len(base | control),
    )


def _lookahead_verdict(counts: LookaheadCounts) -> CheckResult:
    """The five threshold rules, applied once over summed counts."""

    def ratio(inter: int, uni: int) -> float:
        # Mirrors `_jaccard`'s empty convention so a config that produced no
        # events anywhere still reads "vacuously identical" rather than 0.
        return 1.0 if uni == 0 else inter / uni

    jaccard_by_shift = {k: ratio(counts.intersect[k], counts.union[k]) for k in _SHIFT_LEVELS}
    jc = ratio(counts.control_intersect, counts.control_union)

    if counts.base_n == 0:
        return CheckResult(
            "no_lookahead", False, [{"reason": "no_base_events_to_test_against"}], {"base_n": 0}
        )

    violations: list[dict] = []
    j1, j2, j5, j20 = (jaccard_by_shift[k] for k in (1, 2, 5, 20))

    if not (jc < _JACCARD_CONTROL_FLOOR):
        violations.append(
            {"bound": "control_floor", "rule": f"jc < {_JACCARD_CONTROL_FLOOR}", "jc": jc}
        )
    if not (j1 < _JACCARD_SHIFT1_CEILING):
        violations.append(
            {
                "bound": "shift1_material_change",
                "rule": f"j[1] < {_JACCARD_SHIFT1_CEILING}",
                "j1": j1,
            }
        )
    if not (j1 > j2 > j5 > j20):
        violations.append(
            {
                "bound": "monotonic_decay",
                "rule": "j[1] > j[2] > j[5] > j[20]",
                "j1": j1,
                "j2": j2,
                "j5": j5,
                "j20": j20,
            }
        )
    if not (j5 < _JACCARD_SHIFT5_CEILING):
        violations.append(
            {"bound": "shift5_ceiling", "rule": f"j[5] < {_JACCARD_SHIFT5_CEILING}", "j5": j5}
        )
    if not (j20 < _JACCARD_CONTROL_CONVERGENCE_MULT * jc):
        violations.append(
            {
                "bound": "shift20_converges_to_control",
                "rule": f"j[20] < {_JACCARD_CONTROL_CONVERGENCE_MULT} * jc",
                "j20": j20,
                "jc": jc,
            }
        )

    return CheckResult(
        "no_lookahead",
        len(violations) == 0,
        violations,
        {
            "jaccard_by_shift": jaccard_by_shift,
            "jaccard_control": jc,
            "base_n": counts.base_n,
        },
    )


def _check_no_lookahead(
    events: pd.DataFrame, bars_by_ticker: dict[str, pd.DataFrame], config: Config
) -> CheckResult:
    """TESTS.md §3.1's shift ladder plus shuffled control, over the same
    universe `events` was produced from. `events` itself is not read here
    — the check reruns detection from scratch on `bars_by_ticker` — but it
    is still the object being validated: a materially different event set
    under a one-bar shift is what says the engine that produced `events`
    is actually reading indicators from where it claims to.
    """
    if not bars_by_ticker:
        return CheckResult("no_lookahead", False, [{"reason": "no_bars_supplied"}])

    combined = pd.concat(bars_by_ticker.values(), ignore_index=True)
    indicator_cols = [c for c in combined.columns if c not in _BAR_COLUMNS]
    if not indicator_cols:
        return CheckResult(
            "no_lookahead", False, [{"reason": "no_indicator_columns_in_bars_by_ticker"}]
        )

    base = _event_set(combined, config)
    if not base:
        return CheckResult(
            "no_lookahead", False, [{"reason": "no_base_events_to_test_against"}], {"base_n": 0}
        )

    jaccard_by_shift = {
        k: _jaccard(base, _event_set(_shift_indicator_columns(combined, k, indicator_cols), config))
        for k in _SHIFT_LEVELS
    }
    control_set = _event_set(_shuffled_control(combined, indicator_cols, _SHUFFLE_SEED), config)
    jc = _jaccard(base, control_set)

    violations: list[dict] = []
    j1, j2, j5, j20 = (jaccard_by_shift[k] for k in (1, 2, 5, 20))

    if not (jc < _JACCARD_CONTROL_FLOOR):
        violations.append(
            {"bound": "control_floor", "rule": f"jc < {_JACCARD_CONTROL_FLOOR}", "jc": jc}
        )
    if not (j1 < _JACCARD_SHIFT1_CEILING):
        violations.append(
            {
                "bound": "shift1_material_change",
                "rule": f"j[1] < {_JACCARD_SHIFT1_CEILING}",
                "j1": j1,
            }
        )
    if not (j1 > j2 > j5 > j20):
        violations.append(
            {
                "bound": "monotonic_decay",
                "rule": "j[1] > j[2] > j[5] > j[20]",
                "j1": j1,
                "j2": j2,
                "j5": j5,
                "j20": j20,
            }
        )
    if not (j5 < _JACCARD_SHIFT5_CEILING):
        violations.append(
            {"bound": "shift5_ceiling", "rule": f"j[5] < {_JACCARD_SHIFT5_CEILING}", "j5": j5}
        )
    if not (j20 < _JACCARD_CONTROL_CONVERGENCE_MULT * jc):
        violations.append(
            {
                "bound": "shift20_converges_to_control",
                "rule": f"j[20] < {_JACCARD_CONTROL_CONVERGENCE_MULT} * jc",
                "j20": j20,
                "jc": jc,
            }
        )

    return CheckResult(
        "no_lookahead",
        len(violations) == 0,
        violations,
        {"jaccard_by_shift": jaccard_by_shift, "jaccard_control": jc, "base_n": len(base)},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_harness(
    events: pd.DataFrame,
    bars_by_ticker: dict[str, pd.DataFrame],
    config: Config,
    hourly_by_ticker: dict[str, pd.DataFrame] | None = None,
) -> HarnessReport:
    """DESIGN §5.10. Runs all five checks and returns a `HarnessReport`
    regardless of whether any individual check failed — this is the Phase 3
    gate, and a gate that raises on the first problem only ever reports one
    problem per run.

    `hourly_by_ticker` is a second, optional per-ticker dict — one raw
    hourly-bar frame per ticker, the same shape `jobs.cli`'s own hourly
    read produces (`ticker, ts, open, high, low, close, ...`) — used only
    by `entry_sanity`, to validate `TOUCH_5M`/`TOUCH_30M` fills against the
    hourly bar they were actually priced from instead of the daily bar
    (see `_check_entry_sanity`'s docstring). It is kept separate from
    `bars_by_ticker` rather than merged into it: `bars_by_ticker` is one
    row per `(ticker, date)` (the module docstring's "one frame per
    ticker" shape every other check also relies on), while hourly bars are
    several rows per date — merging them in would break that grain for the
    four checks that do not need hourly data at all. Defaults to `None`
    (no hourly data available): every `TOUCH_5M`/`TOUCH_30M` priced row
    then reports `no_hourly_bar_for_entry` rather than silently skipping,
    since a check that cannot fail is worse than one reporting it lacks
    the data it needs (task brief).
    """
    indexed_bars = _indexed_bars(bars_by_ticker)
    return HarnessReport(
        no_lookahead=_check_no_lookahead(events, bars_by_ticker, config),
        entry_sanity=_check_entry_sanity(events, indexed_bars, config, hourly_by_ticker),
        exit_sanity=_check_exit_sanity(events, indexed_bars, config),
        return_identity=_check_return_identity(events),
        non_overlap=_check_non_overlap(events, indexed_bars, config),
    )
