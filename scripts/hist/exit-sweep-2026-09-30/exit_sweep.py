"""Does a different exit rescue the short book? (BACKLOG item 10, 2026-09-30)

A path simulation, not a backtest. For every exited, in-trade, touch-entry
event on the live generation it replays `path` (each day's best, worst and
closing return from the entry, side-adjusted) under a grid of exits:

    target   2%, 3%, 4%, 5%, 7%, none
    stop     1, 1.5, 2, 3 ATR, none
    hold     1, 2, 3, 5, 7, 10 sessions

A day that reaches both the stop and the target counts as a stop. Stops and
targets fill exactly at their level, so gaps are not charged. The
stochastic and band exits are not simulated (about 2% of real exits). Costs
are each side's measured mean. The check on all of that is the first line
printed per side: the simulated current policy against the real `net_ret`.

Inputs are `ev.csv` and `path.csv` from `export.sql`.

    EXIT_SWEEP_DIR=<folder with the CSVs> python exit_sweep.py
"""

from __future__ import annotations

import itertools
import os

import numpy as np
import pandas as pd

HERE = os.environ.get("EXIT_SWEEP_DIR", os.path.dirname(os.path.abspath(__file__)))
COST = {"short": 0.00067, "long": 0.00060}
CURRENT = (0.05, 2.0, 5)
NAMED = {
    "current": CURRENT,
    "1 ATR / 1d": (None, 1.0, 1),
    "1 ATR / 3d": (None, 1.0, 3),
    "none / 10d": (None, None, 10),
    "5% / 2 ATR / 10d": (0.05, 2.0, 10),
}
MAX_DAY = 15


def load() -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    ev = pd.read_csv(os.path.join(HERE, "ev.csv"), parse_dates=["signal_date"])
    pa = pd.read_csv(os.path.join(HERE, "path.csv"))
    ev = ev[ev["id"].isin(pa["event_id"])].reset_index(drop=True)
    row = pd.Series(np.arange(len(ev)), index=ev["id"]).loc[pa["event_id"]].to_numpy()
    col = pa["day_offset"].to_numpy()
    grids = {}
    for name in ("favorable", "adverse", "terminal"):
        g = np.full((len(ev), MAX_DAY + 1), np.nan)
        g[row, col] = pa[name].to_numpy()
        grids[name] = g
    return ev, grids


def simulate(
    grids: dict[str, np.ndarray],
    atr: np.ndarray,
    mask: np.ndarray,
    target: float | None,
    stop_k: float | None,
    hold: int,
) -> tuple[np.ndarray, np.ndarray]:
    fav, adv, ter = (grids[n][mask] for n in ("favorable", "adverse", "terminal"))
    a = atr[mask]
    ret = np.full(len(a), np.nan)
    why = np.full(len(a), "timeout", dtype=object)
    live = np.ones(len(a), bool)
    for d in range(1, hold + 1):
        if stop_k is not None:
            hit = live & (adv[:, d] <= -stop_k * a)
            ret[hit], why[hit] = -stop_k * a[hit], "stop"
            live &= ~hit
        if target is not None:
            hit = live & (fav[:, d] >= target)
            ret[hit], why[hit] = target, "target"
            live &= ~hit
        live &= ~np.isnan(ter[:, d])  # the path ended; dropped, never filled
    ret[live] = ter[live, hold]
    return ret, why


def clustered(x: np.ndarray, dates: np.ndarray) -> str:
    ok = ~np.isnan(x)
    x, dates = x[ok], dates[ok]
    m = x.mean()
    by_day = pd.Series(x - m).groupby(dates).sum()
    k = len(by_day)
    se = np.sqrt((by_day**2).sum() * k / (k - 1)) / len(x)
    return f"{m * 100:+.3f} [{(m - 1.96 * se) * 100:+.3f}, {(m + 1.96 * se) * 100:+.3f}]"


def main() -> None:
    ev, grids = load()
    atr = ev["atr_pct"].to_numpy(float)
    dates_all = ev["signal_date"].to_numpy()
    targets = (0.02, 0.03, 0.04, 0.05, 0.07, None)
    stops = (1.0, 1.5, 2.0, 3.0, None)
    holds = (1, 2, 3, 5, 7, 10)
    for side in ("short", "long"):
        m = (ev["side"] == side).to_numpy()
        dates = dates_all[m]
        early = dates < np.datetime64("2019-01-01")
        cost = COST[side]
        base, why = simulate(grids, atr, m, *CURRENT)
        mix = pd.Series(why).value_counts(normalize=True).round(3).to_dict()
        print(f"\n===== {side}: n={m.sum():,}")
        print(f"real net {ev.loc[m, 'net_ret'].mean() * 100:+.3f}%, ", end="")
        print(f"simulated current policy {(np.nanmean(base) - cost) * 100:+.3f}%, exits {mix}")
        drift = {}
        for d in range(1, 11):
            drift[d] = round(float((np.nanmean(grids["terminal"][m, d]) - cost) * 100), 3)
        print(f"no stop or target, exit at the close of day N: {drift}")

        rows = []
        for target, stop_k, hold in itertools.product(targets, stops, holds):
            r, w = simulate(grids, atr, m, target, stop_k, hold)
            r = r - cost
            rows.append(
                {
                    "target": target,
                    "stop_k": stop_k,
                    "hold": hold,
                    "net": np.nanmean(r) * 100,
                    "pre2019": np.nanmean(r[early]) * 100,
                    "from2019": np.nanmean(r[~early]) * 100,
                    "win": np.nanmean(r > 0),
                    "target_rate": (w == "target").mean(),
                    "stop_rate": (w == "stop").mean(),
                }
            )
        grid = pd.DataFrame(rows)
        grid.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"grid_{side}.csv"))
        print("top 8 by net, % per trade")
        print(grid.sort_values("net", ascending=False).head(8).round(3).to_string(index=False))
        pick = grid.loc[grid["pre2019"].idxmax()]
        target = None if pd.isna(pick["target"]) else float(pick["target"])
        stop_k = None if pd.isna(pick["stop_k"]) else float(pick["stop_k"])
        r, _ = simulate(grids, atr, m, target, stop_k, int(pick["hold"]))
        print(
            f"chosen on pre-2019: target={target} stop={stop_k} hold={int(pick['hold'])}, "
            f"pre-2019 {pick['pre2019']:+.3f}%, from 2019 {pick['from2019']:+.3f}%, "
            f"all years {clustered(r - cost, dates)}"
        )
        for kind in sorted(ev.loc[m, "signal_type"].unique()):
            mm = m & (ev["signal_type"] == kind).to_numpy()
            parts = [f"  {kind:24} n={mm.sum():6,}"]
            for name, policy in NAMED.items():
                r, _ = simulate(grids, atr, mm, *policy)
                parts.append(f"{name} {clustered(r - cost, dates_all[mm])}")
            print(" | ".join(parts))


if __name__ == "__main__":
    main()
