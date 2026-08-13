"""Capital simulation for the benchmark arms. Pure functions, no IO (DESIGN §3.1).

Session 12 aggregated events that already existed. This module simulates
capital: positions open, close, compound, sit in cash, and interact through
a shared pool. Every failure mode here is a plausible number rather than an
exception, which is why the whole module is index-based and takes no frames
it did not build itself — a `Position` carries its own return path, so
`simulate_portfolio` can be checked by hand on four numbers.

**The portfolio model, stated once.** On each day, capital is split equally
across the positions open that day and any day with no open position sits in
cash at the risk-free rate:

```
r_t = mean over open positions of that position's day-t return   (k_t > 0)
r_t = rf_daily                                                   (k_t = 0)
```

Three properties follow, and all three are load-bearing:

1. Buy-and-hold is deployed every day **by construction**, not by
   measurement, so `frac_deployed = 1.0` is structural (gate item 5).
2. `frac_deployed` is the fraction of days with an open position, which is
   the quantity ADR 012's `capital_efficiency = total_ret / frac_deployed`
   divides by.
3. A random-entry arm firing at the signal's rate lands near buy-and-hold
   scaled by `frac_deployed`, which is the cheapest available check that
   the null is constructed correctly (13.2 acceptance).

The model rebalances to equal weight daily among open positions. It carries
no slippage or commission — `core/costs.py` owns those and the backtest
already applies them to `events.net_ret`; charging them twice would make the
signal arm look worse than the run that produced its entries.

**Price series.** `position_returns` is the only place the two series meet.
Levels, stops, and exit fills are split-adjusted `close` (that is what
`core/exits.py` resolves against); the *return* between two days is
total-return `adj_close`. The bridge is `adj_ratio_t = adj_close_t /
close_t`: a fill at price `f` on day `t` has total-return value
`f * adj_ratio_t`, and on the entry day the ratio cancels, leaving
`close_t / entry_price - 1`.

**Sign convention.** `max_drawdown` is a positive fraction, matching
`core.indicators.drawdown_from_high` and `StatsParams.dd_buckets`.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from capitalscan.core.config import BenchmarkParams
from capitalscan.core.types import Side

# `arm` values written to `benchmarks.arm` (DESIGN §6.10). Named here so the
# writer, the CLI, and the tests cannot drift apart on a string.
ARM_BUY_HOLD = "buy_hold"
ARM_RANDOM = "random"
ARM_SIGNAL = "signal"
ARM_TRIM = "trim"
ARM_DCA_FIXED = "dca_fixed"
ARM_DCA_SIGNAL = "dca_signal"
ARM_DCA_HYBRID = "dca_hybrid"
ARM_DCA_LUMP = "dca_lump"

DCA_ARMS = (ARM_DCA_FIXED, ARM_DCA_SIGNAL, ARM_DCA_HYBRID, ARM_DCA_LUMP)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """One round trip, with its day-by-day return path already resolved.

    `returns` covers `entry_idx .. exit_idx` inclusive, so its length is
    `exit_idx - entry_idx + 1`. Carrying the path on the position rather
    than handing the simulator a price panel is what keeps
    `simulate_portfolio` pure and hand-checkable.
    """

    ticker: str
    side: Side
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    returns: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.exit_idx < self.entry_idx:
            raise ValueError(f"exit_idx {self.exit_idx} precedes entry_idx {self.entry_idx}")
        expected = self.exit_idx - self.entry_idx + 1
        if len(self.returns) != expected:
            raise ValueError(f"expected {expected} returns, got {len(self.returns)}")


@dataclass(frozen=True)
class Trade:
    """A closed position with its realized dollars attached.

    `notional` is the capital the position controlled on its entry day
    (`equity / k`), and `pnl` accumulates its actual daily contribution to
    the curve. The two differ whenever the position's weight changed while
    it was open, which happens every time another position opens or closes
    alongside it.

    Dates are optional because the pure simulator is index-based. The tax
    path needs them and skips a trade that has none rather than guessing.
    """

    ticker: str
    side: Side
    entry_date: date | None
    exit_date: date | None
    entry_price: float
    exit_price: float
    realized_return: float
    notional: float
    pnl: float


@dataclass(frozen=True)
class ArmSimulation:
    """One arm's equity curve plus everything derived from it."""

    equity: pd.Series
    trades: tuple[Trade, ...]
    deployed: tuple[bool, ...]

    @property
    def n_days(self) -> int:
        return len(self.deployed)

    @property
    def frac_deployed(self) -> float:
        if not self.deployed:
            return 0.0
        return float(sum(self.deployed) / len(self.deployed))

    @property
    def total_ret(self) -> float:
        return float(self.equity.iloc[-1] / self.equity.iloc[0] - 1.0)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float | None:
        """None with no trades. 0.0 would read as "never won," a different
        claim, and invariant 4's spirit applies to derived rates too."""
        if not self.trades:
            return None
        return float(sum(1 for t in self.trades if t.realized_return > 0) / len(self.trades))

    @property
    def daily_returns(self) -> pd.Series:
        return self.equity.pct_change().dropna()


