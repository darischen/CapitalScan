"""The `cell_stats` writer (Session 12.3 and 12.4, DESIGN §6.7-§6.11).

Session 11 built the statistical machinery and proved it on synthetic data.
This module points it at real events.

`core/cells.py` owns the grid and the identifier, `core/stats.py` owns the
formulas, `core/baselines.py` owns the baseline arithmetic. What lives here
is the part that has to be right about *which rows*: the five consumer
rules the session brief lists are all silent-wrong-number failures, none of
which raises anything.

**The event population.** ADR 102's published table was measured on
`is_cluster_head AND entry_kind = 'next_open'`, with a closed forward
window. Neither filter is stated in the ADR, both were recovered by
measurement on 2026-08-11, and both are load-bearing: dropping the
cluster-head filter multiplies every `n` by roughly four, and dropping the
entry-kind filter multiplies it by four again. `GRID_ENTRY_KIND` and
`GRID_CLUSTER_HEADS_ONLY` below are those filters, named so a future
reader does not have to re-derive them.

**`p_hit` and the baseline are the same quantity.** Both are
`P(signed return over the horizon >= target)`, measured on the event's own
side. The `p_touch_*` columns are a different question — did the price
*ever* reach the target intraday — and they are reported alongside rather
than substituted, because `edge = p_hit - baseline` is only meaningful
when the two terms measure the same event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.baselines import event_weighted_baseline
from capitalscan.core.cells import (
    CellSpec,
    cell_id_for,
    dd_bucket_labels,
    headline_grid,
    holdout_era,
    reported_eras,
    suppression_reason,
)
from capitalscan.core.config import BaselineParams, Config, ReportingParams, StatsParams
from capitalscan.core.stats import (
    benjamini_hochberg,
    effective_sample_size,
    one_sided_p_value,
    rho_bar_for_correction,
    wilson_ci,
)
from capitalscan.research.baselines import attach_event_baselines

# ADR 102's measured population. See the module docstring.
GRID_ENTRY_KIND = "next_open"
GRID_CLUSTER_HEADS_ONLY = True

# `next_open` enters one bar after the signal, so a closed forward window
# needs `entry_offset + max_hold_days` bars. Derived rather than written
# down, since `max_hold_days` is sweepable.
NEXT_OPEN_ENTRY_OFFSET = 1

CELL_STATS_COLUMNS = [
    "cell_id",
    "run_id",
    "config_hash",
    "signal_type",
    "dd_bucket",
    "signal_strength",
    "side",
    "entry_kind",
    "split_key",
    "era",
    "horizon_days",
    "target_pct",
    "n_events",
    "n_eff",
    "n_tickers",
    "mean_cofire",
    "p_hit",
    "baseline_empirical",
    "baseline_parametric",
    "edge",
    "ci_low",
    "ci_high",
    "p_value_randomization",
    "p_value_parametric",
    "q_value",
    "mean_ret",
    "median_ret",
    "ret_p25",
    "ret_p75",
    "mean_mfe",
    "mean_mae",
    "median_time_to_mfe",
    "capture_ratio",
    "p_touch_2pct",
    "p_touch_3pct",
    "p_touch_5pct",
    "p_touch_10pct",
    "median_day_touch_5pct",
    "exit_mix",
    "earnings_frac",
    "suppressed",
    "suppress_reason",
    "computed_at",
    "git_sha",
]

# Nulled on a suppressed cell. Counts are deliberately absent: `n_events`
# and `n_eff` are how a reader sees *why* it suppressed.
RENDERED_COLUMNS = ("p_hit", "edge", "ci_low", "ci_high", "p_value_parametric", "q_value")


@dataclass(frozen=True)
class SelectionReport:
    """What `select_grid_events` dropped, so the exclusions are stated
    rather than silent (12.2 acceptance)."""

    n_in: int
    n_out: int
    excluded_null_dd: int
    excluded_deep_dd: int
    excluded_open_window: int

    def summary(self) -> str:
        return (
            f"grid selection: {self.n_in} -> {self.n_out} events; "
            f"excluded {self.excluded_null_dd} null dd_bucket, "
            f"{self.excluded_deep_dd} deep dd_bucket (ADR 101), "
            f"{self.excluded_open_window} open forward window"
        )


def min_fwd_window_for(cfg: Config) -> int:
    """The forward-window floor an event must clear to carry final labels."""
    return NEXT_OPEN_ENTRY_OFFSET + cfg.exits.max_hold_days


def select_grid_events(
    events: pd.DataFrame,
    sp: StatsParams,
    min_fwd_window: int | None = None,
) -> tuple[pd.DataFrame, SelectionReport]:
    """The event population the headline grid is computed over.

    Three exclusions, all counted:

    - **Null `dd_bucket`.** `cell_key()` coalesces null to `'all'`, so an
      unfiltered null does not drop out — it merges into an aggregate cell
      and moves that cell's `n` without appearing anywhere.
    - **`20-35` and `35+`.** Permanently suppressed per ADR 101. Still
      detected, stored, and visible to the model layer.
    - **Open forward windows.** An event whose forward window has not
      closed carries frozen labels. Today's live events are exactly this.
    """
    n_in = int(len(events))
    headline = set(dd_bucket_labels(sp)[:2])

    null_dd = events["dd_bucket"].isna()
    deep_dd = ~null_dd & ~events["dd_bucket"].isin(headline)
    kept = events.loc[~null_dd & ~deep_dd]

    open_window = 0
    if min_fwd_window is not None:
        window = pd.to_numeric(kept["fwd_window_days"], errors="coerce")
        closed = window >= min_fwd_window
        open_window = int((~closed).sum())
        kept = kept.loc[closed]

    report = SelectionReport(
        n_in=n_in,
        n_out=int(len(kept)),
        excluded_null_dd=int(null_dd.sum()),
        excluded_deep_dd=int(deep_dd.sum()),
        excluded_open_window=open_window,
    )
    return kept.reset_index(drop=True), report


def pooled_rho(events: pd.DataFrame, rho_era: pd.DataFrame) -> float:
    """The cell's `rho_bar`, weighted by each era's **event count**.

    ADR 102's table pools across the three train eras this way. A plain
    mean over the `rho_era` rows weights a 300-event era the same as a
    140,000-event one and lands in the wrong place by enough to move
    `n_eff`.

    A missing `rho_era` row raises. Defaulting it to zero would set the
    correction to nothing and hand back `n_eff == n`, which is precisely
    the overstatement ADR 098 exists to prevent.
    """
    lookup = dict(zip(rho_era["era"], pd.to_numeric(rho_era["rho_empirical"])))
    counts = events["era"].value_counts(dropna=False)
    missing = [era for era in counts.index if era not in lookup]
    if missing:
        raise ValueError(f"no rho_era row for era(s) {sorted(map(str, missing))}")
    weighted = sum(lookup[era] * count for era, count in counts.items())
    return float(weighted / counts.sum())


def cell_n_eff(events: pd.DataFrame, rho_era: pd.DataFrame) -> tuple[float, float, float]:
    """`(k_bar, rho_bar, n_eff)` for one cell.

    `rho_bar` is clamped at zero at the point of use, never in storage
    (`rho_bar_for_correction`), so the raw measurement stays visible in
    `rho_era` while the correction stays in the safe direction.
    """
    n = int(len(events))
    k_bar = float(pd.to_numeric(events["cofire_count"], errors="coerce").mean())
    rho_bar = rho_bar_for_correction(pooled_rho(events, rho_era))
    return k_bar, rho_bar, effective_sample_size(n, k_bar, rho_bar)


def hit_flags(events: pd.DataFrame, target: float, horizon_days: int = 5) -> pd.Series:
    """Per-event hit at `target`, on the event's own side.

    A long hits when the price rises to the target; a short hits when it
    falls to it. `path_labels` builds `touched_*` from `reach["favorable"]`
    for the same reason. Comparing a short cell against an unsigned return
    would report its edge with the sign reversed, and every number in the
    row would still look plausible.

    Null forward returns stay null rather than counting as misses
    (invariant 4): a miss is a measurement, a null is the absence of one.
    """
    sides = set(events["side"].dropna().unique())
    if len(sides) > 1:
        raise ValueError(f"hit_flags needs a single-sided cell, got side values {sorted(sides)}")
    direction = -1.0 if sides == {"short"} else 1.0

    returns = pd.to_numeric(events[f"fwd_ret_{horizon_days}d"], errors="coerce") * direction
    flags = returns >= target
    return flags.where(returns.notna())


def cell_exit_mix(events: pd.DataFrame) -> dict[str, float]:
    """Fraction of the cell's exits by reason, summing to 1.

    Describes the signal **under a specific exit policy** (ADR 100): it
    reads `events.exit_reason`, which the backtest writes from
    `ExitParams`, so moving the stop changes this while the signal is
    unchanged. Comparable across cells only within one `config_hash`.

    Absent reasons are omitted rather than zeroed, so the keys say which
    exits actually occurred.

    Ordered by descending share, then by name. `value_counts` does not
    order ties stably, and two runs over identical data produced `timeout`
    and `stop` in different orders where both landed on 0.3125. Nothing
    stored was affected — `jsonb` normalizes key order — but a frame that
    differs run to run makes the determinism check report a difference that
    is not one.
    """
    counts = events["exit_reason"].dropna().value_counts()
    if counts.empty:
        return {}
    total = counts.sum()
    ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
    return {str(reason): float(count / total) for reason, count in ordered}


def _fraction_true(series: pd.Series) -> float | None:
    values = series.dropna()
    if values.empty:
        return None
    return float(values.astype(bool).mean())


def _median_touch_day(events: pd.DataFrame, suffix: str) -> float | None:
    """Median first-touch day among events that actually touched.

    `touched_*` and `day_touched_*` are read as a pair, always. The first
    is two-valued and the second three-valued, and they disagree about
    what null means: a null `day_touched_*` on a `touched_* = False` event
    means "never reached", not "reached on an unknown day".
    """
    touched = events[f"touched_{suffix}"].fillna(False).astype(bool)
    days = pd.to_numeric(events.loc[touched, f"day_touched_{suffix}"], errors="coerce").dropna()
    if days.empty:
        return None
    return float(days.median())


def _numeric_or_none(series: pd.Series, func: str, *args) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return float(getattr(values, func)(*args))


def _baseline_for(
    events: pd.DataFrame,
    baselines: pd.DataFrame | None,
    target: float,
    column: str,
) -> tuple[float | None, int]:
    """The cell's event-weighted baseline and its null count."""
    if baselines is None or baselines.empty:
        return None, int(len(events))
    joined = attach_event_baselines(events, baselines, target)
    value, _, n_null = event_weighted_baseline(joined[column])
    return value, n_null


