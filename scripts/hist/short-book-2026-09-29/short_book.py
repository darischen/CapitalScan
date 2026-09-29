"""Why the short book loses money (BACKLOG item 10), 2026-09-29.

Reads `shortbook.csv` and `spx.csv`, exported read-only from `wivie`'s
research store by `export.sql` in this folder: every exited, in-trade,
touch-entry event on the live generation (161,728 rows), plus the S&P 500
daily close. Prints the decomposition and day-clustered 95% intervals
reported in RESULTS 2026-09-29.

The market component of a trade is what the S&P alone would have paid
that side over the same window (close before the signal to the exit
close), and the hedged return is `net_ret` minus it: a beta-1 hedge, an
approximation, used to separate market drift from stock selection.

    SHORTBOOK_DIR=<folder with the two CSVs> python short_book.py
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

HERE = os.environ.get("SHORTBOOK_DIR", os.path.dirname(os.path.abspath(__file__)))


def load() -> tuple[pd.DataFrame, pd.Series]:
    trades = pd.read_csv(
        os.path.join(HERE, "shortbook.csv"),
        parse_dates=["signal_date", "exit_date"],
        low_memory=False,
    )
    spx = pd.read_csv(os.path.join(HERE, "spx.csv"), parse_dates=["ts"]).set_index("ts")
    return trades, spx["spx_close"]


def close_before(spx: pd.Series, dates: pd.Series) -> np.ndarray:
    idx = np.clip(spx.index.searchsorted(dates) - 1, 0, len(spx) - 1)
    return spx.iloc[idx].to_numpy()


def close_on(spx: pd.Series, dates: pd.Series) -> np.ndarray:
    idx = np.clip(spx.index.searchsorted(dates, side="right") - 1, 0, len(spx) - 1)
    return spx.iloc[idx].to_numpy()


def enrich(trades: pd.DataFrame, spx: pd.Series) -> pd.DataFrame:
    d = trades.copy()
    d["spx_ret"] = close_on(spx, d["exit_date"]) / close_before(spx, d["signal_date"]) - 1
    sign = np.where(d["side"] == "long", 1, -1)
    d["mkt_component"] = sign * d["spx_ret"]
    d["hedged"] = d["net_ret"] - d["mkt_component"]
    sma = spx.rolling(200).mean()
    idx = np.clip(spx.index.searchsorted(d["signal_date"]) - 1, 0, len(spx) - 1)
    d["spx_above"] = close_before(spx, d["signal_date"]) > sma.iloc[idx].to_numpy()
    return d


def clustered(x: pd.Series, dates: pd.Series) -> str:
    """Mean with a 95% interval clustered by signal date."""
    v = x.to_numpy(float)
    n, m = len(v), v.mean()
    by_day = pd.DataFrame({"x": v - m, "d": dates.to_numpy()}).groupby("d")["x"].sum()
    k = len(by_day)
    se = np.sqrt((by_day**2).sum() * k / (k - 1)) / n
    lo, hi = (m - 1.96 * se) * 100, (m + 1.96 * se) * 100
    return f"{m * 100:+.3f}% [{lo:+.3f}, {hi:+.3f}] n={n:,} days={k:,}"


def main() -> None:
    trades, spx = load()
    d = enrich(trades, spx)
    short, long_ = d["side"] == "short", d["side"] == "long"

    print("exit mix and payoffs by side")
    mix = d.groupby("side")["exit_reason"].value_counts(normalize=True).unstack().round(3)
    print(mix.to_string())
    print(d.pivot_table(index="exit_reason", columns="side", values="net_ret").round(4))
    print("cost (gross - net):", (d["gross_ret"] - d["net_ret"]).groupby(d["side"]).mean().round(5))

    checks = [
        ("short net", short, "net_ret"),
        ("short market component", short, "mkt_component"),
        ("short hedged", short, "hedged"),
        ("long net", long_, "net_ret"),
        ("long hedged", long_, "hedged"),
        ("short net, SPX above 200d", short & d["spx_above"], "net_ret"),
        ("short net, SPX below 200d", short & ~d["spx_above"], "net_ret"),
        ("short hedged, SPX above 200d", short & d["spx_above"], "hedged"),
    ]
    for kind in ("bear_close_above_upper", "bb_upper_touch", "confluence_high"):
        checks.append((f"short net, {kind}", short & (d["signal_type"] == kind), "net_ret"))
    checks.append(
        (
            "long net, bull_close_below_lower",
            long_ & (d["signal_type"] == "bull_close_below_lower"),
            "net_ret",
        )
    )
    print("\nday-clustered 95% intervals")
    for name, mask, col in checks:
        print(f"  {name:36} {clustered(d.loc[mask, col], d.loc[mask, 'signal_date'])}")
    print(f"\nshorts fired with SPX above its 200d: {d.loc[short, 'spx_above'].mean():.3f}")
    print(f"mean SPX move during short holds: {d.loc[short, 'spx_ret'].mean() * 100:+.3f}%")


if __name__ == "__main__":
    main()
