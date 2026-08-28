"""Event enrichment — DESIGN §5.2 steps 7-12: entry, exit, path metrics,
costs, context, split assignment.

Orchestration, not new logic (Session 9 charter). Every hard decision this
module needs already lives in `core/`, covered by the project's five
load-bearing tests: `core.returns.entry_price_for` owns every entry-price
decision (the `TOUCH` gap rule, the `NEXT_OPEN` terminal-bar null, the
hourly interpolation for `TOUCH_5M`/`TOUCH_30M`), `core.returns.mfe_mae`
owns path metrics, `core.costs` owns slippage and borrow, and
`research.backtest.split_key_for` (re-exported from `jobs/config.py`, Ruling
C2) owns train/validate/holdout labelling. This module's job is to slice the
right `bar` / `next_bar` / `hourly` window out of the frames it is handed,
apply costs, and shape the results into rows — never to make a second price
decision (CLAUDE.md invariant 2).

This file grows across Session 9:
  - Task 5: `resolve_entries` — step 7, entry resolution.
  - Task 6: `resolve_exit_for_entry` — step 8, exit resolution.
  - Task 7: `path_metrics` — step 9, path metrics.
  - Task 8 (this task): `enrich_context` — steps 10-12, cost application,
    context tagging, split assignment.

`research/` owns IO in principle, but nothing in this module touches a
database, a socket, or a clock — `bars` and `hourly` arrive as already-
loaded frames, matching `research/candidates.py`'s convention. Entry does
not depend on any exit parameter, which is what lets the sweep compute
entries once per event and reuse them across all 18 exit configs (DESIGN
§5.9).
"""

from __future__ import annotations

from datetime import date
from typing import cast

import numpy as np
import pandas as pd

from capitalscan.core import costs as core_costs
from capitalscan.core.config import CostParams, ExitParams, SplitParams, StatsParams
from capitalscan.core.exits import resolve_exit
from capitalscan.core.returns import entry_price_for, forward_returns, mfe_mae, realized_return
from capitalscan.core.signals import _breach, _isnan
from capitalscan.core.types import Bound, EntryKind, Side

# `research/` may reach into `research/backtest.py` for the shared
# labelling functions, but never into `jobs/` directly (that module's own
# docstring: "so nothing in research/ reaches into jobs/ directly for
# them"). `split_key_for` is canonical in `jobs/config.py` (Ruling C2);
# this is the one re-exported import point.
from capitalscan.research.backtest import split_key_for


def _as_date(value: object) -> date:
    """Same defensive `Timestamp` -> `date` coercion `research/candidates.py`
    already applies to `signal_date`. It arrives as a plain `date` when it
    comes straight from `scan_candidates` (`core.signals._bar_date` returns
    one), but nothing enforces that a caller building a candidate row by
    hand used the same type.
    """
    if isinstance(value, pd.Timestamp):
        return value.date()
    return cast("date", value)