def compute_grid(
    events: pd.DataFrame,
    rho_era: pd.DataFrame,
    cfg: Config,
    *,
    specs: tuple[CellSpec, ...] | None = None,
    baselines: dict[int, pd.DataFrame] | None = None,
    bp: BaselineParams | None = None,
    era: str | None = None,
    run_id: str = "",
    config_hash: str = "",
    git_sha: str = "",
) -> pd.DataFrame:
    """One row per (cell, ladder target), in `cell_stats` column order.

    `era` is null for the headline rows, which are pooled across eras (ADR
    103). 12.4's descriptive era rows call this once per era with `era`
    supplied; those rows carry no `q_value` and enter no test family.

    `baselines` maps direction (`1` long, `-1` short) to a ticker-year
    baseline frame. Omitted, the baseline columns come back null and the
    cell still reports its counts and distribution — a partial row is
    visible, a fabricated baseline is not.
    """
    bp = bp or BaselineParams()
    sp = cfg.stats
    # ADR 103. Era 2024+ *is* the holdout split, so a headline cell for it
    # reports on holdout. Refused here rather than left to the caller's
    # `eras` argument: an exclusion that lives only in a call site is one
    # nothing can test and nothing catches when it moves.
    if era is not None and era == holdout_era(sp, cfg.splits):
        raise ValueError(
            f"era {era!r} is the holdout split and may not be reported as a cell (ADR 103)"
        )
    specs = specs if specs is not None else headline_grid(sp)
    horizon = bp.horizon_days
    computed_at = datetime.now(UTC)

    entry_kind = _single_value(events, "entry_kind", GRID_ENTRY_KIND)
    split_key = _single_value(events, "split_key", "train")

    rows: list[dict[str, Any]] = []
    for spec in specs:
        cell_events = events.loc[
            (events["side"] == spec.side)
            & (events["signal_type"] == spec.signal_type)
            & (events["dd_bucket"] == spec.dd_bucket)
        ]
        if era is not None:
            cell_events = cell_events.loc[cell_events["era"] == era]

        for target in sp.reach_targets:
            rows.append(
                _cell_row(
                    cell_events,
                    spec,
                    target=float(target),
                    horizon=horizon,
                    rho_era=rho_era,
                    sp=sp,
                    baselines=baselines,
                    era=era,
                    entry_kind=entry_kind,
                    split_key=split_key,
                    run_id=run_id,
                    config_hash=config_hash,
                    git_sha=git_sha,
                    computed_at=computed_at,
                )
            )

    frame = pd.DataFrame(rows, columns=CELL_STATS_COLUMNS)
    return apply_benjamini_hochberg(frame, sp)


