"""Strategy-level return summaries, counting **every fire** (ADR 165).

**Why this module exists.** `net_ret` is written per event by
`research/enrich.py`, so every fire already carries its own return. What was
missing was a single place that *aggregates* them. Every strategy-return
figure in `RESULTS.md` before 2026-09-01 was hand-rolled SQL written afresh
for that entry, and the hazard is not hypothetical: the first pass at the
`max_hold_days` comparison filtered `is_cluster_head` and produced three
*different populations* -- 104,460 / 78,432 / 51,832 rows for hold 3 / 5 /
10 -- which read as a non-monotonic result with the splits disagreeing.

Rewritten without the filter, the same query then omitted `in_trade` and
averaged `next_open` and `touch` together: n 310,749 against the correct
163,424, and a train mean of -2.77 bp against -1.94. **This module found
that second error within minutes of being written, against the very entry
that motivated it.** Neither mistake was arithmetic; both were about which
rows were being averaged, which is why `BASE_PREDICATES` is a constant and
`entry_kind` has a default rather than being left to the caller.

**The split this module implements.** `events` serves two questions that
want opposite populations, and one filter was answering both:

- **Returns** -- "what would this policy have made" -- must count every
  fire. They are real trades with real money attached, and the
  cluster-head filter discards 74% of them (measured 2026-08-29), of which
  the discarded ones are *better* by 7-9 bp. Every number in both exit
  sweeps is measured on the worse quarter of the population.
- **`cell_stats` and `benchmarks`** -- "is this cell distinguishable from
  its baseline" -- keep `is_cluster_head` until a serial `n_eff` exists.
  The extra observations are serially dependent by construction, and
  dropping the filter takes train `n` from 78k to 311k, narrowing every
  interval about twofold. That is the direction that manufactures
  significance, and ADR 098's `rho` corrects only the cross-sectional half.

**The sharper reason, found during the `max_hold_days` sweep.** The filter
is **not invariant to the parameter being swept**. Cluster membership is
computed from `days_since_head`, which depends on `max_hold_days`, so
filtering during that sweep compared three different populations and could
have invented an effect that was not there. For any sweep touching a
parameter clustering depends on, filtering is not merely conservative -- it
is wrong.

**What this module does not do.** It does not touch `cell_stats`,
`benchmarks`, or any view. Nothing that reports a probability or a
confidence interval changes; invariant 8 is unaffected. This adds a
reader, not a rule.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

# The two predicates that are *not* in question, kept here so a caller
# cannot quietly measure a different population than `cell_stats` does for
# reasons unrelated to clustering.
#
# `in_trade` -- ADR 122. Detection records trade-universe membership rather
# than filtering on it, so omitting this widens the population ~4.4x and
# nothing about the output looks wrong.
#
# `net_ret IS NOT NULL` -- an event whose forward window has not closed has
# no return yet. Averaging over those rows would treat absent as zero,
# which is the direction invariant 4 exists to forbid.
BASE_PREDICATES = (
    "config_hash = :config_hash",
    "in_trade",
    "net_ret IS NOT NULL",
)

# Columns a caller may group by. A closed list rather than free text: an
# unrecognised name would otherwise reach the SQL string, and this module
# builds its own statement.
GROUPABLE = frozenset(
    {"split_key", "era", "side", "signal_type", "dd_bucket", "bw_regime", "exit_reason"}
)


class UngroupableColumn(ValueError):
    """Raised for a `by` this module will not interpolate into SQL."""


def _summary_select() -> list[str]:
    """The one aggregation, so two callers cannot restate it differently.

    **`percentile_cont` returns double precision**, which `round(v, n)` has
    no overload for -- the `::numeric` casts are required, not cosmetic.
    Left explicit because the error they prevent, `function round(double
    precision, integer) does not exist`, names neither the column nor the
    cause. It cost a query on 2026-09-01.
    """
    return [
        "count(*) AS n",
        "round((avg(net_ret) * 10000)::numeric, 2) AS mean_bp",
        "round((stddev(net_ret) * 100)::numeric, 2) AS sd_pct",
        "round(((percentile_cont(0.01) WITHIN GROUP (ORDER BY net_ret)) * 100)::numeric, 2)"
        " AS p1_pct",
        "round(((percentile_cont(0.001) WITHIN GROUP (ORDER BY net_ret)) * 100)::numeric, 2)"
        " AS p01_pct",
        "round((min(net_ret) * 100)::numeric, 2) AS worst_pct",
        "round((count(*) FILTER (WHERE exit_reason = 'stop')) * 100.0 / count(*), 1)"
        " AS stopped_pct",
        "round((count(*) FILTER (WHERE exit_reason = 'target')) * 100.0 / count(*), 1)"
        " AS target_pct",
        "round((count(*) FILTER (WHERE exit_reason = 'timeout')) * 100.0 / count(*), 1)"
        " AS timeout_pct",
    ]


def strategy_returns(
    engine: Engine,
    config_hash: str,
    *,
    split_key: str | None = None,
    entry_kind: str = "next_open",
    by: str | None = None,
    cluster_heads_only: bool = False,
) -> pd.DataFrame:
    """Return distribution for one config, counting every fire by default.

    `cluster_heads_only` exists to *reproduce* a pre-2026-09-01 figure, not
    for new work. It defaults to `False` because the strategy's return is
    the return of the trades it takes, and the filter drops 74% of them.

    `by` groups the result, and must name a column in `GROUPABLE`.
    """
    if by is not None and by not in GROUPABLE:
        raise UngroupableColumn(f"{by!r} is not groupable; expected one of {sorted(GROUPABLE)}")

    where = [*BASE_PREDICATES, "entry_kind = :entry_kind"]
    # `dict[str, str]`, not `object`: every bound value here is a string,
    # and pandas-stubs types `params` narrowly enough that `object` fails.
    params: dict[str, str] = {"config_hash": config_hash, "entry_kind": entry_kind}
    if split_key is not None:
        where.append("split_key = :split_key")
        params["split_key"] = split_key
    if cluster_heads_only:
        where.append("is_cluster_head")

    select = ([by] if by else []) + _summary_select()
    sql = f"SELECT {', '.join(select)} FROM events WHERE {' AND '.join(where)}"  # noqa: S608
    if by:
        sql += f" GROUP BY {by} ORDER BY {by}"

    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def compare(
    engine: Engine,
    config_hashes: dict[str, str],
    *,
    split_key: str = "train",
    entry_kind: str = "next_open",
    cluster_heads_only: bool = False,
) -> pd.DataFrame:
    """One row per arm, for a sweep table.

    Takes `{label: config_hash}` so the output carries the arm names a
    reader recognises rather than sixteen hex characters.

    **Populations are reported, not assumed.** Arms of one sweep must be
    measured over the same rows, and the `max_hold_days` sweep showed that
    is not automatic. `n` is kept in the output for exactly that reason: if
    it differs across arms of a sweep that should not change the
    population, the table is not measuring what it claims.
    """
    rows = []
    for label, chash in config_hashes.items():
        frame = strategy_returns(
            engine,
            chash,
            split_key=split_key,
            entry_kind=entry_kind,
            cluster_heads_only=cluster_heads_only,
        )
        if frame.empty or int(frame.iloc[0]["n"]) == 0:
            continue
        row = frame.iloc[0].to_dict()
        row["arm"] = label
        row["config_hash"] = chash
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    lead = ["arm", "config_hash", "n", "mean_bp"]
    return out[[*lead, *[c for c in out.columns if c not in lead]]]