@dataclass(frozen=True)
class ArmMetrics:
    """The reported row per DESIGN §6.4, before provenance is stamped on."""

    total_ret: float
    annualized_ret: float
    sharpe: float | None
    max_drawdown: float
    frac_deployed: float
    capital_efficiency: float
    win_rate: float | None
    n_trades: int
    terminal_value: float


@dataclass(frozen=True)
class TrimSimulation:
    """ADR 017's variant. See `simulate_trim` for the state machine."""

    equity: pd.Series
    deployed: tuple[bool, ...]
    n_trims: int
    n_round_trips: int
    avg_days_in_cash: float | None
    n_open_cash_positions: int
    invested_fraction_by_ticker: Mapping[str, float]
    trades: tuple[Trade, ...] = field(default_factory=tuple)

    @property
    def total_ret(self) -> float:
        return float(self.equity.iloc[-1] / self.equity.iloc[0] - 1.0)

    @property
    def frac_deployed(self) -> float:
        if not self.deployed:
            return 0.0
        return float(sum(self.deployed) / len(self.deployed))


@dataclass(frozen=True)
class DcaResult:
    """One DCA variant per DESIGN §6.6."""

    arm: str
    terminal_value: float
    total_ret: float
    irr: float | None
    avg_cost_basis: float | None
    cash_drag: float | None
    capital_deployed: float
    capital_undeployed: float
    n_deployments: int
    # Measured off the variant's own equity curve, not assumed. A
    # signal-triggered variant holding cash until its first signal is not
    # deployed for that stretch, and its drawdown is not the basket's.
    frac_deployed: float = 1.0
    max_drawdown: float = 0.0


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def daily_risk_free(bm: BenchmarkParams) -> float:
    """The per-trading-day rate that compounds to `risk_free_annual`.

    Geometric, not `annual / 252`. The difference is small at 2% and is not
    small at the 5%+ the front end of the curve reached in 2023, and the
    trim arm compounds this over multi-year idle stretches.
    """
    return float((1.0 + bm.risk_free_annual) ** (1.0 / bm.trading_days_per_year) - 1.0)


def max_drawdown(equity: pd.Series) -> float:
    """Deepest peak-to-trough decline, as a **positive** fraction.

    Positive to match `core.indicators.drawdown_from_high` and
    `StatsParams.dd_buckets`, both of which report drawdown positive.
    """
    values = pd.to_numeric(equity, errors="coerce").to_numpy(dtype="float64")
    if values.size == 0:
        return 0.0
    running_peak = np.maximum.accumulate(values)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown = np.where(running_peak > 0, (running_peak - values) / running_peak, 0.0)
    return float(np.nanmax(drawdown))


def annualized_return(total_ret: float, n_days: int, bm: BenchmarkParams) -> float:
    """Geometric annualization over `n_days` trading days.

    Returns 0.0 for an empty window rather than raising: an arm with no days
    has no rate, and every caller here already reports `n_days` alongside.
    """
    if n_days <= 0:
        return 0.0
    growth = 1.0 + total_ret
    if growth <= 0:
        # A total loss has no finite annualized rate. -1.0 is the honest
        # floor: the capital is gone at any horizon.
        return -1.0
    return float(growth ** (bm.trading_days_per_year / n_days) - 1.0)


def sharpe(daily_returns: pd.Series, bm: BenchmarkParams) -> float | None:
    """Annualized Sharpe against `bm.risk_free_annual`.

    None when the series has fewer than two points or zero dispersion. A
    constant return series has no Sharpe — reporting `inf` or substituting
    a value would both make an arm with one flat stretch look decisive.
    """
    values = pd.to_numeric(daily_returns, errors="coerce").dropna().to_numpy(dtype="float64")
    if values.size < 2:
        return None
    excess = values - daily_risk_free(bm)
    sigma = float(np.std(excess, ddof=1))
    if sigma <= 0:
        return None
    return float(np.mean(excess) / sigma * np.sqrt(bm.trading_days_per_year))


def capital_efficiency(total_ret: float, frac_deployed: float) -> float:
    """ADR 012's `total_ret / frac_deployed`.

    0.0 when nothing was ever deployed (13.1 acceptance). An arm that held
    cash all window earned no return on deployed capital because it deployed
    none, and 0/0 is that statement, not a division error.
    """
    if frac_deployed <= 0:
        return 0.0
    return float(total_ret / frac_deployed)


def arm_metrics(sim: ArmSimulation, bm: BenchmarkParams) -> ArmMetrics:
    """Everything DESIGN §6.4 reports per arm, from one equity curve."""
    total = sim.total_ret
    frac = sim.frac_deployed
    return ArmMetrics(
        total_ret=total,
        annualized_ret=annualized_return(total, sim.n_days, bm),
        sharpe=sharpe(sim.daily_returns, bm),
        max_drawdown=max_drawdown(sim.equity),
        frac_deployed=frac,
        capital_efficiency=capital_efficiency(total, frac),
        win_rate=sim.win_rate,
        n_trades=sim.n_trades,
        terminal_value=float(bm.initial_capital * sim.equity.iloc[-1] / sim.equity.iloc[0]),
    )