def _single_value(events: pd.DataFrame, column: str, fallback: str) -> str:
    if column not in events.columns or events.empty:
        return fallback
    values = events[column].dropna().unique()
    if len(values) > 1:
        raise ValueError(f"expected one {column} across the grid, got {sorted(map(str, values))}")
    return str(values[0]) if len(values) else fallback


def _cell_row(
    cell_events: pd.DataFrame,
    spec: CellSpec,
    *,
    target: float,
    horizon: int,
    rho_era: pd.DataFrame,
    sp: StatsParams,
    baselines: dict[int, pd.DataFrame] | None,
    era: str | None,
    entry_kind: str,
    split_key: str,
    run_id: str,
    config_hash: str,
    git_sha: str,
    computed_at: datetime,
) -> dict[str, Any]:
    n = int(len(cell_events))
    row: dict[str, Any] = {
        "cell_id": cell_id_for(
            spec,
            entry_kind=entry_kind,
            split_key=split_key,
            horizon_days=horizon,
            target_pct=target,
            era=era,
        ),
        "run_id": run_id,
        "config_hash": config_hash,
        "signal_type": spec.signal_type,
        "dd_bucket": spec.dd_bucket,
        "signal_strength": None,  # ADR 102 removed it as a dimension
        "side": spec.side,
        "entry_kind": entry_kind,
        "split_key": split_key,
        "era": era,
        "horizon_days": horizon,
        "target_pct": target,
        "n_events": n,
        "p_value_randomization": None,  # Session 13's 200 replications
        "computed_at": computed_at,
        "git_sha": git_sha,
    }

    if n == 0:
        row.update(
            {col: None for col in CELL_STATS_COLUMNS if col not in row},
            n_eff=0.0,
            n_tickers=0,
            suppressed=True,
            suppress_reason="no events in cell",
        )
        return row

    k_bar, _, n_eff = cell_n_eff(cell_events, rho_era)
    reason = suppression_reason(n_eff, sp)

    direction = -1 if spec.side == "short" else 1
    baseline_frame = (baselines or {}).get(direction)
    baseline_empirical, _ = _baseline_for(cell_events, baseline_frame, target, "baseline_empirical")
    baseline_parametric, _ = _baseline_for(
        cell_events, baseline_frame, target, "baseline_parametric"
    )

    flags = hit_flags(cell_events, target, horizon)
    p_hit = None if flags.dropna().empty else float(flags.dropna().mean())

    row.update(
        {
            "n_eff": float(n_eff),
            "n_tickers": int(cell_events["ticker"].nunique()),
            "mean_cofire": k_bar,
            "baseline_empirical": baseline_empirical,
            "baseline_parametric": baseline_parametric,
            "mean_ret": _numeric_or_none(cell_events["net_ret"], "mean"),
            "median_ret": _numeric_or_none(cell_events["net_ret"], "median"),
            "ret_p25": _numeric_or_none(cell_events["net_ret"], "quantile", 0.25),
            "ret_p75": _numeric_or_none(cell_events["net_ret"], "quantile", 0.75),
            "mean_mfe": _numeric_or_none(cell_events["mfe"], "mean"),
            "mean_mae": _numeric_or_none(cell_events["mae"], "mean"),
            "median_time_to_mfe": _numeric_or_none(cell_events["time_to_mfe"], "median"),
            "capture_ratio": _numeric_or_none(cell_events["capture_ratio"], "mean"),
            "p_touch_2pct": _fraction_true(cell_events["touched_2pct"]),
            "p_touch_3pct": _fraction_true(cell_events["touched_3pct"]),
            "p_touch_5pct": _fraction_true(cell_events["touched_5pct"]),
            "p_touch_10pct": _fraction_true(cell_events["touched_10pct"]),
            "median_day_touch_5pct": _median_touch_day(cell_events, "5pct"),
            "exit_mix": cell_exit_mix(cell_events),
            "earnings_frac": _fraction_true(cell_events["earnings_in_window"]),
            "suppressed": reason is not None,
            "suppress_reason": reason,
        }
    )

    if reason is not None:
        # No number renders on a suppressed cell. The counts above stay,
        # because they are the explanation.
        row.update({col: None for col in RENDERED_COLUMNS})
        return row

    edge = None if (p_hit is None or baseline_empirical is None) else p_hit - baseline_empirical
    ci_low: float | None = None
    ci_high: float | None = None
    if p_hit is not None:
        # Wilson takes `n_eff`, never `n`. The parameter is named for it and
        # a signature test enforces the name, but the caller still has to
        # pass the right thing (ADR 098).
        ci_low, ci_high = wilson_ci(p_hit * n_eff, n_eff)

    row.update(
        {
            "p_hit": p_hit,
            "edge": edge,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "p_value_parametric": one_sided_p_value(p_hit, baseline_empirical, n_eff),
            "q_value": None,  # filled by the family-wide correction
        }
    )
    return row