def _ticker_bars(bars: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """`bars` for one ticker, indexed by calendar date, sorted by `ts`.

    Filters by `ticker` only when the column is present, so a caller that
    already sliced to one ticker before calling (the shape `candidates.py`
    uses in its own per-ticker loop) pays no extra cost and a caller that
    hands over a multi-ticker frame still gets the right slice.
    """
    frame = bars.loc[bars["ticker"] == ticker] if "ticker" in bars.columns else bars
    frame = frame.sort_values("ts")
    return frame.set_index(frame["ts"].dt.date, drop=False)


def _touch_level_or_nan(candidate: pd.Series) -> float:
    """Normalize `candidate["touch_level"]` to a float, `nan` when absent.

    `core.types.SignalHit.touch_level` is `None`, never `NaN`, for a
    stochastic-only hit (see that field's own comment: `nan != nan` would
    make two identical hits compare unequal while still hashing the same,
    so a set-based dedupe upstream would silently keep the duplicate).
    `entry_price_for` calls `_isnan` on the level, which does tolerate
    `None` — but this module also calls `_breach` directly, for the
    "did the day gap past the band" fact attached to every touch-level
    row, and `_breach` needs an actual float `nan` from that same `_isnan`
    tolerance rather than being handed `None` twice over. Normalizing once
    here keeps that single conversion in one place.
    """
    level = candidate["touch_level"]
    return float("nan") if _isnan(level) else float(level)


def resolve_entries(
    candidate: pd.Series,
    bars: pd.DataFrame,
    hourly: pd.DataFrame | None,
    cp: CostParams,
) -> list[dict]:
    """DESIGN §5.2 step 7, §5.4. One row per `EntryKind`, entry price only.

    Every row carries `entry_kind`, `entry_date`, `entry_price` (with
    slippage already applied, adverse to `candidate["side"]`), and
    `entry_gapped`. All four kinds are always produced — including
    `TOUCH_5M`/`TOUCH_30M` when `hourly is None`, which yield a `NaN`
    price rather than being dropped (DESIGN §3.8: hourly coverage starts
    2024-08-06, so pre-2024 events legitimately can't be priced at that
    granularity, and that absence belongs in the data, not as a missing
    row a downstream `COUNT(*)` would silently undercount).

    `bars` is the signal's own bar plus, ideally, at least the next
    session's bar (for `NEXT_OPEN`) — this function does not fetch a wider
    window itself (invariant 1: no IO in the price-decision path; the
    caller supplies what it needs). `hourly`, if given, may span multiple
    tickers and multiple days; this function scopes it to the signal's own
    ticker and calendar day before handing it to `entry_price_for`, since
    an unscoped hourly frame would let a later, unrelated day's breach
    (or a different ticker's) silently win the "first breaching bar" scan.

    Raises `ValueError` if `bars` has no row for the candidate's own
    `signal_date` — the signal fired on that bar, so its absence is a
    caller-side input mismatch, not a valid "no fill" case (contrast with
    `NEXT_OPEN`'s terminal bar, which is a real absence of a *next*
    session and produces a null row rather than raising).
    """
    ticker = candidate["ticker"]
    side = Side(candidate["side"])
    signal_date = _as_date(candidate["signal_date"])
    touch_level = _touch_level_or_nan(candidate)
    bound = Bound.LOWER if side is Side.LONG else Bound.UPPER

    ticker_bars = _ticker_bars(bars, ticker)
    if signal_date not in ticker_bars.index:
        raise ValueError(
            f"resolve_entries: no bar for {ticker} on {signal_date} — the "
            "candidate's own signal bar must be present in `bars`."
        )
    bar = cast("pd.Series", ticker_bars.loc[signal_date])

    later_dates = ticker_bars.index[ticker_bars.index > signal_date]
    next_bar = cast("pd.Series", ticker_bars.loc[later_dates.min()]) if len(later_dates) else None

    day_hourly = None
    if hourly is not None and not hourly.empty:
        hframe = hourly.loc[hourly["ticker"] == ticker] if "ticker" in hourly.columns else hourly
        day_hourly = hframe.loc[hframe["ts"].dt.date == signal_date].sort_values("ts")

    # Whether the signal day gapped past the band, from the bar's own
    # open — a property of the day, not of any one entry kind, so it is
    # computed once (DESIGN §5.4's gap rule, same comparison `TOUCH`
    # itself makes inside `entry_price_for`) and attached to every kind
    # that references a touch level. `None` when there is no level to gap
    # past (a stochastic-only signal), never a defaulted `False` — `False`
    # would misreport "checked, and it didn't gap" for a check that never
    # ran.
    open_px = cast("float", bar["open"])
    gapped = None if _isnan(touch_level) else _breach(float(open_px), touch_level, bound)

    rows: list[dict] = []
    for kind in EntryKind:
        raw_price = entry_price_for(kind, bar, next_bar, touch_level, side=side, hourly=day_hourly)

        price = raw_price
        if not _isnan(raw_price):
            leg_slip = core_costs.slippage(raw_price, cp)
            # Adverse to the side: a long pays more, a short receives less
            # (DESIGN §5.4: P_entry x (1 + bps/1e4) for a long).
            price = raw_price + leg_slip if side is Side.LONG else raw_price - leg_slip

        if kind is EntryKind.NEXT_OPEN:
            # NEXT_OPEN prices off the *next* session's open and never
            # references a band level at all, so "did the signal day gap
            # past the band" has no referent here — None (not applicable),
            # not a defaulted False that would read as "checked, no gap."
            entry_gapped = None
            entry_date = None if next_bar is None else next_bar["ts"].date()
        else:
            entry_gapped = gapped
            entry_date = signal_date

        rows.append(
            {
                "entry_kind": kind.value,
                "entry_date": entry_date,
                "entry_price": price,
                "entry_gapped": entry_gapped,
            }
        )

    return rows


def _unresolved_exit() -> dict:
    """The dict shape for a position that has nothing to resolve — either
    it never filled, or its signal fired on the last bar of available
    data. Every field reads as "not applicable," the same convention
    `entry_gapped=None` already uses in `resolve_entries`: `None`/`NaN`
    here means "not computed," never a defaulted zero or a fabricated
    TIMEOUT that would misreport a real evaluation that never ran.
    """
    return {
        "exit_idx": None,
        "exit_date": None,
        "exit_price": float("nan"),
        "exit_reason": None,
        "holding_days": None,
        "ambiguous": None,
    }


def _assert_entry_idx_matches(entry: dict, entry_idx: int, bars: pd.DataFrame) -> None:
    """Guard against the one-bar-shift class of bug (CLAUDE.md invariant 3:
    "the highest-risk silent failure in the system").

    `entry_idx` is a plain `int` — nothing type-checks that it addresses
    the bar the position actually opened on rather than, say, the signal
    bar `NEXT_OPEN` fills one session after. Both are valid positions
    inside `bars`, so a wrong one doesn't raise on its own; it just prices
    every exit for that entry off a window shifted by one bar, and the
    output still looks like a normal trade. This check catches it by
    comparing the date actually sitting at `entry_idx` against the date
    the entry itself claims to have filled on — data already in hand on
    both sides, so this is a read-only equality check, not a second
    date-resolution implementation (invariant 2).

    Also rejects an out-of-range `entry_idx` outright, rather than letting
    a negative index wrap to a wrong-but-valid bar or an oversized one
    surface as an opaque pandas `IndexError` several calls away from here.

    Reuses `_as_date` — the same `Timestamp` -> `date` coercion already
    applied to `signal_date` elsewhere in this module — because `bars`
    may be indexed by plain `date` (`research/candidates.py`'s
    convention) or by `Timestamp` (what `pd.date_range` produces, which
    this module's own tests build with), and comparing one directly
    against the other would fail even for correct input.
    """
    if entry_idx < 0 or entry_idx >= len(bars):
        raise ValueError(
            f"resolve_exit_for_entry: entry_idx={entry_idx} is out of range "
            f"for a {len(bars)}-row bars frame."
        )
    bar_date = _as_date(bars.index[entry_idx])
    fill_date = _as_date(entry["entry_date"])
    if bar_date != fill_date:
        raise ValueError(
            f"resolve_exit_for_entry: entry_idx={entry_idx} addresses "
            f"{bar_date}, but entry['entry_date'] is {fill_date}. "
            "entry_idx must be the position of the bar the entry actually "
            "filled on (NEXT_OPEN fills one bar after the signal date) — "
            "passing the signal bar's position shifts the entire forward "
            "window by one bar."
        )


def resolve_exit_for_entry(
    entry: dict,
    entry_idx: int,
    side: Side,
    bars: pd.DataFrame,
    indicators: pd.DataFrame,
    ep: ExitParams,
) -> dict:
    """DESIGN §5.2 step 8. Slices `bars`/`indicators` around one entry and
    hands the window to `core.exits.resolve_exit` — the only exit
    implementation in the repo (invariant 2, BUILD §9.3). No threshold is
    read from anywhere but `ep` (invariant 9): every level `resolve_exit`
    needs — stop, target, band, stochastic — is either derived inside it
    from `ep`'s fields or, for `atr_at_entry`, read here straight off the
    indicator row with no comparison or default applied.

    `bars` and `indicators` are one ticker's frames, sharing a positional
    index — the same convention `core.exits.resolve_exit`'s own
    `fwd_bars`/`fwd_ind` use. `entry_idx` is the position of the bar the
    entry **actually filled on**, taken from `entry["entry_date"]`, not
    the signal bar: `NEXT_OPEN` fills one session later, so a caller that
    passes the signal bar's position here shifts every exit in this
    position by one bar. Locating that position from `entry_date` is the
    caller's job (this function receives frames, not a date-to-position
    index) — but `_assert_entry_idx_matches` verifies the result before
    any slicing happens, raising `ValueError` on a mismatch or an
    out-of-range index rather than silently resolving off the wrong
    window.

    `ind_at_entry` is always passed to `resolve_exit`. Band levels for
    forward bar i come from bar i-1; `resolve_exit` derives that by
    shifting `fwd_ind`, which has no i-1 row for the first forward bar.
    `ind_at_entry` — the entry bar's own indicator row — is the only
    source for it. Omitting it does not raise; it silently skips band
    exits on the first forward bar only, which is exactly the kind of bug
    that would look fine in aggregate and wrong in the tail.

    Two cases never reach `resolve_exit`, and return `_unresolved_exit()`
    instead of a fabricated result:

    - `entry["entry_price"]` is `NaN` — the position never filled (a
      pre-2024 hourly entry kind, or a terminal-bar `NEXT_OPEN`).
      Resolving an exit for a position that was never opened is not a
      real event; calling `resolve_exit` anyway would still return a
      band or stochastic exit reason (those checks don't reference
      `entry_price` at all), which would misreport a phantom trade as a
      resolved one.
    - The forward window is completely empty — the signal fired on the
      last bar of available data, with no forward bar to price an exit
      against. `resolve_exit` raises on a wholly empty `fwd_bars`
      (`core/exits.py:210`); a single fresh signal at the edge of the
      ingested history is a real, expected case in production, not an
      input error, so it resolves to "not yet resolvable" rather than
      crashing a batch run.

    A window shorter than `ep.max_hold_days` but not empty (end-of-data
    truncation, a delisting) is a normal case `resolve_exit` already
    handles — it times out on the last available bar rather than raising,
    and this function does not special-case it.
    """
    entry_price = entry["entry_price"]
    if _isnan(entry_price):
        return _unresolved_exit()

    # Placed after the NaN short-circuit, not before: an entry that never
    # filled carries `entry_date=None` (see `resolve_entries`'s NEXT_OPEN
    # terminal-bar case), which has nothing to verify against and must not
    # reach this check.
    _assert_entry_idx_matches(entry, entry_idx, bars)

    fwd_bars = bars.iloc[entry_idx + 1 : entry_idx + 1 + ep.max_hold_days]
    if len(fwd_bars) == 0:
        return _unresolved_exit()
    fwd_ind = indicators.iloc[entry_idx + 1 : entry_idx + 1 + ep.max_hold_days]
    ind_at_entry = indicators.iloc[entry_idx]

    # `.get` (not `[]`) tolerates an indicator frame without `atr_14` — a
    # non-ATR stop mode never reads this value, and `resolve_exit`'s own
    # `stop_level` already treats a null ATR as "no stop," never a
    # substituted level, so there is nothing to guard against here beyond
    # not raising on a missing column.
    raw_atr = ind_at_entry.get("atr_14")
    atr_at_entry = float("nan") if _isnan(raw_atr) else float(cast("float", raw_atr))

    result = resolve_exit(
        entry_price=float(entry_price),
        entry_idx=entry_idx,
        side=side,
        fwd_bars=fwd_bars,
        fwd_ind=fwd_ind,
        atr_at_entry=atr_at_entry,
        ep=ep,
        ind_at_entry=ind_at_entry,
    )

    # `exit_idx` is surfaced (not just `holding_days`) so Task 7's path
    # metrics can bound their own window without recomputing it from
    # `holding_days - 1`.
    return {
        "exit_idx": result.exit_idx,
        "exit_date": result.exit_date,
        "exit_price": result.exit_price,
        "exit_reason": result.reason.value,
        "holding_days": result.holding_days,
        "ambiguous": result.ambiguous,
    }


def _pct_suffix(target: float) -> str:
    """`0.05` -> `"5pct"`, `0.10` -> `"10pct"` — the `events` schema's
    reachability columns (`touched_5pct`, `day_touched_5pct`, ...) are named
    off `StatsParams.reach_targets` percentages, not off the raw fraction.

    `round()`, not truncation or an f-string on the float directly: `0.10 *
    100` is `10.000000000000002` in binary floating point, and formatting
    that raw would produce `touched_10.000000000000002pct` — silently
    writing to a column that doesn't exist. `round()` is exact here because
    every target in practice is a multiple of a whole percentage point.
    """
    return f"{round(target * 100)}pct"


def path_metrics(
    entry_price: float,
    side: Side,
    fwd_bars: pd.DataFrame,
    exit_idx: int | None,
    exit_price: float,
    targets: tuple,
    adj_close_fwd: pd.Series | None,
    horizons: tuple,
    capture_ratio_cap: float,
) -> dict:
    """DESIGN §5.6, step 9. Two different windows over the same forward
    bars, and conflating them is the whole difficulty here:

    - MFE/MAE (`core.returns.mfe_mae`) are measured over `[t+1, exit_idx]`
      — the bars the position was actually open for. `fwd_bars` already
      starts at t+1 (the same frame `resolve_exit_for_entry` slices for
      `core.exits.resolve_exit`), and `exit_idx` is 0-based *within* that
      frame (`ExitResult.exit_idx`'s own convention), so the held window is
      `fwd_bars.iloc[:exit_idx + 1]` — inclusive of the exit bar itself.
    - Reachability (`touched_*pct` / `day_touched_*pct`) is measured over
      the FULL `fwd_bars`, regardless of where `exit_idx` landed. "Would a
      limit order at +5% have filled" is a question about the price path,
      independent of when the actual position closed — an exit on bar 2
      does not stop a bar-4 touch from being real.

    `exit_idx is None` means the position never resolved (never filled, or
    the forward window was empty — see `_unresolved_exit`), so every
    exit-dependent field is `None`/`NaN`, the same "not applicable, not a
    fabricated zero" convention `resolve_entries`/`resolve_exit_for_entry`
    already use. `fwd_ret_*d` is the one exception: DESIGN §5.6 calls it
    "unconditional... for baseline comparison" — it depends only on the
    price series, not on whether this particular entry kind ever filled —
    so it is computed whenever `adj_close_fwd` is given, even then.

    `targets` and `horizons` come from `StatsParams.reach_targets` and
    `StatsParams.fwd_ret_horizons` respectively (invariant 9: no magic
    numbers outside `core/config.py`) — this function only derives column
    *names* from them, via `_pct_suffix`.

    Two price series, matching DESIGN §2.2 (see also `core/returns.py`'s
    own module docstring): `fwd_bars` is split-adjusted OHLC — the series
    the market actually traded at, which is what a limit order or an MFE
    excursion needs. `adj_close_fwd` is **total-return** adjusted close —
    `forward_returns` measures return, and dividends are real return.

    `capture_ratio` (`η = R_exit / MFE`) needs `R_exit`, computed here via
    `core.returns.realized_return(entry_price, exit_price, side)` — never
    hand-rolled (invariant 2) — from an `exit_price` this function accepts
    as a parameter, since `core.returns` owns the one realized-return
    calculation and this module's job is only to route the right prices
    into it. Null (not a division by a negative or by zero) when `MFE <=
    0` — a zero MFE is a division by zero, not merely an odd ratio.
    Otherwise clamped to `[-capture_ratio_cap, capture_ratio_cap]`
    (`ExitParams.capture_ratio_cap`, invariant 9): MFE is never clamped
    (`core.returns.mfe_mae`'s own docstring), so it can sit arbitrarily
    close to zero, and `r_exit / mfe` is unbounded there — a real run
    (2026-08-05) hit a ratio exceeding `events.capture_ratio`'s
    `numeric(12,6)` column limit and aborted that ticker's insert batch.

    `adj_close_fwd`, when given, must start at the entry bar itself (its
    `iloc[0]` is the entry's own close) and extend forward at least
    `max(horizons)` bars — `core.returns.forward_returns` computes
    `close.shift(-h) / close - 1` elementwise over whatever it is given, so
    this function reads only its first row. This is a different window
    than `fwd_bars`: `max_hold_days` bounds `fwd_bars` at (by default) 5
    bars, but `fwd_ret_10d` needs a 10-bar horizon that a position-bounded
    frame cannot supply, which is why this is a second, separate parameter
    rather than a slice of `fwd_bars` — see the module-level note in
    `core/config.py`'s `StatsParams.fwd_ret_horizons` for why the horizons
    themselves live in config instead of literal in this module.
    """
    out: dict = {}

    if exit_idx is None or len(fwd_bars) == 0:
        # Not applicable, never a fabricated zero — the position never
        # resolved (unfilled entry, or a signal at the very edge of
        # available history; see `_unresolved_exit`).
        out["mfe"] = float("nan")
        out["mae"] = float("nan")
        out["time_to_mfe"] = None
        out["capture_ratio"] = None
        for target in targets:
            suffix = _pct_suffix(target)
            out[f"touched_{suffix}"] = None
            out[f"day_touched_{suffix}"] = None
    else:
        held_window = fwd_bars.iloc[: exit_idx + 1]
        mfe, mae, time_to_mfe = mfe_mae(entry_price, side, held_window)
        out["mfe"] = mfe
        out["mae"] = mae
        out["time_to_mfe"] = time_to_mfe

        r_exit = realized_return(entry_price, exit_price, side)
        # Boundary is <=, not <: MFE == 0 is a division by zero, not merely
        # an odd ratio (DESIGN §5.6, ADR 089).
        if _isnan(mfe) or mfe <= 0:
            out["capture_ratio"] = None
        else:
            ratio = float(r_exit / mfe)
            out["capture_ratio"] = max(-capture_ratio_cap, min(capture_ratio_cap, ratio))

        # Reachability: the level a limit order would need to fill, in the
        # favorable direction for `side` — the same direction `mfe_mae`
        # already uses, just against the FULL forward window rather than
        # the held one. Routed through `_breach`, the one band comparison
        # in the repo (invariant 2), not a second `>=`/`<=` written here.
        bound = Bound.UPPER if side is Side.LONG else Bound.LOWER
        price_col = "high" if side is Side.LONG else "low"
        entry = float(entry_price)

        # **Extracted once, for all four targets.** This was a nested loop
        # -- `for target: for bar in fwd_bars.iterrows()` -- so the window
        # was re-scanned once per `reach_targets` entry, materialising a
        # pandas Series per bar to read one column and make one comparison.
        # Profiled 2026-08-28 during arm 3: `path_metrics` was **41.2s of a
        # 110s** single-ticker compute, and the process built **193,469
        # Series for 7,428 events**, about 26 each. Same defect
        # `scan_candidates` had.
        #
        # `to_numeric(errors="coerce")` rather than `astype(float)`:
        # `iterrows()` on a frame mixing float and datetime columns yields
        # an object Series in which NaN becomes NaT, and the old
        # `float(bar[price_col])` raised `TypeError` on it before
        # `_breach`'s own NaN guard could run. Coercing to NaN here lets a
        # missing price simply not breach, which is what `_breach` intends.
        prices = np.round(
            pd.to_numeric(fwd_bars[price_col], errors="coerce").to_numpy(dtype=float), 4
        )

        for target in targets:
            suffix = _pct_suffix(target)
            level = entry * (1 + target) if side is Side.LONG else entry * (1 - target)

            # **`round`, not `np.round`, for the level.** They are not the
            # same function: measured over a boundary sweep they disagree
            # at exactly `level - 5e-05`, the half-way point, because
            # `np.round` scales-rounds-divides and picks up different float
            # error at the tie. The level carries full float precision from
            # `entry * (1 + target)`, so it gets Python's `round` to match
            # `core.signals._breach` exactly. The *prices* are safe under
            # `np.round` because `bars.high`/`low` are `numeric(12,4)` --
            # 0 of 5.9M daily rows carry more than four decimals -- so it
            # is the identity there. `test_path_metrics_reachability.py`
            # pins both halves.
            level_r = round(float(level), 4)
            mask = prices >= level_r if bound is Bound.UPPER else prices <= level_r
            hits = np.flatnonzero(mask)

            # First breach wins, and `day_number` is 1-based: the loop this
            # replaces used `enumerate(..., start=1)` and broke on the first
            # hit, and `day_touched_*pct` is written from it.
            touched = bool(hits.size)
            out[f"touched_{suffix}"] = touched
            out[f"day_touched_{suffix}"] = int(hits[0]) + 1 if touched else None

    # Unconditional forward returns (DESIGN §5.6): independent of whether
    # this entry kind ever filled, so computed whenever a close window is
    # available at all — see the docstring's `fwd_ret_*d` note.
    if adj_close_fwd is not None and len(adj_close_fwd) > 0:
        fwd = forward_returns(adj_close_fwd, list(horizons))
        entry_row = fwd.iloc[0]
        for h in horizons:
            val = entry_row[f"fwd_ret_{h}d"]
            out[f"fwd_ret_{h}d"] = float("nan") if _isnan(val) else float(val)
    else:
        for h in horizons:
            out[f"fwd_ret_{h}d"] = float("nan")

    return out


def _dd_bucket_label(dd_52w: object, sp: StatsParams) -> str | None:
    """DESIGN §5.2 step 11. Drawdown bucket label, derived from
    `sp.dd_buckets` (invariant 9) rather than restated as literal strings.

    `jobs.compute._dd_bucket` labels the same quantity off its own module
    constant `DD_BUCKETS = ((0.10, "0-10"), (0.20, "10-20"), (0.35,
    "20-35"))` — itself a hand-typed restatement of `StatsParams.dd_buckets
    = (0.10, 0.20, 0.35)`, with a `"35+"` fallback above the last boundary.
    That module constant is the pre-existing source of the "two lists that
    can silently diverge" problem the task brief calls out; this function
    is the fix on this side of it — it builds the identical strings
    formulaically from the config tuple alone, so the same event never gets
    a different `dd_bucket` depending on whether `jobs/compute.py` or this
    module wrote it. `test_backtest_context.py`'s `TestDdBucketMatchesCompute`
    proves the two stay byte-for-byte identical across representative
    values, the `35+` fallback, and a null input.

    `None`, not a computed bucket, when `dd_52w` itself is null — matches
    `_dd_bucket`'s own null handling and invariant 4 (no fabricated value
    for a missing input).

    `round()` on each boundary times 100, not truncation: `0.10 * 100 ==
    10.000000000000002` in binary floating point, the same guard
    `_pct_suffix` already applies for the same reason.
    """
    if _isnan(dd_52w):
        return None
    dd = float(dd_52w)  # type: ignore[arg-type]
    prev = 0.0
    for threshold in sp.dd_buckets:
        if dd <= threshold:
            return f"{round(prev * 100)}-{round(threshold * 100)}"
        prev = threshold
    return f"{round(prev * 100)}+"


def _era(signal_date: date, sp: StatsParams, splits: SplitParams) -> str:
    """DESIGN §5.2 step 11; ADR 042. Reporting era — a DESIGN §6.10
    stability check across four eras, never a Phase 4 grid dimension
    (DESIGN §6.7: era is excluded from the model's own features precisely
    because it "invites memorizing regime").

    Boundaries come from `sp.era_bounds` (invariant 9): three ISO date
    strings, each the *last* day of an era — `("2014-12-31", "2019-12-31",
    "2023-12-31")` by default, matching ADR 042's four eras (2010-2014,
    2015-2019, 2020-2023, 2024-2026).

    The lower edge of the *first* era is `splits.event_start`'s year, not a
    literal "2010" — the same field that already bounds every event's
    earliest valid `signal_date` (`split_key_for` raises below it), so era 1
    and the event universe start together by construction rather than by a
    second hardcoded year that could drift from `SplitParams` independently.

    ADR 042 closes its last era at a literal "2026" — a real fixed horizon
    when that decision was written, but no config field names where the
    *next* boundary lands, and baking in a year this code will silently
    outlive is exactly what invariant 9 forbids ("a literal that happens to
    match a default elsewhere"). The open era is labelled `"{year}+"`
    instead — the same open-ended convention `_dd_bucket_label`'s `"35+"`
    fallback already uses for "no further boundary configured." This is a
    deliberate, documented deviation from ADR 042's literal wording; see the
    Task 8 report for the full justification.
    """
    bounds = [date.fromisoformat(b) for b in sp.era_bounds]
    lo = date.fromisoformat(splits.event_start).year
    for bound in bounds:
        if signal_date <= bound:
            return f"{lo}-{bound.year}"
        lo = bound.year + 1
    return f"{lo}+"


def _earnings_in_window(days_to_earnings: object, ep: ExitParams) -> bool | None:
    """DESIGN §5.2 step 11; ADR 036: "A 5-day window containing an earnings
    report is contaminated regardless of session."

    `days_to_earnings` (`ind_row["days_to_earnings"]`) is calendar days from
    the signal bar to the next scheduled report, computed once in
    `jobs.compute._merge_days_to_earnings` and always `>= 0` or null (never
    negative — that function masks a passed report to null rather than
    reporting how long ago it happened, since this column only ever answers
    "is one coming").

    The window checked against is `ep.max_hold_days` — the FIXED analysis
    window, never a literal 5 (invariant 9) and never the trade's own
    realized `holding_days`. An earlier version of this function compared
    against `holding_days` on the theory that a position stopped out on day
    2 was never exposed to a day-4 report; that reasoning is correct for
    `gross_ret`/`net_ret`, which really do stop accruing at the realized
    exit, but it is wrong for this flag specifically, because
    `earnings_in_window` sits on the same row as Task 7's `touched_5pct` /
    `day_touched_5pct`, and those are computed by `path_metrics` over the
    FULL `[t+1, t+max_hold_days]` window regardless of when the exit fired
    (DESIGN §5.6) — the two columns describe the same fixed window and are
    read together through `v_events` (DESIGN §1759). Checking against
    `holding_days` produced `earnings_in_window=False` on a row whose
    reachability columns already reflect days 3-5 of an earnings-driven
    move, which is the exact silent inversion the autonomous-run findings
    warned about: a contamination filter answering "clear" when a report
    was days away. Fixed in response to code review Finding 1.

    `None`, not `False`, when `days_to_earnings` is unknown (invariant 4):
    it is null before ~2014 for a large share of events (thin SEC 8-K
    coverage that far back — noted in the task brief's autonomous-run
    findings), and writing `False` there would tell a downstream
    contamination filter "confirmed clear," not "unknown," silently
    admitting exactly the events that could not be checked.
    """
    if _isnan(days_to_earnings):
        return None
    return bool(0 <= float(days_to_earnings) <= float(ep.max_hold_days))  # type: ignore[arg-type]


def enrich_context(
    event: dict,
    ind_row: pd.Series,
    market_row: pd.Series | None,
    sp: StatsParams,
    splits: SplitParams,
    cp: CostParams,
    ep: ExitParams,
) -> dict:
    """DESIGN §5.2 steps 10-12: cost application, context tagging, split
    assignment. Turns one resolved event — the candidate plus its entry
    (Task 5) and exit (Task 6), already merged by the caller into one dict
    with `side`, `signal_date`, `entry_price`, `exit_price`, and
    `holding_days` — into the remaining columns `events`/the backtest output
    table need: `gross_ret`, `net_ret`, `dd_bucket`, `bw_regime`, `era`,
    `earnings_in_window`, `split_key`, `vix_close`, `spx_ret_1d`.

    `cp: CostParams` and `ep: ExitParams` are not in the task brief's
    one-line interface sketch, but `core.costs.apply_costs` requires `cp`
    (invariant 2: apply costs through that one implementation, never a
    second one written here) and `_earnings_in_window` requires `ep` — the
    fixed analysis window (`ep.max_hold_days`) it must compare against,
    never a literal 5 (invariant 9). Nothing else in scope can supply
    either, so both are added as required parameters, the same way
    `resolve_entries` already takes `cp` for entry-side slippage. Flagged in
    the Task 8 report rather than silently deviating from the pinned
    signature.

    `gross_ret` is `core.returns.realized_return(entry_price, exit_price,
    side)` — never hand-computed (invariant 2). It is `NaN` whenever either
    price is `NaN`/`None`, which `realized_return` already handles via
    `_isnan`'s `None`-tolerant check, covering the unresolved-position case
    (`_unresolved_exit`'s `entry_price`/`exit_price` are `NaN`) without a
    special case here.

    `net_ret` routes `gross_ret` through `core.costs.apply_costs` — the one
    cost implementation (invariant 2) — which always subtracts (slippage on
    both legs, commission if configured, borrow on shorts only), so a
    losing trade gets worse on both sides, never better. Null whenever
    `gross_ret` is itself null or `holding_days` is `None` (unresolved
    position): `apply_costs` needs a real holding length for the borrow
    term, and there is nothing to cost a trade that never happened.

    `bw_regime` is `None` — deliberately unimplemented. DESIGN §6.7 lists
    "bandwidth regime" as a continuous *feature*, not a bucketed Phase 4
    cell, and no ADR or config field pins thresholds for one. Inventing a
    bucketing here would fabricate a grid dimension the docs never defined
    (task brief: "a wrong regime label is worse than an honest null"). See
    the Task 8 report for the full reasoning and what a future ADR would
    need to pin before this can be implemented.
    """
    side = event["side"] if isinstance(event["side"], Side) else Side(event["side"])
    entry_price = event.get("entry_price")
    exit_price = event.get("exit_price")
    holding_days = event.get("holding_days")
    signal_date = _as_date(event["signal_date"])

    gross_ret = realized_return(entry_price, exit_price, side)
    net_ret = (
        float("nan")
        if _isnan(gross_ret) or holding_days is None
        else core_costs.apply_costs(gross_ret, side, holding_days, cp, entry_price=entry_price)
    )

    return {
        "gross_ret": gross_ret,
        "net_ret": net_ret,
        "dd_bucket": _dd_bucket_label(ind_row.get("dd_52w"), sp),
        "bw_regime": None,
        "era": _era(signal_date, sp, splits),
        "earnings_in_window": _earnings_in_window(ind_row.get("days_to_earnings"), ep),
        "split_key": split_key_for(signal_date, splits),
        "vix_close": None if market_row is None else market_row.get("vix_close"),
        "spx_ret_1d": None if market_row is None else market_row.get("spx_ret_1d"),
    }
