#!/usr/bin/env python
"""Time `core.exits.resolve_exit` on a production-shaped workload.

Written 2026-11-18 for the BACKLOG item "`core.exits.resolve_exit` builds
three pandas Series per forward bar". Not a test and not run in CI: it takes
tens of seconds and measures a machine, not a contract. The contract (new and
old produce identical results) is
`capitalscan/tests/property/test_resolve_exit_equivalence.py`.

**Frames are shaped the way `research.backtest` hands them over**, because
the cost being measured depends on it. Bars carry `ts` and `ticker` beside
the floats and are indexed by `ts` with `drop=False`, so a row is a
mixed-dtype object Series; the indicator frame carries `ts`, `ticker` and
`config_hash` the same way. A float-only fixture would make `.iloc[i]` look
cheaper than it is in the backtest.

The workload is deterministic in `--seed`: random-walk bars, one entry every
`--stride` bars on alternating sides, under the baseline `ExitParams`. The
frozen pre-2026-11-18 implementation lives in the equivalence test module and
is timed alongside the live one in the same process, so both see the same
interpreter, the same inputs and the same machine state.

    uv run python scripts/bench_resolve_exit.py --events 4000
    uv run python scripts/bench_resolve_exit.py --events 4000 --profile
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import time
from collections.abc import Callable

import numpy as np
import pandas as pd

from capitalscan.core.config import ExitParams
from capitalscan.core.exits import resolve_exit
from capitalscan.core.types import ExitResult, Side
from capitalscan.tests.property.test_resolve_exit_equivalence import reference_resolve_exit

Call = tuple[float, int, Side, pd.DataFrame, pd.DataFrame, float, pd.Series]


def make_frames(seed: int, n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One ticker's bars and indicators, production-shaped."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.015, n)))
    spread = np.abs(rng.normal(0.0, 0.012, n)) * close
    ts = pd.date_range("2010-01-04", periods=n, freq="B")
    bars = pd.DataFrame(
        {
            "ts": ts,
            "ticker": "BENCH",
            "open": np.round(close + rng.normal(0.0, 0.004, n) * close, 4),
            "high": np.round(close + spread, 4),
            "low": np.round(close - spread, 4),
            "close": np.round(close, 4),
            "adj_close": np.round(close, 4),
            "volume": rng.integers(1_000_000, 9_000_000, n).astype(float),
        }
    ).set_index("ts", drop=False)
    mid = pd.Series(close).rolling(20, min_periods=1).mean().to_numpy()
    sd = pd.Series(close).rolling(20, min_periods=2).std().fillna(0.0).to_numpy()
    lo14 = pd.Series(close - spread).rolling(14, min_periods=1).min().to_numpy()
    hi14 = pd.Series(close + spread).rolling(14, min_periods=1).max().to_numpy()
    k = 100.0 * (close - lo14) / np.where(hi14 > lo14, hi14 - lo14, 1.0)
    atr = pd.Series(2.0 * spread).rolling(14, min_periods=1).mean().to_numpy()
    ind = pd.DataFrame(
        {
            "ticker": "BENCH",
            "ts": ts,
            "config_hash": "f183b0f5209a4677",
            "bb_mid": mid,
            "bb_upper": mid + 2.0 * sd,
            "bb_lower": mid - 2.0 * sd,
            "k_full": k,
            "k_fast": k,
            "atr_14": atr,
        },
        index=bars.index,
    )
    return bars, ind


def make_calls(seed: int, events: int, stride: int, ep: ExitParams) -> list[Call]:
    bars_per_ticker = 2_000
    calls: list[Call] = []
    ticker = 0
    while len(calls) < events:
        bars, ind = make_frames(seed + ticker, bars_per_ticker)
        for entry_idx in range(25, bars_per_ticker - 1, stride):
            if len(calls) >= events:
                break
            fwd = slice(entry_idx + 1, entry_idx + 1 + ep.max_hold_days)
            ind_at_entry = ind.iloc[entry_idx]
            side = Side.LONG if len(calls) % 2 == 0 else Side.SHORT
            calls.append(
                (
                    float(bars["open"].iloc[entry_idx]),
                    entry_idx,
                    side,
                    bars.iloc[fwd],
                    ind.iloc[fwd],
                    float(ind_at_entry["atr_14"]),
                    ind_at_entry,
                )
            )
        ticker += 1
    return calls


def run(fn: Callable[..., ExitResult], calls: list[Call], ep: ExitParams) -> list[ExitResult]:
    return [
        fn(
            entry_price=entry,
            entry_idx=idx,
            side=side,
            fwd_bars=fb,
            fwd_ind=fi,
            atr_at_entry=atr,
            ep=ep,
            ind_at_entry=at_entry,
        )
        for entry, idx, side, fb, fi, atr, at_entry in calls
    ]


def best_of(
    fn: Callable[..., ExitResult], calls: list[Call], ep: ExitParams, repeats: int
) -> float:
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        run(fn, calls, ep)
        times.append(time.perf_counter() - t0)
    return min(times)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events", type=int, default=4_000)
    ap.add_argument("--stride", type=int, default=7)
    ap.add_argument("--seed", type=int, default=20261118)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--profile", action="store_true", help="cProfile both, top 15 by tottime")
    args = ap.parse_args()

    ep = ExitParams()
    calls = make_calls(args.seed, args.events, args.stride, ep)
    old = run(reference_resolve_exit, calls, ep)
    new = run(resolve_exit, calls, ep)
    if old != new:
        raise SystemExit("MISMATCH: live and reference resolve_exit disagree")
    reasons = pd.Series([r.reason.value for r in new]).value_counts().to_dict()
    mean_hold = float(np.mean([r.holding_days for r in new]))
    print(f"events {len(calls)}, mean holding {mean_hold:.2f} bars, reasons {reasons}")
    print("results identical: yes")

    if args.profile:
        for name, fn in (("reference", reference_resolve_exit), ("live", resolve_exit)):
            prof = cProfile.Profile()
            prof.enable()
            run(fn, calls, ep)
            prof.disable()
            print(f"\n--- {name} ---")
            pstats.Stats(prof).sort_stats("tottime").print_stats(15)
        return

    t_old = best_of(reference_resolve_exit, calls, ep, args.repeats)
    t_new = best_of(resolve_exit, calls, ep, args.repeats)
    n = len(calls)
    print(f"reference {t_old:.3f}s  ({t_old / n * 1e6:.1f} us/call)")
    print(f"live      {t_new:.3f}s  ({t_new / n * 1e6:.1f} us/call)")
    print(f"speedup   {t_old / t_new:.2f}x  (best of {args.repeats})")


if __name__ == "__main__":
    main()