def apply_benjamini_hochberg(rows: pd.DataFrame, sp: StatsParams) -> pd.DataFrame:
    """FDR correction over DESIGN §6.8's family, and nothing else.

    The family is every **rendered, pooled** headline cell across every
    ladder target for one config: twelve cells times four targets is 48
    tests.

    Two exclusions matter and both are correctness, not taste. Suppressed
    cells stay out because including them inflates `m` and weakens every
    q-value in the family. Era and breadth rows stay out because they are
    descriptive splits on cells already tested (ADR 103, ADR 099); folding
    them in would quadruple `m` to buy nothing.
    """
    out = rows.copy()
    if "q_value" not in out.columns:
        out["q_value"] = None
    if out.empty:
        return out

    in_family = (
        out["p_value_parametric"].notna()
        & ~out["suppressed"].fillna(False).astype(bool)
        & out["era"].isna()
    )
    if not in_family.any():
        out.loc[:, "q_value"] = np.nan
        return out

    p_values = out.loc[in_family, "p_value_parametric"].to_numpy(dtype="float64")
    _, q_values = benjamini_hochberg(p_values, alpha=sp.fdr_alpha)

    out["q_value"] = np.nan
    out.loc[in_family, "q_value"] = q_values
    return out


# ============ 12.4 descriptive reporting ============


