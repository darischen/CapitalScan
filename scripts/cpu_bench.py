#!/usr/bin/env python
"""Compare two machines on *this* workload, sustained.

Written 2026-08-28 to decide whether a Ryzen 9 5950HS laptop (ASUS ROG Flow
X13, 35W / 54W turbo, LPDDR4X-4266) should take arms off the Ryzen 7 3700X
workstation (65W, DDR4-3600) during the exit sweep.

**Why not Cinebench.** The question is not which chip is faster in general.
It is whether that chip is faster at *this* loop -- pandas rolling windows,
per-bar `detect`, per-entry `resolve_exit` -- for *two hours without
stopping*. A 30-second burst benchmark answers neither half.

**Why it needs no database.** `core/` performs no IO by invariant 1, so the
whole hot path can be driven from synthetic bars. The laptop needs the repo
and `uv sync`, nothing else -- no Postgres, no 22 GB copy.

**The number that decides it is `sustain`, not the mean.** Every laptop wins
the first minute; the Flow X13 boosts to 4.6 GHz in a 13-inch chassis and
then has to live inside 35W. This reports throughput per 30s bucket and the
ratio of the last full minute to the first, so thermal decay is visible
rather than averaged away:

    sustain = (rate over final 60s) / (rate over first 60s)

A desktop with real cooling sits near 1.00. A laptop that drops to 0.70 is
30% slower than its own opening minute for the remaining 118 of 120.

**Deterministic inputs.** One seed, so both machines grind identical numbers
and a difference is the machine rather than the data. The bars are random
walks, which is fine here: the cost of `bollinger` or `resolve_exit` depends
on the array length and the branch mix, not on whether the prices are
realistic. `detect`'s hit rate does depend on it, so the generator is tuned
to fire on roughly 3% of bars, close to production.

Run identically on both machines:

    uv run python scripts/cpu_bench.py --workers 8 --seconds 600

`--workers` should be the physical core count on both (8 here, 8 there), not
the thread count, so the comparison is core-for-core.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import platform
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

BARS_PER_TICKER = 2_000  # ~8 years of daily bars, the real per-ticker shape
SEED = 20260828


def make_bars(seed: int, n: int = BARS_PER_TICKER) -> pd.DataFrame:
    """One ticker's OHLCV as a random walk, deterministic in `seed`."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.015, n)
    close = 100.0 * np.exp(np.cumsum(steps))
    spread = np.abs(rng.normal(0.0, 0.008, n)) * close
    return pd.DataFrame(
        {
            "ts": pd.date_range("2015-01-02", periods=n, freq="B"),
            "ticker": "BENCH",
            "open": close + rng.normal(0.0, 0.004, n) * close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "adj_close": close,
            "volume": rng.integers(1_000_000, 9_000_000, n).astype(float),
        }
    )


def _one_unit(seed: int) -> int:
    """One ticker's worth of the compute hot path. Returns entries resolved.

    Imports live inside the worker: `ProcessPoolExecutor` uses **spawn** on
    Windows, so a worker re-imports the module and top-level heavy imports
    would be paid per task rather than once.
    """
    from capitalscan.core.config import ExitParams, IndicatorParams, SignalParams
    from capitalscan.core.exits import resolve_exit
    from capitalscan.core.indicators import atr, bollinger, realized_vol, stochastic
    from capitalscan.core.signals import detect
    from capitalscan.core.types import Side

    ip, sp, ep = IndicatorParams(), SignalParams(), ExitParams()
    bars = make_bars(seed)

    ind = bars[["ts", "ticker"]].copy()
    for fn in (bollinger, stochastic, atr, realized_vol):
        out = fn(bars, ip)
        for col in out.columns:
            if col not in ("ts", "ticker"):
                ind[col] = out[col].to_numpy()

    # Indicators are read at t-1, never t (invariant 3) -- the shift is part
    # of the work being measured, so it stays.
    prior = ind.shift(1)

    resolved = 0
    warmup = 60
    for i in range(warmup, len(bars) - ep.max_hold_days - 1):
        hits = detect(bars.iloc[i], prior.iloc[i], sp)
        if not hits:
            continue
        entry_idx = i + 1
        fwd = slice(entry_idx + 1, entry_idx + 1 + ep.max_hold_days)
        atr_at_entry = float(ind.iloc[entry_idx].get("atr_14", float("nan")))
        for _hit in hits:
            resolve_exit(
                entry_price=float(bars.iloc[entry_idx]["open"]),
                entry_idx=entry_idx,
                side=Side.LONG,
                fwd_bars=bars.iloc[fwd],
                fwd_ind=ind.iloc[fwd],
                atr_at_entry=atr_at_entry,
                ep=ep,
                ind_at_entry=ind.iloc[entry_idx],
            )
            resolved += 1
    return resolved


def main() -> None:
    ap = argparse.ArgumentParser(description="Sustained CPU benchmark on the compute hot path.")
    ap.add_argument("--workers", type=int, default=8, help="physical cores, not threads")
    ap.add_argument("--seconds", type=int, default=600, help="wall clock to sustain")
    ap.add_argument("--bucket", type=int, default=30, help="reporting bucket, seconds")
    args = ap.parse_args()

    print(f"machine : {platform.processor() or platform.machine()}")
    print(f"python  : {platform.python_version()}  cores={mp.cpu_count()}")
    print(f"workers : {args.workers}   duration: {args.seconds}s   seed: {SEED}")
    print("warming up (one unit, so the first bucket is not import cost)...", flush=True)
    t0 = time.perf_counter()
    n = _one_unit(SEED)
    print(f"one unit = {n} entries resolved in {time.perf_counter() - t0:.1f}s\n", flush=True)

    done_at: list[float] = []
    start = time.perf_counter()
    deadline = start + args.seconds
    seed = SEED

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(_one_unit, seed + k) for k in range(args.workers * 2)}
        seed += args.workers * 2
        print(f"{'elapsed':>8}  {'units':>6}  {'units/s':>8}")
        next_report = args.bucket
        while pending:
            for fut in as_completed(pending, timeout=None):
                pending.discard(fut)
                fut.result()
                done_at.append(time.perf_counter() - start)
                if time.perf_counter() < deadline:
                    pending.add(pool.submit(_one_unit, seed))
                    seed += 1
                elapsed = time.perf_counter() - start
                if elapsed >= next_report:
                    lo = next_report - args.bucket
                    in_bucket = sum(1 for t in done_at if lo <= t < next_report)
                    print(
                        f"{next_report:>7.0f}s  {in_bucket:>6d}  {in_bucket / args.bucket:>8.3f}",
                        flush=True,
                    )
                    next_report += args.bucket
                break
            if time.perf_counter() >= deadline and not pending:
                break

    total = time.perf_counter() - start
    first = sum(1 for t in done_at if t < 60.0) / 60.0
    last = sum(1 for t in done_at if total - 60.0 <= t < total) / 60.0
    print(f"\ntotal   : {len(done_at)} units in {total:.0f}s = {len(done_at) / total:.3f} units/s")
    print(f"first60 : {first:.3f} units/s")
    print(f"last60  : {last:.3f} units/s")
    if first > 0:
        print(f"sustain : {last / first:.3f}   (1.00 = no thermal decay)")
    print("\nCompare `total` for raw speed and `sustain` for whether it holds up.")


if __name__ == "__main__":
    main()