# --------------------------------------------------------------------------
# Position return paths
# --------------------------------------------------------------------------


def position_returns(
    close: np.ndarray,
    adj_close: np.ndarray,
    entry_idx: int,
    exit_idx: int,
    entry_price: float,
    exit_price: float,
    side: Side,
) -> tuple[float, ...]:
    """Day-by-day total return of one position, entry day through exit day.

    The only place the two price series meet (see the module docstring).
    `close` and `adj_close` are the same ticker's aligned daily arrays;
    `entry_price` and `exit_price` are actual fills on the split-adjusted
    series, which is what `core/exits.py` resolves against.

    ```
    entry day  : close[e] / entry_price - 1          (adj_ratio cancels)
    middle day : adj_close[i] / adj_close[i-1] - 1
    exit day   : exit_price * adj_ratio[x] / adj_close[x-1] - 1
    same day   : exit_price / entry_price - 1
    ```

    A short is the exact negation of the price return, so the whole path is
    negated rather than re-derived (`core/baselines.py` takes the same route
    for the short baseline).
    """
    if exit_idx < entry_idx:
        raise ValueError(f"exit_idx {exit_idx} precedes entry_idx {entry_idx}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be positive, got {entry_price}")

    sign = 1.0 if side is Side.LONG else -1.0

    if entry_idx == exit_idx:
        return (sign * (exit_price / entry_price - 1.0),)

    out: list[float] = [sign * (float(close[entry_idx]) / entry_price - 1.0)]
    for i in range(entry_idx + 1, exit_idx):
        out.append(sign * (float(adj_close[i]) / float(adj_close[i - 1]) - 1.0))
    adj_ratio = float(adj_close[exit_idx]) / float(close[exit_idx])
    out.append(sign * (exit_price * adj_ratio / float(adj_close[exit_idx - 1]) - 1.0))
    return tuple(out)


# --------------------------------------------------------------------------
# Portfolio simulation
# --------------------------------------------------------------------------


def simulate_portfolio(
    n_days: int,
    positions: Sequence[Position],
    bm: BenchmarkParams,
    dates: Sequence[date] | None = None,
) -> ArmSimulation:
    """Equal-weight the open positions each day, cash at `rf` otherwise.

    The single simulator behind the signal arm and all 200 replications of
    the random-entry null. 13.2's acceptance requires the two share exit
    handling; sharing the whole simulator is a stronger version of the same
    guarantee.

    `pnl` is accumulated day by day rather than derived from the realized
    return, because a position's weight changes whenever another opens or
    closes beside it. The two agree only for a position that was alone for
    its whole life, and the accumulated version is the one that sums to the
    curve.
    """
    if n_days <= 0:
        raise ValueError(f"n_days must be positive, got {n_days}")
    if dates is not None and len(dates) != n_days:
        raise ValueError(f"dates has {len(dates)} entries for {n_days} days")

    opens: dict[int, list[int]] = {}
    for j, position in enumerate(positions):
        if position.entry_idx < 0 or position.exit_idx >= n_days:
            raise ValueError(
                f"position {position.ticker} spans {position.entry_idx}..{position.exit_idx}, "
                f"outside the {n_days}-day calendar"
            )
        opens.setdefault(position.entry_idx, []).append(j)

    rf = daily_risk_free(bm)
    equity = np.empty(n_days + 1, dtype="float64")
    equity[0] = 1.0
    deployed = np.zeros(n_days, dtype=bool)
    pnl = np.zeros(len(positions), dtype="float64")
    notional = np.zeros(len(positions), dtype="float64")

    live: list[int] = []
    for i in range(n_days):
        live = [j for j in live if positions[j].exit_idx >= i]
        live.extend(opens.get(i, []))
        k = len(live)
        start = equity[i]

        if k == 0:
            equity[i + 1] = start * (1.0 + rf)
            continue

        deployed[i] = True
        weight = start / k
        day_return = 0.0
        for j in live:
            position = positions[j]
            r = position.returns[i - position.entry_idx]
            day_return += r / k
            pnl[j] += weight * r
            if position.entry_idx == i:
                notional[j] = weight
        equity[i + 1] = start * (1.0 + day_return)

    index = pd.RangeIndex(n_days + 1)
    trades = tuple(
        Trade(
            ticker=position.ticker,
            side=position.side,
            entry_date=None if dates is None else dates[position.entry_idx],
            exit_date=None if dates is None else dates[position.exit_idx],
            entry_price=position.entry_price,
            exit_price=position.exit_price,
            realized_return=float(np.prod([1.0 + r for r in position.returns]) - 1.0),
            notional=float(notional[j] * bm.initial_capital),
            pnl=float(pnl[j] * bm.initial_capital),
        )
        for j, position in enumerate(positions)
    )
    return ArmSimulation(
        equity=pd.Series(equity, index=index),
        trades=trades,
        deployed=tuple(bool(x) for x in deployed),
    )