def quarter_universe_size(events: pd.DataFrame) -> pd.Series:
    """Distinct tickers with any event, per calendar quarter.

    ADR 104's breadth denominator. ADR 099 originally specified the *trade*
    universe (~62 names per quarter) while `events.cofire_count` counts
    co-firing names across the **train** universe of up to 750. Numerator
    and denominator came from different populations, so the ratio could
    exceed 1, and events in quarters with too few `in_trade` names dropped
    out entirely — 1,531 of them.
    """
    quarters = pd.PeriodIndex(pd.to_datetime(events["signal_date"]), freq="Q")
    return events.groupby(quarters)["ticker"].nunique()


def breadth_ratio(events: pd.DataFrame) -> pd.Series:
    """Per-event breadth: `cofire_count` over that quarter's universe size.

    A genuine fraction in [0, 1]. Null `cofire_count` propagates rather
    than becoming zero, which would read as "fired alone".
    """
    quarters = pd.PeriodIndex(pd.to_datetime(events["signal_date"]), freq="Q")
    sizes = quarter_universe_size(events)
    denominator = pd.Series(quarters.map(dict(sizes)), index=events.index, dtype="float64")
    numerator = pd.to_numeric(events["cofire_count"], errors="coerce")
    return numerator / denominator


def assign_breadth_tercile(events: pd.DataFrame, rp: ReportingParams) -> pd.DataFrame:
    """Label each event `low`, `mid`, or `high` breadth, cut **within era**.

    A pooled cut would load one era into one bucket and end up measuring
    the era rather than the breadth: 2010-2014 fires at `k_bar` near 64
    against 12 to 38 elsewhere.
    """
    out = events.copy()
    out["breadth"] = breadth_ratio(out)
    out["breadth_tercile"] = pd.Series(pd.NA, index=out.index, dtype="object")
    for _, group in out.groupby("era", dropna=False):
        values = group["breadth"]
        if values.notna().sum() < rp.breadth_quantiles:
            continue
        # `duplicates="drop"` rather than a rank-based cut: ties are real
        # here (a quarter's events share a denominator), and splitting
        # identical breadth values across buckets to force equal sizes
        # would put two indistinguishable events in different terciles.
        # An era degenerate enough to collapse bins gets fewer buckets,
        # named for what they are, rather than a silent relabelling.
        binned = pd.qcut(values, rp.breadth_quantiles, duplicates="drop")
        out.loc[group.index, "breadth_tercile"] = binned.cat.rename_categories(
            _tercile_labels(len(binned.cat.categories))
        ).astype(object)
    return out


