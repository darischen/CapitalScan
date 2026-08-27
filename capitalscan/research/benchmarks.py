"""The eight benchmark arms (Session 13, DESIGN §6.4-§6.6 and §6.10).

`core/arms.py` owns the capital arithmetic. This module owns which rows:
which trading days form the window, who is in the universe on each of them,
which events become entries, and how a random entry is drawn so the null is
reproducible. That split is deliberate for the same reason
`research/baselines.py` splits from `core/baselines.py` — the arithmetic is
checkable by hand and the windowing is where a plausible wrong number comes
from.

**One universe, one calendar, one date range.** ADR 012's "identical
universe and dates" is the entire basis of the comparison, so every arm is
built off a single `ArmWindow`: the same trading calendar, the same
forward-filled trade-universe membership, the same first and last day. A
signal arm that starts on its first signal measures a different window than
a buy-and-hold arm starting on day one, and the difference is invisible in
the output.

**One exit implementation.** Every arm that opens a position routes through
`research.enrich.resolve_exit_for_entry`, which is the pinned wrapper around
`core.exits.resolve_exit` (invariant 2). The random-entry null isolates
entry *timing*; any difference in exit handling contaminates it, so the null
does not reimplement exits, it calls the same function the signal arm calls.

**The signal arm is the long side.** ADR 058 computes both sides and
surfaces only long, and ADR 017 makes the upper-band and overbought signals
drive the **trim** arm rather than a naive short. So the three-arm
comparison runs long entries and the short-side signal's role in this
session is `arm = 'trim'`. Nothing here writes a naive-short control.

**Seeding.** The null's rng is derived from `config_hash` and the
replication number (ADR 061). Seeding from a wall clock breaks
reproducibility and seeding from a constant makes every config share one
null. Both run without complaint, which is why `_null_rng` is one function
with one docstring rather than an inline `default_rng` call per caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, cast

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core import arms as core_arms
from capitalscan.core.arms import (
    ARM_BUY_HOLD,
    ARM_RANDOM,
    ARM_SIGNAL,
    ARM_TRIM,
    ArmMetrics,
    Position,
    Trade,
)
from capitalscan.core.config import BenchmarkParams, Config, ReportingParams
from capitalscan.core.types import Side
from capitalscan.jobs import db_io
from capitalscan.research.cell_stats import (
    GRID_CLUSTER_HEADS_ONLY,
    GRID_ENTRY_KIND,
    assign_breadth_tercile,
)
from capitalscan.research.enrich import resolve_exit_for_entry

BENCHMARK_COLUMNS = [
    "run_id",
    "config_hash",
    "arm",
    "replication",
    "split_key",
    "era",
    "total_ret",
    "annualized_ret",
    "sharpe",
    "max_drawdown",
    "frac_deployed",
    "capital_efficiency",
    "win_rate",
    "n_trades",
    "terminal_value",
    "irr",
    "avg_cost_basis",
    "cash_drag",
    "capital_undeployed",
    "n_round_trips",
    "avg_days_in_cash",
    "pre_tax_ret",
    "post_tax_ret",
    "wash_sale_flagged",
    "computed_at",
    "git_sha",
]

# The `era` marker for 13.6's high-breadth subset rows. `era`, not
# `split_key`: `split_key` names which dates an event was allowed to be
# measured on and the subset shares its parent's dates exactly, so
# overloading it would make the firewall query ambiguous. `benchmarks.era`
# has no constraint and no other writer.
POOLED_ERA = "pooled"
HIGH_BREADTH_ERA = "breadth_high"
HIGH_BREADTH_TERCILE = "high"

# DESIGN §6.5's trim trigger and its redeploy counterpart.
TRIM_SIGNAL_TYPE = "confluence_high"
REDEPLOY_SIGNAL_TYPE = "confluence_low"


# ==========================================================================
# The window: one calendar, one membership, one date range
# ==========================================================================


@dataclass(frozen=True)
class ArmWindow:
    """The shared calendar and universe every arm is measured on.

    `members[i]` is who is held **during** day `i`, forward-filled from
    `universe.as_of`. Membership is quarterly, so a ticker leaving the trade
    universe stays until the next `as_of` — which is what "sold at the next
    rebalance, not immediately" means (13.1 rule).
    """

    split_key: str
    dates: tuple[date, ...]
    members: tuple[tuple[str, ...], ...]
    delisted_on: Mapping[str, date]

    @property
    def n_days(self) -> int:
        return len(self.dates)

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted({t for day in self.members for t in day}))

    def index_of(self) -> dict[date, int]:
        return {d: i for i, d in enumerate(self.dates)}


def split_bounds(cfg: Config, split_key: str) -> tuple[date, date]:
    """First and last calendar day of a split (ADR 019).

    The holdout end is open — it runs to whatever data exists — so it is
    returned as today's date and the calendar query bounds it in practice.
    """
    splits = cfg.splits
    if split_key == "train":
        return date.fromisoformat(splits.event_start), date.fromisoformat(splits.train_end)
    if split_key == "validate":
        start = date.fromisoformat(splits.train_end)
        return _next_day(start), date.fromisoformat(splits.validate_end)
    if split_key == "holdout":
        return _next_day(date.fromisoformat(splits.validate_end)), date.today()
    raise ValueError(f"unknown split_key {split_key!r}")


def _next_day(when: date) -> date:
    return date.fromordinal(when.toordinal() + 1)


def load_window(engine: Engine, cfg: Config, split_key: str) -> ArmWindow:
    """Build the shared calendar and daily trade-universe membership.

    The calendar is the distinct daily bar dates inside the split's bounds,
    not `trading_days`: an arm can only be measured on a day some price
    exists for, and taking the union of bar dates guarantees every ticker's
    own dates are a subset of it, which is what lets a position's local
    span map cleanly onto the shared one.
    """
    start, end = split_bounds(cfg, split_key)
    with engine.connect() as conn:
        calendar = (
            conn.execute(
                text(
                    "SELECT DISTINCT ts::date AS d FROM bars "
                    "WHERE interval = '1d' AND ts >= :start AND ts <= :end ORDER BY d"
                ),
                {"start": start, "end": end},
            )
            .scalars()
            .all()
        )
        universe = pd.read_sql(
            text(
                "SELECT ticker, as_of FROM universe WHERE in_trade AND as_of <= :end "
                "ORDER BY as_of, ticker"
            ),
            conn,
            params={"end": end},
        )
        delistings = conn.execute(
            text("SELECT ticker, delisted_on FROM tickers WHERE delisted_on IS NOT NULL")
        ).all()

    dates = tuple(pd.Timestamp(d).date() for d in calendar)
    if not dates:
        raise ValueError(f"no daily bars between {start} and {end} for split {split_key!r}")

    universe["as_of"] = pd.to_datetime(universe["as_of"]).dt.date
    by_as_of: dict[date, tuple[str, ...]] = {
        cast("date", as_of): tuple(sorted(group["ticker"]))
        for as_of, group in universe.groupby("as_of", sort=True)
    }
    rebalances = sorted(by_as_of)

    members: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    cursor = 0
    for day in dates:
        while cursor < len(rebalances) and rebalances[cursor] <= day:
            current = by_as_of[rebalances[cursor]]
            cursor += 1
        members.append(current)

    # The window opens on the first day the trade universe has members.
    #
    # The train split starts 2010-01-01 and the first `universe.as_of` is
    # 2010-03-31, so without this clip every arm sits in cash for ~60
    # trading days and buy-and-hold's `frac_deployed` measures 0.98 rather
    # than the 1.0 gate item 5 asserts structurally. Clipping is the honest
    # fix: an arm cannot hold a universe that does not exist yet, and
    # measuring a stretch where every arm is identically in cash adds no
    # information to a comparison between arms. Every arm still shares one
    # calendar, so ADR 012's identical-dates rule is untouched.
    first = next((i for i, names in enumerate(members) if names), None)
    if first is None:
        raise ValueError(
            f"no in_trade universe members between {start} and {end} for split {split_key!r}"
        )
    dates = dates[first:]
    members = members[first:]

    return ArmWindow(
        split_key=split_key,
        dates=dates,
        members=tuple(members),
        delisted_on={str(ticker): pd.Timestamp(when).date() for ticker, when in delistings},
    )


# ==========================================================================
# Panels: one ticker's bars and indicators, mapped onto the shared calendar
# ==========================================================================


@dataclass(frozen=True)
class Panel:
    """One ticker's dense daily frames plus its map onto the shared calendar.

    `bars` and `indicators` share a positional index and a `pd.Timestamp`
    index, which is what `research.enrich.resolve_exit_for_entry` and
    `core.exits.resolve_exit` both require. `global_idx[p]` is the shared
    calendar position of local bar `p`.
    """

    ticker: str
    bars: pd.DataFrame
    indicators: pd.DataFrame
    close: np.ndarray
    adj_close: np.ndarray
    dates: tuple[date, ...]
    global_idx: np.ndarray
    pos_by_date: Mapping[date, int]

    @property
    def n_bars(self) -> int:
        return len(self.dates)


_PANEL_INDICATORS = ("bb_lower", "bb_mid", "bb_upper", "k_full", "atr_14")


def load_panels(engine: Engine, tickers: Sequence[str], window: ArmWindow) -> dict[str, Panel]:
    """One `Panel` per ticker with bars inside the window.

    Tickers with no bars are omitted rather than carried as empty frames: a
    name in the universe with no price contributes nothing to any arm, and
    every consumer here already handles a missing key.
    """
    index_of = window.index_of()
    start, end = window.dates[0], window.dates[-1]
    out: dict[str, Panel] = {}
    indicator_sql = ", ".join(_PANEL_INDICATORS)

    with engine.connect() as conn:
        for ticker in tickers:
            params: dict[str, Any] = {"ticker": ticker, "start": start, "end": end}
            bars = pd.read_sql(
                text(
                    "SELECT ts, open, high, low, close, adj_close FROM bars "
                    "WHERE ticker = :ticker AND interval = '1d' "
                    "AND ts >= :start AND ts <= :end ORDER BY ts"
                ),
                conn,
                params=params,
            )
            if bars.empty:
                continue
            indicators = pd.read_sql(
                text(
                    f"SELECT ts, {indicator_sql} FROM indicators "
                    "WHERE ticker = :ticker AND interval = '1d' "
                    "AND ts >= :start AND ts <= :end ORDER BY ts"
                ),
                conn,
                params=params,
            )
            out[ticker] = _build_panel(ticker, bars, indicators, index_of)
    return out


def _build_panel(
    ticker: str,
    bars: pd.DataFrame,
    indicators: pd.DataFrame,
    index_of: Mapping[date, int],
) -> Panel:
    frame = bars.copy()
    frame["ts"] = pd.to_datetime(frame["ts"]).dt.tz_localize(None).dt.normalize()
    for column in ("open", "high", "low", "close", "adj_close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.drop_duplicates("ts").sort_values("ts").set_index("ts", drop=False)

    if indicators.empty:
        aligned = pd.DataFrame(index=frame.index, columns=list(_PANEL_INDICATORS), dtype="float64")
    else:
        ind = indicators.copy()
        ind["ts"] = pd.to_datetime(ind["ts"]).dt.tz_localize(None).dt.normalize()
        for column in _PANEL_INDICATORS:
            ind[column] = pd.to_numeric(ind[column], errors="coerce")
        aligned = ind.drop_duplicates("ts").set_index("ts").reindex(frame.index)

    dates = tuple(ts.date() for ts in frame.index)
    return Panel(
        ticker=ticker,
        bars=frame,
        indicators=aligned,
        close=frame["close"].to_numpy(dtype="float64"),
        adj_close=frame["adj_close"].to_numpy(dtype="float64"),
        dates=dates,
        global_idx=np.array([index_of.get(d, -1) for d in dates], dtype="int64"),
        pos_by_date={d: i for i, d in enumerate(dates)},
    )


def price_matrix(panels: Mapping[str, Panel], window: ArmWindow) -> dict[str, np.ndarray]:
    """Total-return prices per ticker, aligned to the shared calendar.

    NaN on a day the ticker had no bar — not yet listed, delisted, or a
    rejected bar. Never filled or carried forward (invariant 4); the arms
    drop a NaN member from that day's weighting instead.
    """
    out: dict[str, np.ndarray] = {}
    for ticker, panel in panels.items():
        series = np.full(window.n_days, np.nan, dtype="float64")
        valid = panel.global_idx >= 0
        series[panel.global_idx[valid]] = panel.adj_close[valid]
        out[ticker] = series
    return out


# ==========================================================================
# Entries and positions
# ==========================================================================


@dataclass(frozen=True)
class Entry:
    ticker: str
    signal_date: date


def load_signal_events(
    engine: Engine,
    config_hash: str,
    split_key: str,
    side: Side,
) -> pd.DataFrame:
    """ADR 102's measured population, one side, for the arm layer.

    Same two filters `research.cell_stats.load_grid_events` applies —
    `entry_kind = 'next_open'` and `is_cluster_head` — imported from that
    module rather than restated, so the arms and the grid cannot end up
    measuring different populations of the same config.
    """
    sql = [
        "SELECT ticker, signal_date, signal_type, side, era, k_full, cofire_count",
        "FROM events WHERE config_hash = :config_hash AND split_key = :split_key "
        # ADR 122. The arms are measured on the trade universe, the same
        # population `cell_stats` uses. See the note there.
        "AND in_trade",
        "AND side = :side AND entry_kind = :entry_kind",
    ]
    if GRID_CLUSTER_HEADS_ONLY:
        sql.append("AND is_cluster_head")
    sql.append("ORDER BY signal_date, ticker")
    with engine.connect() as conn:
        frame = pd.read_sql(
            text(" ".join(sql)),
            conn,
            params={
                "config_hash": config_hash,
                "split_key": split_key,
                "side": side.value,
                "entry_kind": GRID_ENTRY_KIND,
            },
        )
    frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.date
    return frame


def restrict_to_universe(events: pd.DataFrame, window: ArmWindow) -> pd.DataFrame:
    """Keep only events whose ticker was in the trade universe that day.

    ADR 012 again: the signal arm has to trade the same names buy-and-hold
    holds, or the comparison is between two universes rather than two entry
    rules.
    """
    if events.empty:
        return events
    membership = {day: set(names) for day, names in zip(window.dates, window.members)}
    keep = [
        day in membership and ticker in membership[day]
        for ticker, day in zip(events["ticker"], events["signal_date"])
    ]
    return events.loc[pd.Series(keep, index=events.index)].copy()


def entries_from_events(events: pd.DataFrame) -> tuple[Entry, ...]:
    return tuple(
        Entry(ticker=str(row.ticker), signal_date=cast("date", row.signal_date))
        for row in events.itertuples(index=False)
    )


def build_positions(
    entries: Iterable[Entry],
    panels: Mapping[str, Panel],
    window: ArmWindow,
    cfg: Config,
    side: Side,
    cache: dict[tuple[str, int], Any] | None = None,
) -> list[Position]:
    """Resolve every entry into a `Position` on the shared calendar.

    Entry is `next_open`: the signal fires on day `s` and the position fills
    at the open of day `s + 1`, which is the population
    `load_signal_events` selects and the offset `core.returns.entry_offset_for`
    pins. Exits come from `research.enrich.resolve_exit_for_entry` — the same
    call the backtest makes, which is what 13.2's "assert the same function
    is called" asks for.

    `cache` memoizes exit resolution on `(ticker, entry_position)`. The
    random null draws 200 replications from the same few hundred
    ticker-years, so the same entry bar recurs constantly and resolving it
    once is worth several minutes over a full run. It is keyed on exactly
    what determines the answer — a ticker's bars and one entry bar, under a
    fixed `ExitParams` — so a shared cache across replications is sound and
    across configs is not, which is why the caller owns it.

    An entry that cannot resolve is dropped, never fabricated: no bar for
    the signal date, no next bar to fill on, a NaN fill price, or an empty
    forward window.
    """
    ep = cfg.exits
    out: list[Position] = []
    for entry in entries:
        panel = panels.get(entry.ticker)
        if panel is None:
            continue
        signal_pos = panel.pos_by_date.get(entry.signal_date)
        if signal_pos is None:
            continue
        entry_pos = signal_pos + 1
        if entry_pos >= panel.n_bars:
            continue
        entry_price = float(panel.bars["open"].iloc[entry_pos])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        key = (entry.ticker, entry_pos)
        resolved = None if cache is None else cache.get(key)
        if resolved is None:
            resolved = resolve_exit_for_entry(
                {
                    "entry_date": panel.dates[entry_pos],
                    "entry_price": entry_price,
                    "entry_kind": GRID_ENTRY_KIND,
                },
                entry_pos,
                side,
                panel.bars,
                panel.indicators,
                ep,
            )
            if cache is not None:
                cache[key] = resolved
        if resolved["exit_idx"] is None:
            continue

        exit_pos = entry_pos + 1 + int(resolved["exit_idx"])
        exit_price = float(resolved["exit_price"])
        if exit_pos >= panel.n_bars or not np.isfinite(exit_price):
            continue

        position = _to_global_position(panel, entry_pos, exit_pos, entry_price, exit_price, side)
        if position is not None and position.exit_idx < window.n_days:
            out.append(position)
    return out


def _to_global_position(
    panel: Panel,
    entry_pos: int,
    exit_pos: int,
    entry_price: float,
    exit_price: float,
    side: Side,
) -> Position | None:
    """Map a ticker-local span onto the shared calendar.

    A ticker missing an interior day (a rejected bar) is shorter than the
    shared calendar over the same span. The missing days are padded with a
    0.0 return rather than dropped or interpolated: no bar means no observed
    price change, and stretching the neighbouring day's move across the gap
    would invent one.
    """
    g_entry = int(panel.global_idx[entry_pos])
    g_exit = int(panel.global_idx[exit_pos])
    if g_entry < 0 or g_exit < 0:
        return None

    local = core_arms.position_returns(
        panel.close, panel.adj_close, entry_pos, exit_pos, entry_price, exit_price, side
    )
    padded = [0.0] * (g_exit - g_entry + 1)
    for k, pos in enumerate(range(entry_pos, exit_pos + 1)):
        slot = int(panel.global_idx[pos]) - g_entry
        if 0 <= slot < len(padded):
            padded[slot] = local[k]

    return Position(
        ticker=panel.ticker,
        side=side,
        entry_idx=g_entry,
        exit_idx=g_exit,
        entry_price=entry_price,
        exit_price=exit_price,
        returns=tuple(padded),
    )


# ==========================================================================
# The random-entry null (13.2, ADR 061)
# ==========================================================================


def _null_rng(config_hash: str, replication: int) -> np.random.Generator:
    """The null's generator, derived from `config_hash` and the replication.

    ADR 061 pins the seed to `config_hash`. That is what makes the null
    reproducible **within** a config and different **across** configs, which
    is the property comparing sweep configs needs. A wall-clock seed breaks
    the first and a fixed constant breaks the second, and both wrong
    versions run without complaint — the whole reason this is one named
    function.

    `SeedSequence` mixes the two rather than adding or xoring them, so
    adjacent replications do not produce correlated streams.
    """
    return np.random.default_rng(np.random.SeedSequence([int(config_hash, 16), replication]))


def firing_rate_by_ticker_year(entries: Sequence[Entry]) -> dict[tuple[str, int], int]:
    """How many times each ticker fired in each year.

    ADR 061 samples the **empirical firing rate per ticker-year**, not a
    uniform rate. A ticker that fired 12 times in 2018 gets 12 random
    entries in 2018 and none in a year it never fired. A uniform rate would
    spread the same total across every ticker-year evenly, which changes
    both which names and which regimes the null covers.
    """
    counts: dict[tuple[str, int], int] = {}
    for entry in entries:
        key = (entry.ticker, entry.signal_date.year)
        counts[key] = counts.get(key, 0) + 1
    return counts


def eligible_days(
    panels: Mapping[str, Panel], window: ArmWindow, cfg: Config
) -> dict[tuple[str, int], tuple[date, ...]]:
    """Days a random entry could be drawn on, per ticker-year.

    A day qualifies when the ticker has a bar, is in the trade universe that
    day, and has at least one bar after the `next_open` fill to resolve an
    exit against. The last condition matters at a window's right edge and at
    a delisting, where a drawn entry would otherwise be dropped in
    `build_positions` and quietly shrink that replication.
    """
    membership = {day: set(names) for day, names in zip(window.dates, window.members)}
    out: dict[tuple[str, int], list[date]] = {}
    for ticker, panel in panels.items():
        for pos, day in enumerate(panel.dates):
            if pos + 2 >= panel.n_bars:
                continue
            if ticker not in membership.get(day, ()):
                continue
            out.setdefault((ticker, day.year), []).append(day)
    return {key: tuple(value) for key, value in out.items()}


def random_entries(
    signal_entries: Sequence[Entry],
    pools: Mapping[tuple[str, int], tuple[date, ...]],
    config_hash: str,
    replication: int,
) -> tuple[Entry, ...]:
    """One replication of the null: the same firing counts, random dates.

    Drawn **without replacement**, since a ticker cannot enter twice on one
    day, and iterated in sorted key order so the stream is a function of
    `(config_hash, replication)` alone rather than of dictionary ordering.

    A ticker-year whose pool is smaller than its firing count draws the
    whole pool. That happens only where the universe membership is thinner
    than the event history, and taking fewer is the honest outcome —
    sampling with replacement to hit the count would double-enter a day.
    """
    rng = _null_rng(config_hash, replication)
    counts = firing_rate_by_ticker_year(signal_entries)
    out: list[Entry] = []
    for key in sorted(counts):
        pool = pools.get(key, ())
        if not pool:
            continue
        n = min(counts[key], len(pool))
        drawn = rng.choice(np.array(pool, dtype=object), size=n, replace=False)
        out.extend(Entry(ticker=key[0], signal_date=d) for d in drawn)
    return tuple(sorted(out, key=lambda e: (e.signal_date, e.ticker)))


# ==========================================================================
# Trim and redeploy (13.3)
# ==========================================================================


def trim_signals(
    trim_events: pd.DataFrame,
    redeploy_events: pd.DataFrame,
    window: ArmWindow,
    bm: BenchmarkParams,
) -> dict[str, list[tuple[int, str]]]:
    """DESIGN §6.5's triggers, as calendar-indexed per-ticker instructions.

    Trim on `CONFLUENCE_HIGH` **or** `%K_full >= trim_stoch_threshold`;
    redeploy on the next `CONFLUENCE_LOW` in the same name. Both the
    fraction and the threshold come from `BenchmarkParams` — never a
    literal, and never `ExitParams.exit_stoch_threshold`, which is exit
    policy for an open sleeve position rather than a trim rule for a held
    core one (ADR 092's pattern, which the 13.3 brief notes has already
    escaped once into `v_positions`).
    """
    index_of = window.index_of()
    out: dict[str, list[tuple[int, str]]] = {}

    if not trim_events.empty:
        k_full = pd.to_numeric(trim_events["k_full"], errors="coerce")
        triggered = (trim_events["signal_type"] == TRIM_SIGNAL_TYPE) | (
            k_full >= bm.trim_stoch_threshold
        )
        for row in trim_events.loc[triggered].itertuples(index=False):
            idx = index_of.get(cast("date", row.signal_date))
            if idx is not None:
                out.setdefault(str(row.ticker), []).append((idx, "trim"))

    if not redeploy_events.empty:
        wanted = redeploy_events["signal_type"] == REDEPLOY_SIGNAL_TYPE
        for row in redeploy_events.loc[wanted].itertuples(index=False):
            idx = index_of.get(cast("date", row.signal_date))
            if idx is not None:
                out.setdefault(str(row.ticker), []).append((idx, "redeploy"))

    return {ticker: sorted(entries) for ticker, entries in out.items()}


# ==========================================================================
# Rows
# ==========================================================================


@dataclass(frozen=True)
class BenchmarkRow:
    """One `benchmarks` row before provenance is stamped on."""

    arm: str
    values: dict[str, Any]
    replication: int | None = None
    era: str = POOLED_ERA


def _metrics_dict(metrics: ArmMetrics) -> dict[str, Any]:
    return {
        "total_ret": metrics.total_ret,
        "annualized_ret": metrics.annualized_ret,
        "sharpe": metrics.sharpe,
        "max_drawdown": metrics.max_drawdown,
        "frac_deployed": metrics.frac_deployed,
        "capital_efficiency": metrics.capital_efficiency,
        "win_rate": metrics.win_rate,
        "n_trades": metrics.n_trades,
        "terminal_value": metrics.terminal_value,
    }


def core_purchases(window: ArmWindow, panels: Mapping[str, Panel]) -> list[tuple[str, date]]:
    """Every core-position buy, for the wash-sale test (ADR 032).

    The core position is the buy-and-hold book, and a ticker is bought on
    each day it enters the trade universe. 13.5's acceptance requires a core
    purchase alone to trigger the flag, so this cannot be the sleeve's own
    entries — the sleeve is what the loss is realized in.

    Dividend reinvestment is **not** modeled as a separate purchase: the
    total-return series compounds distributions into the price rather than
    buying shares on the ex-date, so there is no reinvestment date to place.
    That understates flagging, in a stated direction, and it is recorded
    here rather than left for a reader to infer from silence.
    """
    out: list[tuple[str, date]] = []
    previous: set[str] = set()
    for day, names in zip(window.dates, window.members):
        current = set(names)
        for ticker in sorted(current - previous):
            if ticker in panels:
                out.append((ticker, day))
        previous = current
    return out


def _tax_row(
    trades: Sequence[Trade],
    core: Sequence[tuple[str, date]],
    pre_tax_ret: float,
    bm: BenchmarkParams,
) -> dict[str, Any]:
    """ADR 032's pre-tax / post-tax pair for one arm.

    The purchase list is the core position **plus the arm's own other
    entries**. ADR 032 says "bought within 30 days either side, *including*
    the core position" — the sleeve's own repurchase of a name it just took
    a loss in is the textbook wash sale, and passing only `core` would miss
    every one of them. `core_arms.wash_sale_flags` excludes each trade's own
    entry, so a trade cannot flag itself.
    """
    purchases = list(core) + [(t.ticker, t.entry_date) for t in trades if t.entry_date is not None]
    pre, post, flagged = core_arms.tax_summary(trades, purchases, pre_tax_ret, bm)
    return {"pre_tax_ret": pre, "post_tax_ret": post, "wash_sale_flagged": flagged}


def month_start_indices(dates: Sequence[date]) -> tuple[int, ...]:
    """The first trading day of each calendar month in the window."""
    seen: set[tuple[int, int]] = set()
    out: list[int] = []
    for i, day in enumerate(dates):
        key = (day.year, day.month)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return tuple(out)


def expected_deployment_days(
    engine: Engine,
    config_hash: str,
    cfg: Config,
    window: ArmWindow,
    realized_signal_days: int,
) -> int:
    """`N` for `dca_signal`: how many deployments the historical rate implies.

    DESIGN §6.6 says "`N` comes from the historical firing rate" and
    "underfiring is reported explicitly since idle capital has a real cost."
    Taking `N` from the measured window's own realized count satisfies the
    first and makes the second **structurally impossible** on every split:
    the arm would deploy `C/N` exactly `N` times and `capital_undeployed`
    would be zero by construction, which is a number that cannot report
    anything.

    So `N` is the **train** split's signal-day rate scaled to this window's
    length — a forward-applied expectation. On the train split itself the
    two definitions coincide by definition (the window that *defines* the
    rate realizes it), so `N` is the realized count there and underfiring is
    only observable on validate and holdout. That is a property of what a
    historical rate is, not a shortcut.

    Reading the train split from a validate or holdout run is not a firewall
    breach: this is a firing *rate*, never an outcome, and ADR 088's rule is
    about attaching statistics to events.
    """
    if window.split_key == "train":
        return max(1, realized_signal_days)

    train_window = load_window(engine, cfg, "train")
    events = restrict_to_universe(
        load_signal_events(engine, config_hash, "train", Side.LONG), train_window
    )
    if events.empty or train_window.n_days == 0:
        return max(1, realized_signal_days)
    rate = float(events["signal_date"].nunique()) / float(train_window.n_days)
    return max(1, int(round(rate * window.n_days)))


def signal_day_indices(positions: Sequence[Position]) -> tuple[int, ...]:
    """Distinct calendar days on which the signal arm opened anything.

    A day where six names fire is one deployment day for DCA, not six: the
    DCA variants deploy a tranche of a fixed capital pool, and counting the
    day once is what keeps `N` the number of *deployments* rather than the
    number of events.
    """
    return tuple(sorted({p.entry_idx for p in positions}))


# ==========================================================================
# The run
# ==========================================================================


@dataclass(frozen=True)
class ArmCurves:
    """Equity curves already computed inside `run_benchmarks`, for 14.1.

    Populated only when `run_benchmarks(collect_curves=True)`. This carries
    exactly the `ArmSimulation.equity` series `run_benchmarks` built for the
    pooled buy-and-hold, signal, and null-replication arms — nothing here is
    re-derived. `research/curves.py` may only shape these series (percentile
    band, tidy frame, CSV); reconstructing them from `load_window` /
    `build_positions` / `simulate_*` in a second module would be a second
    simulation of the same thing, which is exactly the invariant 2 violation
    this dataclass exists to avoid.

    `dates` is `window.dates` (`n_days` entries). Each `equity` series has
    `n_days + 1` points: index 0 is the pre-window baseline of 1.0
    (`core.arms.simulate_portfolio`'s docstring), and `equity[i + 1]` is the
    value after day `i`'s return, so it is `equity[i + 1]` that pairs with
    `dates[i]`.
    """

    dates: tuple[date, ...]
    buy_hold: pd.Series
    signal: pd.Series
    random: tuple[pd.Series, ...]


@dataclass
class BenchmarkReport:
    """What one `run_benchmarks` call measured and wrote."""

    config_hash: str
    split_key: str
    run_id: str
    n_days: int
    first_day: date
    last_day: date
    n_tickers: int
    n_signal_entries: int
    replications: int
    rows_written: int = 0
    null_threshold: float | None = None
    null_percentile: float | None = None
    signal_total_ret: float | None = None
    signal_exceeds_null: bool | None = None
    subset_n_events: int | None = None
    notes: list[str] = field(default_factory=list)
    # Every arm that is a single row rather than a distribution, in the
    # order they were computed. Carried on the report so the CLI, the
    # integration tests, and `RESULTS.md` all read one table instead of
    # three re-derivations of it.
    arm_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    # 14.1: the pooled buy-hold / signal / null equity curves, set only when
    # `collect_curves=True`. `None` on the default path, which is what keeps
    # that path byte-identical to before this field existed.
    curves: ArmCurves | None = None

    def arm_table(self) -> str:
        """One line per summary arm. `n/a` where a column does not apply to
        an arm rather than a zero, which would read as a measurement."""
        if self.arm_rows.empty:
            return "(no summary arms)"
        header = (
            f"  {'arm':<11} {'era':<13} {'total_ret':>10} {'ann_ret':>9} "
            f"{'sharpe':>7} {'max_dd':>7} {'deploy':>7} {'cap_eff':>8} "
            f"{'win':>6} {'n':>7} {'post_tax':>9}"
        )
        lines = [header]
        for row in self.arm_rows.itertuples(index=False):
            lines.append(
                f"  {row.arm:<11} {str(row.era):<13} {_pct(row.total_ret):>10} "
                f"{_pct(row.annualized_ret):>9} {_num(row.sharpe):>7} "
                f"{_pct(row.max_drawdown):>7} {_pct(row.frac_deployed):>7} "
                f"{_num(row.capital_efficiency):>8} {_pct(row.win_rate):>6} "
                f"{_int(row.n_trades):>7} {_pct(row.post_tax_ret):>9}"
            )
        return "\n".join(lines)

    def summary(self) -> str:
        verdict = {True: "above", False: "at or below", None: "no null"}[self.signal_exceeds_null]
        threshold = "n/a" if self.null_threshold is None else f"{self.null_threshold:+.4f}"
        signal = "n/a" if self.signal_total_ret is None else f"{self.signal_total_ret:+.4f}"
        return (
            f"config_hash={self.config_hash} split={self.split_key} run_id={self.run_id}\n"
            f"window: {self.first_day} .. {self.last_day} "
            f"({self.n_days} trading days, {self.n_tickers} tickers)\n"
            f"signal entries: {self.n_signal_entries}, replications: {self.replications}\n"
            f"{self.arm_table()}\n"
            f"signal total_ret={signal} vs null p{self.null_percentile or '?'} "
            f"= {threshold} -> [bold]{verdict}[/bold]\n"
            f"rows written: {self.rows_written}"
        )


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _num(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{float(value):.3f}"


def _int(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return str(int(value))


def _replication_slice(
    database_url: str,
    config_hash: str,
    split_key: str,
    cfg: Config,
    bm: BenchmarkParams,
    lo: int,
    hi: int,
) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    """Replications `lo..hi` inclusive, in a worker process.

    Spawn-safe on the same template as `backtest._backtest_one_ticker` and
    `compute._compute_one_ticker` (CLAUDE.md "Platform"): opens its own
    connection because engines are not picklable, performs no top-level
    side effects, and is importable on its own.

    **It rebuilds the panels rather than receiving them.** Measured
    2026-08-27: setup is **35.4 s** and a replication is **4.52 s**, so 200
    serial replications are 15.6 minutes and setup is 4% of it. Rebuilding
    avoids pickling a price panel for the whole universe through a pipe --
    the same trade `0b2cc00` made for the harness, which spools parquet
    rather than sending frames.

    **The setups do not fully overlap**, and that is the ceiling here.
    `load_panels` is database-bound, so eight workers doing it at once
    contend rather than run concurrently. Measured end to end on 200
    replications: **8 workers 3.99 min, 4 workers 4.39 min, serial 15.6**.
    Eight is worth having and sixteen would not be -- the returns flatten
    where the setup contention starts, not where the cores run out.

    **At small replication counts there is no win at all.** 24 replications
    measured 109.7 s serial and 109.5 s on eight workers: the setup *is*
    the job below roughly 40 draws. `--replications 50` sweeps (ADR 061)
    should stay serial.

    **A contiguous range, not one replication per task.** Each task pays
    the setup, so 200 tasks would pay it 200 times. Eight ranges pay it
    eight times, concurrently.

    Returns plain tuples. `BenchmarkRow` is assembled in the parent so the
    row's shape stays defined in one place.
    """
    engine = db_io.get_engine(database_url)
    window = load_window(engine, cfg, split_key)
    panels = load_panels(engine, window.tickers, window)
    long_events = restrict_to_universe(
        load_signal_events(engine, config_hash, split_key, Side.LONG), window
    )
    signal_entries = entries_from_events(long_events)
    purchases = core_purchases(window, panels)
    pools = eligible_days(panels, window, cfg)
    cache: dict[tuple[str, int], Any] = {}

    out: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for replication in range(lo, hi + 1):
        entries = random_entries(signal_entries, pools, config_hash, replication)
        positions = build_positions(entries, panels, window, cfg, Side.LONG, cache)
        sim = core_arms.simulate_portfolio(window.n_days, positions, bm, window.dates)
        metrics = core_arms.arm_metrics(sim, bm)
        out.append(
            (
                replication,
                _metrics_dict(metrics),
                _tax_row(sim.trades, purchases, metrics.total_ret, bm),
            )
        )
    return out


def _replication_ranges(replications: int, workers: int) -> list[tuple[int, int]]:
    """Contiguous 1-based ranges, as evenly as they divide.

    Even sizing matters more than it looks: every worker pays the same 35 s
    setup, so one worker left with twice the replications sets the wall
    clock for all of them.
    """
    workers = max(1, min(workers, replications))
    base, extra = divmod(replications, workers)
    ranges: list[tuple[int, int]] = []
    start = 1
    for i in range(workers):
        size = base + (1 if i < extra else 0)
        if size == 0:
            continue
        ranges.append((start, start + size - 1))
        start += size
    return ranges


def run_benchmarks(
    engine: Engine,
    config_hash: str,
    split_key: str = "train",
    *,
    cfg: Config | None = None,
    bm: BenchmarkParams | None = None,
    rp: ReportingParams | None = None,
    replications: int | None = None,
    run_id: str = "",
    git_sha: str = "",
    write: bool = True,
    progress: Any = None,
    collect_curves: bool = False,
    max_workers: int = 1,
) -> tuple[pd.DataFrame, BenchmarkReport]:
    """Measure all eight arms on one config and split, and write them.

    Order matches the session brief's dependency chain: the buy-and-hold and
    signal arms first, the null next (it reuses the signal arm's exit
    machinery and firing rates), then trim, then DCA off the buy-and-hold
    basket, then the tax pass over every arm's trade list, then 13.6's
    high-breadth subset re-running the same three arm functions on a
    filtered entry list.

    `collect_curves=True` (14.1) additionally sets `report.curves` to the
    pooled buy-hold, signal, and 200-replication null `ArmSimulation.equity`
    series already computed below — no extra simulation runs. Default is
    `False` and that path is byte-identical to before this parameter
    existed: the only difference under `True` is appending each already-built
    equity series to a list as it is produced.
    """
    cfg = cfg or Config()
    bm = bm or BenchmarkParams()
    rp = rp or ReportingParams()
    replications = cfg.stats.n_replications_default if replications is None else replications

    window = load_window(engine, cfg, split_key)
    panels = load_panels(engine, window.tickers, window)
    prices = price_matrix(panels, window)
    purchases = core_purchases(window, panels)

    long_events = restrict_to_universe(
        load_signal_events(engine, config_hash, split_key, Side.LONG), window
    )
    short_events = restrict_to_universe(
        load_signal_events(engine, config_hash, split_key, Side.SHORT), window
    )
    signal_entries = entries_from_events(long_events)

    report = BenchmarkReport(
        config_hash=config_hash,
        split_key=split_key,
        run_id=run_id,
        n_days=window.n_days,
        first_day=window.dates[0],
        last_day=window.dates[-1],
        n_tickers=len(panels),
        n_signal_entries=len(signal_entries),
        replications=replications,
    )

    rows: list[BenchmarkRow] = []
    cache: dict[tuple[str, int], Any] = {}

    # --- 13.1 buy and hold -------------------------------------------------
    hold = core_arms.simulate_buy_hold(prices, window.members, bm, window.dates)
    hold_metrics = core_arms.arm_metrics(hold, bm)
    rows.append(
        BenchmarkRow(
            ARM_BUY_HOLD,
            {
                **_metrics_dict(hold_metrics),
                **_tax_row(hold.trades, purchases, hold_metrics.total_ret, bm),
            },
        )
    )

    # --- 13.1 signal -------------------------------------------------------
    signal_positions = build_positions(signal_entries, panels, window, cfg, Side.LONG, cache)
    signal_sim = core_arms.simulate_portfolio(window.n_days, signal_positions, bm, window.dates)
    signal_metrics = core_arms.arm_metrics(signal_sim, bm)
    rows.append(
        BenchmarkRow(
            ARM_SIGNAL,
            {
                **_metrics_dict(signal_metrics),
                **_tax_row(signal_sim.trades, purchases, signal_metrics.total_ret, bm),
            },
        )
    )
    report.signal_total_ret = signal_metrics.total_ret

    # --- 13.2 random-entry null -------------------------------------------
    pools = eligible_days(panels, window, cfg)
    null_equities: list[pd.Series] = []

    # **The replications are the job.** Measured 2026-08-27: setup 35.4 s,
    # one replication 4.52 s, so 200 of them are 15.6 of the 16.1 minutes
    # `benchmarks` cost -- and they were run one at a time while fifteen of
    # sixteen cores idled. Each draw depends only on its own seed
    # (`random_entries(..., config_hash, replication)`), so they are
    # independent by construction.
    #
    # **`max_workers=1` keeps the in-process path**, which is what
    # `collect_curves` needs: the equity series are large and returning 200
    # of them through a pipe would trade the saving away. `research/curves`
    # is the only caller that asks for them.
    if max_workers > 1 and not collect_curves and replications > 1:
        from concurrent.futures import ProcessPoolExecutor

        url = engine.url.render_as_string(hide_password=False)
        ranges = _replication_ranges(replications, max_workers)
        done = 0
        collected: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        with ProcessPoolExecutor(max_workers=len(ranges)) as pool:
            futures = [
                pool.submit(_replication_slice, url, config_hash, split_key, cfg, bm, lo, hi)
                for lo, hi in ranges
            ]
            for future in futures:
                part = future.result()
                collected.extend(part)
                done += len(part)
                if progress is not None:
                    progress(done, replications)
        # Sorted because `as_completed` order is not replication order, and
        # `benchmarks` is keyed on (arm, replication) -- an out-of-order
        # write would still be correct but would make two runs of the same
        # config produce different row ids.
        for replication, metrics_d, tax_d in sorted(collected):
            rows.append(BenchmarkRow(ARM_RANDOM, {**metrics_d, **tax_d}, replication=replication))
    else:
        for replication in range(1, replications + 1):
            entries = random_entries(signal_entries, pools, config_hash, replication)
            positions = build_positions(entries, panels, window, cfg, Side.LONG, cache)
            sim = core_arms.simulate_portfolio(window.n_days, positions, bm, window.dates)
            metrics = core_arms.arm_metrics(sim, bm)
            rows.append(
                BenchmarkRow(
                    ARM_RANDOM,
                    {
                        **_metrics_dict(metrics),
                        **_tax_row(sim.trades, purchases, metrics.total_ret, bm),
                    },
                    replication=replication,
                )
            )
            if collect_curves:
                null_equities.append(sim.equity)
            if progress is not None:
                progress(replication, replications)

    # --- 13.3 trim and redeploy -------------------------------------------
    signals = trim_signals(short_events, long_events, window, bm)
    trim = core_arms.simulate_trim(prices, window.members, signals, bm, window.dates)
    trim_annual = core_arms.annualized_return(trim.total_ret, window.n_days, bm)
    rows.append(
        BenchmarkRow(
            ARM_TRIM,
            {
                "total_ret": trim.total_ret,
                "annualized_ret": trim_annual,
                "sharpe": core_arms.sharpe(trim.equity.pct_change().dropna(), bm),
                "max_drawdown": core_arms.max_drawdown(trim.equity),
                "frac_deployed": trim.frac_deployed,
                "capital_efficiency": core_arms.capital_efficiency(
                    trim.total_ret, trim.frac_deployed
                ),
                "win_rate": (
                    None
                    if not trim.trades
                    else sum(1 for t in trim.trades if t.realized_return > 0) / len(trim.trades)
                ),
                "n_trades": trim.n_trims,
                "terminal_value": bm.initial_capital * float(trim.equity.iloc[-1]),
                "n_round_trips": trim.n_round_trips,
                "avg_days_in_cash": trim.avg_days_in_cash,
                # Taxed on its full holding-stint disposals, the same ones
                # buy-and-hold books, so the two arms' post-tax numbers are
                # comparable. **Stated gap:** the trims themselves are
                # partial disposals and are not modeled as taxable events,
                # which understates the trim arm's tax in a known direction.
                # ADR 032 is Provisional; partial-lot accounting is outside
                # this session's scope.
                **_tax_row(trim.trades, purchases, trim.total_ret, bm),
            },
        )
    )
    report.notes.append(
        f"trim: {trim.n_trims} trims, {trim.n_round_trips} round trips, "
        f"{trim.n_open_cash_positions} left in cash at window end"
    )

    # --- 13.4 DCA ----------------------------------------------------------
    basket = hold.equity.to_numpy(dtype="float64")[1:]
    deployment_days = signal_day_indices(signal_positions)
    dca = core_arms.simulate_dca_family(
        basket,
        month_start_indices(window.dates),
        deployment_days,
        n_expected_signals=expected_deployment_days(
            engine, config_hash, cfg, window, len(deployment_days)
        ),
        bm=bm,
    )
    for arm, result in dca.items():
        rows.append(
            BenchmarkRow(
                arm,
                {
                    "total_ret": result.total_ret,
                    "annualized_ret": core_arms.annualized_return(
                        result.total_ret, window.n_days, bm
                    ),
                    "terminal_value": result.terminal_value,
                    "irr": result.irr,
                    "avg_cost_basis": result.avg_cost_basis,
                    "cash_drag": result.cash_drag,
                    "capital_undeployed": result.capital_undeployed,
                    "n_trades": result.n_deployments,
                    # Measured off the variant's own curve. `dca_signal`
                    # holds cash until its first signal, so assuming 1.0
                    # here would overstate its deployment and understate
                    # its capital efficiency.
                    "frac_deployed": result.frac_deployed,
                    "capital_efficiency": core_arms.capital_efficiency(
                        result.total_ret, result.frac_deployed
                    ),
                    "max_drawdown": result.max_drawdown,
                    "pre_tax_ret": result.total_ret,
                    # DESIGN §6.6: no exits, so nothing is realized and
                    # there is no tax event. The position's unrealized gain
                    # is untaxed by construction, not by omission.
                    "post_tax_ret": result.total_ret,
                    "wash_sale_flagged": False,
                },
            )
        )

    # --- 13.6 high-breadth subset -----------------------------------------
    subset_rows, subset_note, subset_n = _high_breadth_rows(
        long_events,
        panels,
        window,
        cfg,
        bm,
        rp,
        prices,
        purchases,
        config_hash,
        replications,
        cache,
        progress,
    )
    rows.extend(subset_rows)
    report.subset_n_events = subset_n
    report.notes.append(subset_note)

    frame = _to_frame(rows, config_hash, split_key, run_id, git_sha)
    report.rows_written = write_benchmarks(engine, frame) if write else 0
    report.arm_rows = frame.loc[frame["replication"].isna()].reset_index(drop=True)
    report.null_percentile = bm.null_percentile

    if collect_curves:
        report.curves = ArmCurves(
            dates=window.dates,
            buy_hold=hold.equity,
            signal=signal_sim.equity,
            random=tuple(null_equities),
        )

    # The percentile is read back off the **stored** rows (13.2 acceptance),
    # not off the in-memory list that produced them. An in-memory read would
    # agree with a writer that silently dropped or duplicated a replication.
    # `--no-write` has nothing stored, so it falls back to the in-memory
    # frame and says so rather than reporting nothing.
    if replications > 0:
        if write:
            null_values = load_null_distribution(engine, run_id, config_hash, split_key)
        else:
            null_values = _in_memory_null(frame)
            report.notes.append("null read from memory: --no-write stored nothing to read back")
        report.null_threshold = core_arms.null_percentile(null_values, bm.null_percentile)
        report.signal_exceeds_null = core_arms.exceeds_null(
            signal_metrics.total_ret, null_values, bm.null_percentile
        )

    return frame, report


def _in_memory_null(frame: pd.DataFrame, column: str = "total_ret") -> list[float]:
    """The pooled null straight off the frame, for `--no-write` only.

    Never used on a run that wrote: 13.2's acceptance is explicit that the
    percentile comes from the stored rows, because that is the only version
    that catches a writer dropping a replication.
    """
    rows = frame.loc[
        (frame["arm"] == ARM_RANDOM) & (frame["era"] == POOLED_ERA) & frame["replication"].notna()
    ]
    return [float(v) for v in rows[column] if v is not None and not np.isnan(float(v))]


def _high_breadth_rows(
    long_events: pd.DataFrame,
    panels: Mapping[str, Panel],
    window: ArmWindow,
    cfg: Config,
    bm: BenchmarkParams,
    rp: ReportingParams,
    prices: Mapping[str, np.ndarray],
    purchases: Sequence[tuple[str, date]],
    config_hash: str,
    replications: int,
    cache: dict[tuple[str, int], Any],
    progress: Any,
) -> tuple[list[BenchmarkRow], str, int]:
    """ADR 099's three-arm comparison on the high-breadth subset alone.

    The direct test of the market-timing hypothesis. If the signal's edge
    lives on broad-selloff days it fires exactly when buy-and-hold is also
    buying cheaply, and the pooled comparison flatters it for the wrong
    reason.

    **Same arm code, not a parallel implementation** (13.6 acceptance):
    buy-and-hold is `simulate_buy_hold` on the same prices and the same
    dates, the null is `random_entries` + `build_positions`, and the signal
    arm is `build_positions` on a filtered entry list. The only thing that
    changes is which entries go in.
    """
    if long_events.empty:
        return [], "high-breadth subset: no long events to split", 0

    labelled = assign_breadth_tercile(long_events, rp)
    subset = labelled.loc[labelled["breadth_tercile"] == HIGH_BREADTH_TERCILE]
    if subset.empty:
        return [], "high-breadth subset: tercile cut produced no high bucket", 0

    entries = entries_from_events(subset)

    # Buy-and-hold restricted to the same dates, which for this subset is
    # the same window: ADR 012's identical-dates rule does not relax because
    # the entry population narrowed.
    hold = core_arms.simulate_buy_hold(prices, window.members, bm, window.dates)
    hold_metrics = core_arms.arm_metrics(hold, bm)
    rows = [
        BenchmarkRow(
            ARM_BUY_HOLD,
            {
                **_metrics_dict(hold_metrics),
                **_tax_row(hold.trades, purchases, hold_metrics.total_ret, bm),
            },
            era=HIGH_BREADTH_ERA,
        )
    ]

    positions = build_positions(entries, panels, window, cfg, Side.LONG, cache)
    sim = core_arms.simulate_portfolio(window.n_days, positions, bm, window.dates)
    metrics = core_arms.arm_metrics(sim, bm)
    rows.append(
        BenchmarkRow(
            ARM_SIGNAL,
            {**_metrics_dict(metrics), **_tax_row(sim.trades, purchases, metrics.total_ret, bm)},
            era=HIGH_BREADTH_ERA,
        )
    )

    pools = eligible_days(panels, window, cfg)
    for replication in range(1, replications + 1):
        drawn = random_entries(entries, pools, config_hash, replication)
        null_positions = build_positions(drawn, panels, window, cfg, Side.LONG, cache)
        null_sim = core_arms.simulate_portfolio(window.n_days, null_positions, bm, window.dates)
        null_metrics = core_arms.arm_metrics(null_sim, bm)
        rows.append(
            BenchmarkRow(
                ARM_RANDOM,
                {
                    **_metrics_dict(null_metrics),
                    **_tax_row(null_sim.trades, purchases, null_metrics.total_ret, bm),
                },
                replication=replication,
                era=HIGH_BREADTH_ERA,
            )
        )
        if progress is not None:
            progress(replication, replications)

    return (
        rows,
        f"high-breadth subset: {len(subset)} events, {len(positions)} positions",
        len(subset),
    )


# ==========================================================================
# Writes and reads
# ==========================================================================


def _to_frame(
    rows: Sequence[BenchmarkRow],
    config_hash: str,
    split_key: str,
    run_id: str,
    git_sha: str,
) -> pd.DataFrame:
    computed_at = datetime.now(UTC)
    records = []
    for row in rows:
        record: dict[str, Any] = dict.fromkeys(BENCHMARK_COLUMNS)
        record.update(
            {
                "run_id": run_id,
                "config_hash": config_hash,
                "arm": row.arm,
                "replication": row.replication,
                "split_key": split_key,
                "era": row.era,
                "computed_at": computed_at,
                "git_sha": git_sha,
            }
        )
        record.update(row.values)
        records.append(record)
    return pd.DataFrame(records, columns=BENCHMARK_COLUMNS)


def write_benchmarks(engine: Engine, rows: pd.DataFrame) -> int:
    """Insert, never upsert.

    `benchmarks` has no natural key — the 200 null replications share
    `(run_id, arm, split_key)` and differ only by `replication`, and a
    re-run is a *new* `run_id`. Rollback is `DELETE FROM benchmarks WHERE
    run_id = ...`, which the session brief names as the whole reversal path.
    """
    if rows.empty:
        return 0
    payload = rows.replace({np.nan: None})
    columns = list(BENCHMARK_COLUMNS)
    statement = text(
        f"INSERT INTO benchmarks ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':{c}' for c in columns)})"
    )
    records = [
        {str(k): _as_param(v) for k, v in record.items()}
        for record in payload[columns].to_dict(orient="records")
    ]
    with engine.begin() as conn:
        conn.execute(statement, records)
    return len(records)


def load_null_distribution(
    engine: Engine,
    run_id: str,
    config_hash: str,
    split_key: str,
    era: str = POOLED_ERA,
    column: str = "total_ret",
) -> list[float]:
    """The stored null, read back for the percentile test (13.2 acceptance).

    Reading this from the database rather than from memory is the point: a
    writer that dropped or duplicated a replication produces a different
    distribution here and the same one in memory.
    """
    with engine.connect() as conn:
        values = (
            conn.execute(
                text(
                    f"SELECT {column} FROM benchmarks WHERE run_id = :run_id "
                    "AND config_hash = :config_hash AND split_key = :split_key "
                    "AND era = :era AND arm = :arm AND replication IS NOT NULL "
                    "ORDER BY replication"
                ),
                {
                    "run_id": run_id,
                    "config_hash": config_hash,
                    "split_key": split_key,
                    "era": era,
                    "arm": ARM_RANDOM,
                },
            )
            .scalars()
            .all()
        )
    return [float(v) for v in values if v is not None]


def _as_param(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float) and np.isnan(value):
        return None
    if value is pd.NaT:
        return None
    return value