# --------------------------------------------------------------------------
# The share book: buy-and-hold (13.1) and trim-and-redeploy (13.3)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldingsSimulation:
    """A share book run forward: buy-and-hold, optionally with a trim overlay."""

    equity: pd.Series
    trades: tuple[Trade, ...]
    deployed: tuple[bool, ...]
    n_trims: int
    n_round_trips: int
    avg_days_in_cash: float | None
    n_open_cash_positions: int
    invested_fraction_by_ticker: Mapping[str, float]


def simulate_holdings(
    prices: Mapping[str, np.ndarray],
    members: Sequence[Sequence[str]],
    bm: BenchmarkParams,
    dates: Sequence[date] | None = None,
    signals: Mapping[str, Sequence[tuple[int, str]]] | None = None,
) -> HoldingsSimulation:
    """Equal-weight the trade universe and hold, with an optional trim overlay.

    **One simulator for two arms.** ADR 017 measures trim-and-redeploy
    "against buy-and-hold on the same names," and the only way that
    comparison means anything is if the no-trim case *is* buy-and-hold. Two
    implementations agreeing on a static-membership fixture and diverging on
    real quarterly membership is exactly the plausible-wrong-number failure
    this module exists to prevent — it was measured on the train split at
    +725% against +413%, entirely from the two arms holding different books.

    **Rebalancing.** Day 0 and every day the membership changes are
    rebalance days; DESIGN §6.4's "equal-weight the trade universe,
    rebalance quarterly on universe changes," and `universe.as_of` is
    quarterly. On a rebalance the whole book is re-weighted to `equity / k`
    across the current priced members. Between rebalances the share counts
    are fixed, so weights drift with prices — which is what holding is.

    **A name that stops trading is valued at its last observed price** and
    sold at the next rebalance. `tickers.delisted_on` exists and a position
    in a delisted name must resolve rather than silently persist; carrying
    the last price means it neither gains nor loses while untraded, and the
    rebalance is where it actually leaves the book. It is never filled or
    interpolated (invariant 4).

    **The trim overlay.** `held_frac[ticker]` is the fraction of that
    ticker's slot currently invested; the rest sits in that ticker's cash at
    the risk-free rate. A trim multiplies it by `1 - trim_fraction`, which
    is what makes a second trim take the fraction of what **remains** (two
    trims leave 0.64, not 0.60). A redeploy resets it to 1.0 and closes the
    whole outstanding cash position, so two trims followed by one redeploy
    is **one** round trip. `held_frac` survives a rebalance, so trimming is
    an overlay on the weights rather than something the next rebalance
    silently undoes.

    With `signals=None` every `held_frac` stays 1.0 and this is buy-and-hold
    exactly.
    """
    n_days = len(members)
    if n_days == 0:
        raise ValueError("members must cover at least one day")
    if dates is not None and len(dates) != n_days:
        raise ValueError(f"dates has {len(dates)} entries for {n_days} days")

    rf = daily_risk_free(bm)
    by_day: dict[int, list[tuple[str, str]]] = {}
    for ticker, entries in (signals or {}).items():
        for idx, kind in entries:
            if kind not in ("trim", "redeploy"):
                raise ValueError(f"unknown trim signal kind {kind!r}")
            by_day.setdefault(int(idx), []).append((ticker, kind))

    shares: dict[str, float] = {}
    cash: dict[str, float] = {}
    held_frac: dict[str, float] = {}
    last_price: dict[str, float] = {}
    first_trim: dict[str, int] = {}
    stints: dict[str, dict[str, float]] = {}
    min_held_frac: dict[str, float] = {}

    unallocated = 1.0
    equity = np.empty(n_days + 1, dtype="float64")
    equity[0] = 1.0
    deployed = np.zeros(n_days, dtype=bool)
    trades: list[Trade] = []
    n_trims = 0
    n_round_trips = 0
    days_in_cash: list[int] = []

    def _price(ticker: str, i: int) -> float:
        series = prices.get(ticker)
        if series is None or i >= len(series):
            return float("nan")
        return float(series[i])

    def _value(ticker: str) -> float:
        """Position value at the last price the market actually printed."""
        return shares.get(ticker, 0.0) * last_price.get(ticker, 0.0)

    for i in range(n_days):
        if i > 0:
            unallocated *= 1.0 + rf
            for ticker in cash:
                cash[ticker] *= 1.0 + rf

        for ticker in members[i]:
            price = _price(ticker, i)
            if not np.isnan(price) and price > 0:
                last_price[ticker] = price

        # --- price the book forward, and attribute the move to its stint ---
        if i > 0:
            for ticker, held in shares.items():
                if held <= 0:
                    continue
                price = _price(ticker, i)
                prior = _price(ticker, i - 1)
                if np.isnan(price) or np.isnan(prior) or prior <= 0:
                    continue
                stints[ticker]["pnl"] += held * (price - prior)

        # --- rebalance on membership change (and on day 0) ---
        if i == 0 or tuple(members[i]) != tuple(members[i - 1]):
            current = [t for t in members[i] if last_price.get(t, 0.0) > 0]
            book = sum(_value(t) for t in shares) + sum(cash.values()) + unallocated

            for ticker in list(shares):
                if ticker not in current:
                    _close_stint(trades, stints, ticker, i, last_price.get(ticker, 0.0), bm, dates)
                    shares.pop(ticker, None)
                    cash.pop(ticker, None)
                    held_frac.pop(ticker, None)
                    first_trim.pop(ticker, None)

            if current:
                slot = book / len(current)
                for ticker in current:
                    frac = held_frac.get(ticker, 1.0)
                    price = last_price[ticker]
                    if ticker not in stints:
                        stints[ticker] = {
                            "entry_idx": float(i),
                            "entry_price": price,
                            "notional": slot,
                            "pnl": 0.0,
                        }
                        min_held_frac.setdefault(ticker, 1.0)
                    shares[ticker] = frac * slot / price
                    cash[ticker] = (1.0 - frac) * slot
                    held_frac[ticker] = frac
                unallocated = 0.0
            else:
                shares.clear()
                cash.clear()
                unallocated = book

        # --- trim and redeploy, at this day's price ---
        for ticker, kind in sorted(by_day.get(i, [])):
            if ticker not in shares:
                continue
            price = _price(ticker, i)
            if np.isnan(price) or price <= 0:
                continue
            if kind == "trim":
                sold = shares[ticker] * bm.trim_fraction
                if sold <= 0:
                    continue
                shares[ticker] -= sold
                cash[ticker] = cash.get(ticker, 0.0) + sold * price
                held_frac[ticker] *= 1.0 - bm.trim_fraction
                min_held_frac[ticker] = min(min_held_frac.get(ticker, 1.0), held_frac[ticker])
                n_trims += 1
                first_trim.setdefault(ticker, i)
            else:
                parked = cash.get(ticker, 0.0)
                if parked <= 0:
                    continue
                shares[ticker] += parked / price
                cash[ticker] = 0.0
                held_frac[ticker] = 1.0
                opened = first_trim.pop(ticker, None)
                if opened is not None:
                    days_in_cash.append(i - opened)
                    n_round_trips += 1

        invested = sum(_value(t) for t in shares)
        equity[i + 1] = invested + sum(cash.values()) + unallocated
        deployed[i] = invested > 0

    for ticker in list(shares):
        _close_stint(trades, stints, ticker, n_days - 1, last_price.get(ticker, 0.0), bm, dates)

    return HoldingsSimulation(
        equity=pd.Series(equity, index=pd.RangeIndex(n_days + 1)),
        trades=tuple(sorted(trades, key=lambda t: (t.ticker, str(t.entry_date)))),
        deployed=tuple(bool(x) for x in deployed),
        n_trims=n_trims,
        n_round_trips=n_round_trips,
        avg_days_in_cash=float(np.mean(days_in_cash)) if days_in_cash else None,
        n_open_cash_positions=len(first_trim),
        invested_fraction_by_ticker=dict(min_held_frac),
    )