def _tercile_labels(n_bins: int) -> list[str]:
    if n_bins == 3:
        return ["low", "mid", "high"]
    return [f"q{i + 1}" for i in range(n_bins)]


def ticker_concentration(events: pd.DataFrame) -> tuple[str | None, float | None]:
    """The cell's largest single-ticker contributor and its share.

    A cell where one ticker supplies 40% of events is a statement about
    that ticker (DESIGN §6.7). Above `ReportingParams.max_ticker_share`
    the cell is reported with and without it.
    """
    counts = events["ticker"].dropna().value_counts()
    if counts.empty:
        return None, None
    return str(counts.index[0]), float(counts.iloc[0] / counts.sum())


# ============ database IO ============


def load_grid_events(
    engine: Engine,
    config_hash: str,
    split_key: str = "train",
    *,
    entry_kind: str = GRID_ENTRY_KIND,
    cluster_heads_only: bool = GRID_CLUSTER_HEADS_ONLY,
) -> pd.DataFrame:
    """Events for one config and split, in ADR 102's measured population.

    Reads labels from `events`, never from `path` — `path.terminal` is a
    different quantity by design (the label source contract in `BUILD.md`).
    """
    sql = [
        "SELECT ticker, signal_date, signal_type, side, dd_bucket, era, split_key,",
        "       entry_kind, is_cluster_head, fwd_window_days, cofire_count,",
        "       fwd_ret_1d, fwd_ret_2d, fwd_ret_3d, fwd_ret_5d, fwd_ret_10d,",
        "       net_ret, gross_ret, mfe, mae, time_to_mfe, capture_ratio, exit_reason,",
        "       earnings_in_window, touched_2pct, day_touched_2pct, touched_3pct,",
        "       day_touched_3pct, touched_5pct, day_touched_5pct, touched_10pct,",
        "       day_touched_10pct",
        "FROM events WHERE config_hash = :config_hash AND split_key = :split_key "
        # ADR 122. Detection stopped filtering on trade-universe
        # membership and started recording it, so the cell population is
        # only unchanged while this predicate is here. Without it every
        # measured cell silently widens by roughly 4.4x on the next
        # `cscan cell-stats`, and nothing about the output would look
        # wrong.
        "AND in_trade",
        "AND entry_kind = :entry_kind",
    ]
    if cluster_heads_only:
        sql.append("AND is_cluster_head")
    with engine.connect() as conn:
        frame = pd.read_sql(
            text(" ".join(sql)),
            conn,
            params={
                "config_hash": config_hash,
                "split_key": split_key,
                "entry_kind": entry_kind,
            },
        )
    return frame


def load_rho_era(engine: Engine, config_hash: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text("SELECT era, rho_empirical FROM rho_era WHERE config_hash = :config_hash"),
            conn,
            params={"config_hash": config_hash},
        )


def write_cell_stats(engine: Engine, rows: pd.DataFrame) -> int:
    """Upsert on `(cell_id, config_hash)` — ADR 096's composite key.

    A second `config_hash` adds rows rather than replacing the first's,
    which is what the composite key was for and what makes eighteen swept
    configs comparable without re-running Phase 4 per config.

    `arm` is not written here. The column arrives in 12.5 with
    `DEFAULT 'signal'`, which is the correct value for every row this
    module produces (ADR 105); writing it before the migration would fail,
    and writing it conditionally would hide a missing migration.
    """
    if rows.empty:
        return 0

    payload = rows.copy()
    payload["exit_mix"] = payload["exit_mix"].map(
        lambda value: json.dumps(value) if isinstance(value, dict) else None
    )
    payload = payload.replace({np.nan: None})

    columns = [c for c in CELL_STATS_COLUMNS if c in payload.columns]
    placeholders = ", ".join(f":{c}" for c in columns)
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in columns if c not in ("cell_id", "config_hash")
    )
    statement = text(
        f"INSERT INTO cell_stats ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT (cell_id, config_hash) DO UPDATE SET {updates}"
    )

    records = payload[columns].to_dict(orient="records")
    with engine.begin() as conn:
        conn.execute(
            statement,
            [{str(k): _as_param(v) for k, v in record.items()} for record in records],
        )
    return len(records)


