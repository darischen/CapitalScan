"""Event enrichment — DESIGN §5.2 steps 7-11: entry, exit, path metrics,
costs, context.

Orchestration, not new logic (Session 9 charter). Every hard decision this
module needs already lives in `core/`, covered by the project's five
load-bearing tests: `core.returns.entry_price_for` owns every entry-price
decision (the `TOUCH` gap rule, the `NEXT_OPEN` terminal-bar null, the
hourly interpolation for `TOUCH_5M`/`TOUCH_30M`), `core.returns.mfe_mae`
owns path metrics, and `core.costs` owns slippage and borrow. This module's
job is to slice the right `bar` / `next_bar` / `hourly` window out of the
frames it is handed, apply costs, and shape the results into rows — never
to make a second price decision (CLAUDE.md invariant 2).

This file grows across Session 9:
  - Task 5 (this task): `resolve_entries` — step 7, entry resolution.
  - Task 6: exit resolution — step 8.
  - Task 7: path metrics — step 9.
  - Task 8: cost application and context tagging — steps 10-11.

`research/` owns IO in principle, but nothing in this module touches a
database, a socket, or a clock — `bars` and `hourly` arrive as already-
loaded frames, matching `research/candidates.py`'s convention. Entry does
not depend on any exit parameter, which is what lets the sweep compute
entries once per event and reuse them across all 18 exit configs (DESIGN
§5.9).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from capitalscan.core import costs as core_costs
from capitalscan.core.config import CostParams
from capitalscan.core.returns import entry_price_for
from capitalscan.core.signals import _breach, _isnan
from capitalscan.core.types import Bound, EntryKind, Side


def _as_date(value: object) -> date:
    """Same defensive `Timestamp` -> `date` coercion `research/candidates.py`
    already applies to `signal_date`. It arrives as a plain `date` when it
    comes straight from `scan_candidates` (`core.signals._bar_date` returns
    one), but nothing enforces that a caller building a candidate row by
    hand used the same type.
    """
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value


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
    bar = ticker_bars.loc[signal_date]

    later_dates = ticker_bars.index[ticker_bars.index > signal_date]
    next_bar = ticker_bars.loc[later_dates.min()] if len(later_dates) else None

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
    gapped = None if _isnan(touch_level) else _breach(float(bar["open"]), touch_level, bound)

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