def _close_stint(
    trades: list[Trade],
    stints: dict[str, dict[str, float]],
    ticker: str,
    exit_idx: int,
    exit_price: float,
    bm: BenchmarkParams,
    dates: Sequence[date] | None,
) -> None:
    """Book one uninterrupted holding stint as a `Trade`.

    A stint runs from the rebalance that first bought the name to the one
    that sold it, spanning any number of intermediate re-weightings. That
    keeps `n_trades` and `win_rate` interpretable — a quarterly rebalance
    resizing a position is not a round trip — and gives ADR 032's tax pass a
    dollar `pnl` on a real disposal.

    **Stated gap:** the size adjustments a rebalance makes to a *retained*
    name are real partial disposals and are not modeled as taxable events.
    ADR 032 is Provisional; this understates realized gains in a stated
    direction rather than silently.
    """
    stint = stints.pop(ticker, None)
    if stint is None:
        return
    entry_price = float(stint["entry_price"])
    realized = (
        0.0
        if entry_price <= 0 or np.isnan(exit_price) or exit_price <= 0
        else exit_price / entry_price - 1.0
    )
    entry_idx = int(stint["entry_idx"])
    trades.append(
        Trade(
            ticker=ticker,
            side=Side.LONG,
            entry_date=None if dates is None else dates[entry_idx],
            exit_date=None if dates is None else dates[max(exit_idx, entry_idx)],
            entry_price=entry_price,
            exit_price=float(exit_price),
            realized_return=float(realized),
            notional=float(stint["notional"] * bm.initial_capital),
            pnl=float(stint["pnl"] * bm.initial_capital),
        )
    )