@dataclass(frozen=True)
class CellStatsRunReport:
    """What one `run_cell_stats` call measured and wrote."""

    config_hash: str
    split_key: str
    run_id: str
    selection: SelectionReport
    n_rows_pooled: int
    n_rows_era: int
    n_written: int
    suppressed_cells: tuple[str, ...]

    def summary(self) -> str:
        return (
            f"{self.selection.summary()}\n"
            f"pooled rows: {self.n_rows_pooled}, era rows: {self.n_rows_era}, "
            f"written: {self.n_written}\n"
            f"suppressed: {', '.join(self.suppressed_cells) or 'none'}"
        )


def build_baselines(
    engine: Engine,
    events: pd.DataFrame,
    sp: StatsParams,
    bp: BaselineParams,
) -> dict[int, pd.DataFrame]:
    """Ticker-year baselines for both directions over the event universe.

    Both are needed: six of the twelve headline cells are short, and a
    short's baseline is `P(price falls target)` rather than
    `P(price rises target)` (ADR 106, Session 12.3).

    The bar window is widened past the event years on both sides because
    the parametric baseline needs a complete strictly-prior trailing window
    and the empirical one needs a complete forward window. A window cut to
    the event years exactly would null the first and last year of every
    ticker.
    """
    from capitalscan.research.baselines import load_adj_close_panel, ticker_year_baselines

    tickers = sorted(events["ticker"].dropna().unique().tolist())
    years = pd.to_datetime(events["signal_date"]).dt.year
    start = f"{int(years.min()) - 2}-01-01"
    end = f"{int(years.max()) + 1}-12-31"

    panel = load_adj_close_panel(engine, tickers=tickers, start=start, end=end)
    return {
        1: ticker_year_baselines(panel, sp.reach_targets, bp, direction=1),
        -1: ticker_year_baselines(panel, sp.reach_targets, bp, direction=-1),
    }


def run_cell_stats(
    engine: Engine,
    config_hash: str,
    split_key: str = "train",
    *,
    cfg: Config | None = None,
    bp: BaselineParams | None = None,
    run_id: str = "",
    git_sha: str = "",
    eras: tuple[str, ...] | None = None,
    write: bool = True,
) -> tuple[pd.DataFrame, CellStatsRunReport]:
    """Measure the headline grid for one config and write it.

    Returns `(rows, report)`. The pooled rows carry q-values over the
    48-test family; the era rows (12.4) are descriptive and carry none.
    """
    cfg = cfg or Config()
    bp = bp or BaselineParams()

    events = load_grid_events(engine, config_hash, split_key)
    selected, selection = select_grid_events(
        events, cfg.stats, min_fwd_window=min_fwd_window_for(cfg)
    )
    rho_era = load_rho_era(engine, config_hash)
    baselines = build_baselines(engine, selected, cfg.stats, bp)

    pooled = compute_grid(
        selected,
        rho_era,
        cfg,
        baselines=baselines,
        bp=bp,
        run_id=run_id,
        config_hash=config_hash,
        git_sha=git_sha,
    )

    # Default to every era except the holdout one (ADR 103), derived rather
    # than listed at the call site. `compute_grid` refuses the holdout era
    # regardless, so an explicit caller cannot route around this.
    eras = reported_eras(cfg.stats, cfg.splits) if eras is None else eras

    era_frames = []
    for era in eras:
        era_frames.append(
            compute_grid(
                selected,
                rho_era,
                cfg,
                baselines=baselines,
                bp=bp,
                era=era,
                run_id=run_id,
                config_hash=config_hash,
                git_sha=git_sha,
            )
        )

    rows = pd.concat([pooled, *era_frames], ignore_index=True) if era_frames else pooled
    n_written = write_cell_stats(engine, rows) if write else 0

    suppressed = tuple(
        sorted(rows.loc[rows["suppressed"].fillna(False).astype(bool), "cell_id"].unique())
    )
    report = CellStatsRunReport(
        config_hash=config_hash,
        split_key=split_key,
        run_id=run_id,
        selection=selection,
        n_rows_pooled=int(len(pooled)),
        n_rows_era=int(sum(len(f) for f in era_frames)),
        n_written=n_written,
        suppressed_cells=suppressed,
    )
    return rows, report


def _as_param(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is pd.NaT:
        return None
    return value
