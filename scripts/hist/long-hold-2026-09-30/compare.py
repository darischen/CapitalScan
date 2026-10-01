"""Long-hold backtest arms against the live generation (2026-09-30).

Reads the workstation research copy, where `run_arms.sh` wrote both arms.
Same events in every arm: touch entry, in trade, exited, signal date on or
before 2026-08-20 so a 10-day hold has closed. Intervals are 95%, clustered
by signal date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import sqlalchemy as sa

ARMS = {
    "f183b0f5209a4677": "current 5d / 2 ATR / 5%",
    "47beeecbd6d41696": "10d / 2 ATR / 5%",
    "a90d4561f883c4a5": "10d / 3 ATR / no target",
}
QUERY = """
SELECT config_hash, signal_date, side, signal_type, exit_reason, holding_days, net_ret::float
  FROM events
 WHERE config_hash = ANY(:arms) AND entry_kind = 'touch' AND in_trade
   AND exit_reason IS NOT NULL AND exit_reason <> 'unfinished' AND net_ret IS NOT NULL
   AND signal_date <= '2026-08-20'
"""


def summary(g: pd.DataFrame) -> str:
    x = g["net_ret"].to_numpy()
    m = x.mean()
    by_day = pd.Series(x - m).groupby(g["signal_date"].to_numpy()).sum()
    k = len(by_day)
    se = np.sqrt((by_day**2).sum() * k / (k - 1)) / len(x)
    hold = g["holding_days"].mean()
    return (
        f"{m * 100:+.3f} [{(m - 1.96 * se) * 100:+.3f}, {(m + 1.96 * se) * 100:+.3f}] "
        f"n={len(x):,} hold={hold:.1f}d per_day={m * 100 / hold:+.4f} win={np.mean(x > 0):.3f}"
    )


def main() -> None:
    engine = sa.create_engine("postgresql+psycopg://capscan:capscan@localhost:5432/capitalscan")
    d = pd.read_sql(sa.text(QUERY), engine, params={"arms": list(ARMS)})
    d["arm"] = pd.Categorical(d["config_hash"].map(ARMS), list(ARMS.values()))
    for (side, arm), g in d.groupby(["side", "arm"], observed=True):
        print(f"{side:5} {arm:26} {summary(g)}")
    longs = d[d["side"] == "long"]
    for (kind, arm), g in longs.groupby(["signal_type", "arm"], observed=True):
        print(f"  {kind:24} {arm:26} {summary(g)}")
    early = pd.to_datetime(longs["signal_date"]) < "2019-01-01"
    longs = longs.assign(period=np.where(early, "pre2019", "from2019"))
    for (period, arm), g in longs.groupby(["period", "arm"], observed=True):
        print(f"  {period:9} {arm:26} {summary(g)}")
    mix = d.groupby(["side", "arm"], observed=True)["exit_reason"].value_counts(normalize=True)
    print(mix.unstack().round(3).to_string())


if __name__ == "__main__":
    main()