def simulate_buy_hold(
    prices: Mapping[str, np.ndarray],
    members: Sequence[Sequence[str]],
    bm: BenchmarkParams,
    dates: Sequence[date] | None = None,
) -> ArmSimulation:
    """Arm 1: equal-weight the trade universe and hold throughout.

    `simulate_holdings` with no trim overlay. `frac_deployed` is 1.0 for any
    window whose membership is non-empty throughout, which is what gate item
    5 asserts — and why `research.benchmarks.load_window` clips the window's
    start to the first day the trade universe has members, rather than
    letting an empty pre-universe stretch turn a structural 1.0 into a
    measured 0.98.
    """
    run = simulate_holdings(prices, members, bm, dates)
    return ArmSimulation(equity=run.equity, trades=run.trades, deployed=run.deployed)


def simulate_trim(
    prices: Mapping[str, np.ndarray],
    members: Sequence[Sequence[str]],
    signals: Mapping[str, Sequence[tuple[int, str]]],
    bm: BenchmarkParams,
    dates: Sequence[date] | None = None,
) -> TrimSimulation:
    """ADR 017's variant: trim into strength, redeploy on the next weakness.

    The same share book buy-and-hold runs, with the trim overlay switched
    on, so the difference between the two arms is the overlay and nothing
    else. See `simulate_holdings` for the three rules the 13.3 brief asks to
    be stated explicitly.
    """
    run = simulate_holdings(prices, members, bm, dates, signals)
    return TrimSimulation(
        equity=run.equity,
        deployed=run.deployed,
        n_trims=run.n_trims,
        n_round_trips=run.n_round_trips,
        avg_days_in_cash=run.avg_days_in_cash,
        n_open_cash_positions=run.n_open_cash_positions,
        invested_fraction_by_ticker=run.invested_fraction_by_ticker,
        trades=run.trades,
    )


# --------------------------------------------------------------------------
# DCA (13.4)
# --------------------------------------------------------------------------


def simulate_dca_family(
    basket_index: np.ndarray,
    month_starts: Sequence[int],
    signal_days: Sequence[int],
    n_expected_signals: int,
    bm: BenchmarkParams,
) -> dict[str, DcaResult]:
    """All four DESIGN §6.6 variants over one equal-weight basket index.

    `basket_index` is the buy-and-hold arm's own equity curve, so every
    variant accumulates units of the same thing. That is what makes
    `dca_lump` and buy-and-hold agree by construction on a single ticker
    (13.4 acceptance) rather than by coincidence.

    | Variant | Rule |
    |---|---|
    | `dca_fixed` | `C / dca_tranches` on each month start |
    | `dca_signal` | `C / N` on each signal day, `N` the historical count |
    | `dca_hybrid` | monthly base, doubled on a month start that is also a signal day |
    | `dca_lump` | `C` on day one |

    Two rules that decide the numbers:

    - **Idle DCA cash earns nothing.** DESIGN §6.5 puts the risk-free rate on
      the trim arm's idle cash; §6.6 does not, and `cash_drag` is the whole
      point of this comparison — crediting the cash would net out part of the
      quantity being measured.
    - **Leftover deploys on the final day,** and the leftover *before* that
      sweep is `capital_undeployed`. Every variant therefore deploys exactly
      `C`, and underfiring shows up as a number rather than as a smaller
      total.

    All four are computed together because `cash_drag` is defined against
    `dca_lump`, and computing lump separately is how the two would drift.
    """
    values = np.asarray(basket_index, dtype="float64")
    if values.size == 0:
        raise ValueError("basket_index must hold at least one day")
    if np.any(values <= 0):
        raise ValueError("basket_index must be strictly positive")
    if n_expected_signals < 0:
        raise ValueError(f"n_expected_signals must be non-negative, got {n_expected_signals}")

    capital = bm.initial_capital
    last = values.size - 1
    months = sorted({int(i) for i in month_starts})
    signals = sorted({int(i) for i in signal_days})
    signal_set = set(signals)

    monthly = capital / bm.dca_tranches
    per_signal = capital / n_expected_signals if n_expected_signals > 0 else 0.0

    schedules: dict[str, list[tuple[int, float]]] = {
        ARM_DCA_FIXED: [(i, monthly) for i in months],
        ARM_DCA_SIGNAL: [(i, per_signal) for i in signals],
        ARM_DCA_HYBRID: [(i, monthly * 2.0 if i in signal_set else monthly) for i in months],
        ARM_DCA_LUMP: [(0, capital)],
    }

    results = {
        arm: _run_dca(arm, values, schedule, capital, last, bm)
        for arm, schedule in schedules.items()
    }
    lump_ret = results[ARM_DCA_LUMP].total_ret
    return {
        arm: DcaResult(**{**result.__dict__, "cash_drag": lump_ret - result.total_ret})
        for arm, result in results.items()
    }


def _run_dca(
    arm: str,
    values: np.ndarray,
    schedule: Sequence[tuple[int, float]],
    capital: float,
    last: int,
    bm: BenchmarkParams,
) -> DcaResult:
    """One variant. `cash_drag` is filled in by the caller, which is the only
    place `dca_lump` is in scope.

    The equity curve is built alongside the schedule rather than assumed:
    `dca_signal` may hold cash for a long stretch before its first signal,
    so its `frac_deployed` and `max_drawdown` are measurements, not the 1.0
    and basket-drawdown that a fully-invested variant would show.
    """
    remaining = capital
    units = 0.0
    spent = 0.0
    flows: list[tuple[int, float]] = []
    n_deployments = 0

    by_day: dict[int, float] = {}
    for day, amount in sorted(schedule):
        if remaining <= 0:
            break
        tranche = min(amount, remaining)
        if tranche <= 0:
            continue
        by_day[day] = by_day.get(day, 0.0) + tranche
        remaining -= tranche

    undeployed = remaining
    if remaining > 0:
        by_day[last] = by_day.get(last, 0.0) + remaining
        remaining = 0.0

    cash = capital
    equity = np.empty(last + 1, dtype="float64")
    deployed = np.zeros(last + 1, dtype=bool)
    for i in range(last + 1):
        deposit = by_day.get(i)
        if deposit is not None:
            units += deposit / float(values[i])
            cash -= deposit
            spent += deposit
            flows.append((i, -deposit))
            n_deployments += 1
        equity[i] = units * float(values[i]) + cash
        deployed[i] = units > 0

    terminal = units * float(values[last])
    flows.append((last, terminal))
    return DcaResult(
        arm=arm,
        terminal_value=float(terminal),
        total_ret=float(terminal / capital - 1.0),
        irr=irr_from_day_offsets(flows, bm),
        avg_cost_basis=float(spent / units) if units > 0 else None,
        cash_drag=None,
        capital_deployed=float(spent),
        capital_undeployed=float(undeployed),
        n_deployments=n_deployments,
        frac_deployed=float(deployed.sum() / len(deployed)),
        max_drawdown=max_drawdown(pd.Series(equity)),
    )


# --------------------------------------------------------------------------
# IRR (13.4)
# --------------------------------------------------------------------------


def irr(flows: Sequence[tuple[date, float]], bm: BenchmarkParams) -> float | None:
    """Money-weighted rate solving `sum(amount / (1+r)^t) = 0`.

    `t` is in **calendar** years (`irr_days_per_year = 365`), because an IRR
    discounts elapsed time rather than trading sessions. Using 252 would
    inflate every rate by roughly 45%.

    None when the flows carry no sign change, since there is no root to
    find. Returning a bracket endpoint would put a number in `benchmarks.irr`
    that no cash flow implies.
    """
    if len(flows) < 2:
        return None
    origin = min(when for when, _ in flows)
    offsets = [((when - origin).days / bm.irr_days_per_year, amount) for when, amount in flows]
    return _solve_irr(offsets, bm)


def irr_from_day_offsets(flows: Sequence[tuple[int, float]], bm: BenchmarkParams) -> float | None:
    """`irr` for flows indexed by trading-day offset rather than by date.

    The trading-day offset is converted to calendar years through
    `trading_days_per_year`, so the two entry points agree on what a year is
    even though one counts sessions and the other counts days.
    """
    if len(flows) < 2:
        return None
    origin = min(day for day, _ in flows)
    offsets = [((day - origin) / bm.trading_days_per_year, amount) for day, amount in flows]
    return _solve_irr(offsets, bm)


def _solve_irr(offsets: Sequence[tuple[float, float]], bm: BenchmarkParams) -> float | None:
    """Bisection on NPV over `(-1, irr_max_rate]`.

    Bisection rather than Newton: NPV is monotone on this bracket for the
    single-sign-change flows DCA produces, and a bracketed method cannot
    wander off to a second root or diverge, which a derivative method can on
    a flow sequence with a near-flat NPV.
    """
    amounts = [amount for _, amount in offsets]
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return None

    def npv(rate: float) -> float:
        return float(sum(amount / (1.0 + rate) ** t for t, amount in offsets))

    lo, hi = -1.0 + bm.irr_tolerance, bm.irr_max_rate
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None

    for _ in range(bm.irr_max_iterations):
        mid = 0.5 * (lo + hi)
        f_mid = npv(mid)
        if abs(f_mid) < bm.irr_tolerance or (hi - lo) < bm.irr_tolerance:
            return float(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return float(0.5 * (lo + hi))


# --------------------------------------------------------------------------
# Tax and wash sales (13.5, ADR 032)
# --------------------------------------------------------------------------


def wash_sale_flags(
    trades: Sequence[Trade],
    purchases: Sequence[tuple[str, date]],
    bm: BenchmarkParams,
) -> tuple[tuple[bool, ...], bool]:
    """Which loss-closing trades fall inside a wash-sale window.

    ADR 032: a loss triggers disallowance when the same ticker was bought
    within `wash_sale_window_days` **either side** of the disposal, counting
    **calendar** days. `purchases` must include the core position and any
    dividend reinvestment, not only the sleeve's own entries — a core
    holding is what turns most of these into wash sales, and the sleeve
    alone would report none.

    A trade's **own entry never flags it.** The rule is about replacement
    shares; a five-day hold would otherwise flag every losing trade in the
    system, which would be a statement about the holding period. Purchases
    of the same ticker dated exactly on the trade's own entry day are
    therefore excluded. Known limit: a core purchase that happens to land on
    that same day is excluded with it.

    A trade with no dates cannot be evaluated and is reported unflagged
    rather than guessed.

    Purchases are bucketed by ticker and sorted so the window lookup is a
    pair of bisections rather than a scan. A full-universe arm carries
    thousands of trades and thousands of purchases, and the quadratic form
    of this dominates a 200-replication run.
    """
    by_ticker: dict[str, list[date]] = {}
    for ticker, when in purchases:
        by_ticker.setdefault(ticker, []).append(when)
    for dates in by_ticker.values():
        dates.sort()

    window = timedelta(days=bm.wash_sale_window_days)
    flags: list[bool] = []
    for trade in trades:
        if trade.pnl >= 0 or trade.exit_date is None:
            flags.append(False)
            continue
        candidates = by_ticker.get(trade.ticker)
        if not candidates:
            flags.append(False)
            continue
        lo = bisect_left(candidates, trade.exit_date - window)
        hi = bisect_right(candidates, trade.exit_date + window)
        flags.append(any(candidates[i] != trade.entry_date for i in range(lo, hi)))
    return tuple(flags), any(flags)


def tax_summary(
    trades: Sequence[Trade],
    purchases: Sequence[tuple[str, date]],
    pre_tax_ret: float,
    bm: BenchmarkParams,
) -> tuple[float, float, bool]:
    """`(pre_tax_ret, post_tax_ret, wash_sale_flagged)` for one arm.

    **Taxed per calendar year, and a wash-sale loss is deferred rather than
    destroyed.** Both parts matter and the naive version is badly wrong on a
    multi-year window:

    - Pooling every year into one net figure would let a 2021 loss offset a
      2011 gain, which no tax year permits. Each year nets its own gains
      against its own allowed losses and pays on the excess; a net-loss year
      owes nothing and is not refunded (no carry-forward is modeled).
    - A wash-sale disallowance **defers** the loss into the replacement
      lot's basis; it does not delete it. Modeling it as permanently
      disallowed produced `post_tax_ret = -354%` against `pre_tax_ret =
      +109%` on the train split — a tax bill several times the account,
      from twelve years of losses being thrown away one year at a time.
      Here a flagged loss moves into the **next** year's allowed pool,
      which is a timing cost and is what the rule actually is.

    The flag still has a real numeric consequence, which 13.5's acceptance
    requires: deferring a loss out of a profitable year into a lossmaking
    one means it never offsets anything.

    **Stated gaps.** Deferred losses still outstanding in the final year are
    lost rather than carried past the window. `post_tax_ret` subtracts
    nominal cumulative tax over `initial_capital`, so it ignores the
    compounding cost of paying tax early — an understatement of the drag, in
    a stated direction. Federal short-term rates only: no state tax, no
    NIIT, no bracket progression.

    **This is a modeling assumption for evaluation, not tax advice.** ADR 032
    is Provisional and stays Provisional.
    """
    flags, any_flag = wash_sale_flags(trades, purchases, bm)

    gains: dict[int, float] = {}
    allowed: dict[int, float] = {}
    deferred: dict[int, float] = {}
    for trade, flagged in zip(trades, flags):
        if trade.exit_date is None or np.isnan(trade.pnl):
            continue
        year = trade.exit_date.year
        if trade.pnl > 0:
            gains[year] = gains.get(year, 0.0) + trade.pnl
        elif trade.pnl < 0:
            bucket = deferred if flagged else allowed
            bucket[year] = bucket.get(year, 0.0) + (-trade.pnl)

    tax = 0.0
    carried = 0.0
    for year in sorted(set(gains) | set(allowed) | set(deferred)):
        offset = allowed.get(year, 0.0) + carried
        tax += max(0.0, gains.get(year, 0.0) - offset) * bm.short_term_tax_rate
        carried = deferred.get(year, 0.0)

    return float(pre_tax_ret), float(pre_tax_ret - tax / bm.initial_capital), bool(any_flag)


# --------------------------------------------------------------------------
# The null distribution (13.2, ADR 061)
# --------------------------------------------------------------------------


def null_percentile(values: Sequence[float], percentile: float) -> float | None:
    """A percentile of the stored null distribution.

    13.2's acceptance requires this be read off the rows written to
    `benchmarks`, never an in-memory list that could diverge from them, so
    this takes a plain sequence and the caller is responsible for having
    queried it back.
    """
    clean = [float(v) for v in values if v is not None and not np.isnan(float(v))]
    if not clean:
        return None
    return float(np.percentile(np.array(clean, dtype="float64"), percentile))


def exceeds_null(
    signal_value: float, null_values: Sequence[float], percentile: float
) -> bool | None:
    """ADR 061's criterion: signal performance above the 97.5th percentile.

    None when the null is empty — "no distribution to test against" is not
    the same claim as "did not clear it."
    """
    threshold = null_percentile(null_values, percentile)
    if threshold is None:
        return None
    return bool(signal_value > threshold)
