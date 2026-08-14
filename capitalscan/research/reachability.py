"""Session 14.4: DESIGN §6.12's volatility-scaled reachability ladder.

Computed at **report time**, from `events` joined to `indicators.rv_20d` —
never stored, no migration (session doc §2, "Volatility-scaled
reachability is computed at report time, not stored").

**`events` does not carry an `rv_20d` column** (verified against
`db/schema.sql` and the live database on `config_hash = 697f3ae71428d392`:
only `rv_pct_252d`, the rolling percentile, made it onto the row).
`indicators.rv_20d` is the raw annualized value, so
`load_events_for_reachability` joins each event to the same t-1 indicator
row `research.backtest._prior_indicator` reads at event creation
(invariant 3) — the latest `indicators` row strictly before `signal_date`,
same ticker, `interval = '1d'`. This is the identical lookup `bb_pctb`,
`atr_14`, and every other indicator-sourced column on `events` already
went through; it is not a second indicator computation (invariant 2).

**Fixed ladder** targets (2/3/5/10%) are read straight off the stored
`events.touched_{2,3,5,10}pct` booleans — already computed by
`research/enrich.py::path_metrics` over the DESIGN §7.1 reachability
window (`entry_offset+1 .. entry_offset+max_hold_days`) — never
recomputed here.

**Scaled ladder** targets (0.5sigma/1.0sigma/1.5sigma of sigma_5d) need a
*continuous* value to compare against, which the four fixed booleans
cannot supply. `events.peak_ret_{h}d` (ADR 093/094 — "max forward return
within h days of entry, uncapped by exit") is that value: the same
reachability question the fixed ladder answers, at a continuous level
instead of four discrete ones. It is read at
`h = BaselineParams.horizon_days` (default 5), matching the horizon
sigma_5d is itself scaled to — never hardcoded.

**Diagnostic only, never in the FDR family.** This module returns
fractions, and nothing here computes or imports a `p_value`, a `q_value`,
or the Benjamini-Hochberg family correction itself. DESIGN §6.12 and the
session plan (`docs/sessions/session14-phase4-close.md` §2) are explicit:
fixed is the headline that carries that correction (computed and stored
elsewhere, by `research.cell_stats`); scaled reveals whether an apparent
edge is a volatility effect, and this module is the only place it is
reported.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.baselines import sigma_5d as _sigma_5d
from capitalscan.core.cells import headline_grid
from capitalscan.core.config import BaselineParams, StatsParams

# `events.peak_ret_*d` is fixed by the schema at these horizons (mirrors
# `StatsParams.fwd_ret_horizons`'s own fixed set) — `BaselineParams.
# horizon_days` must land on one of them or there is no column to compare
# sigma_5d's reachability question against.
_PEAK_RET_HORIZONS: tuple[int, ...] = (1, 2, 3, 5, 10)

# `StatsParams.reach_targets` -> the stored boolean column that already
# answers "did this event reach that fixed target within the reachability
# window." Keyed by value rather than position so a config that reorders
# `reach_targets` still resolves to the right column.
_FIXED_TARGET_COLUMNS: dict[float, str] = {
    0.02: "touched_2pct",
    0.03: "touched_3pct",
    0.05: "touched_5pct",
    0.10: "touched_10pct",
}

SCALED_LEVELS: tuple[float, ...] = (0.5, 1.0, 1.5)

_GROUP_COLS: tuple[str, ...] = ("signal_type", "side", "dd_bucket")


def peak_ret_column(bp: BaselineParams) -> str:
    """The `events.peak_ret_{h}d` column matching `bp.horizon_days`.

    Raises rather than guessing when the horizon has no matching column —
    a silent fallback to the wrong horizon would compare sigma_5d against
    a reachability window it was never scaled to.
    """
    if bp.horizon_days not in _PEAK_RET_HORIZONS:
        raise ValueError(
            f"BaselineParams.horizon_days={bp.horizon_days} has no matching "
            f"events.peak_ret_*d column (the schema fixes these at "
            f"{_PEAK_RET_HORIZONS}); sigma_5d cannot be compared against a "
            "reachability value at this horizon without a schema change."
        )
    return f"peak_ret_{bp.horizon_days}d"


def _level_suffix(level: float) -> str:
    # 0.5 -> "0.5", 1.0 -> "1.0" - DESIGN §6.12's own spelling ("0.5sigma",
    # "1.0sigma", "1.5sigma"), a different convention from `touched_*pct`'s
    # percent-string suffix (a different quantity, kept visually distinct).
    return f"{level:.1f}"


def attach_scaled_targets(
    events: pd.DataFrame,
    bp: BaselineParams,
    levels: tuple[float, ...] = SCALED_LEVELS,
) -> pd.DataFrame:
    """Attach `sigma_5d`, the `level * sigma_5d` targets, and whether the
    event's own `peak_ret_{h}d` reached each one.

    `events` must carry `rv_20d` (joined in by
    `load_events_for_reachability`, never computed here) and
    `peak_ret_{bp.horizon_days}d`. A null `rv_20d` propagates to a null
    `sigma_5d`, null targets, and a null (`pd.NA`, never `False`) reached
    flag — invariant 4: dropped from any downstream fraction, not
    substituted.
    """
    if "rv_20d" not in events.columns:
        raise ValueError("events is missing required column: 'rv_20d'")
    peak_col = peak_ret_column(bp)
    if peak_col not in events.columns:
        raise ValueError(f"events is missing required column: {peak_col!r}")

    out = events.copy()
    rv = pd.to_numeric(out["rv_20d"], errors="coerce").to_numpy(dtype="float64")
    sigma = _sigma_5d(rv, bp)
    out["sigma_5d"] = sigma
    peak = pd.to_numeric(out[peak_col], errors="coerce").to_numpy(dtype="float64")
    null_mask = np.isnan(sigma) | np.isnan(peak)

    for level in levels:
        target = sigma * level
        suffix = _level_suffix(level)
        out[f"target_{suffix}sigma"] = target
        with np.errstate(invalid="ignore"):
            reached = peak >= target
        reached_nullable = pd.array(reached, dtype="boolean")
        reached_nullable[null_mask] = pd.NA
        out[f"reached_{suffix}sigma"] = reached_nullable
    return out


def scaled_reachability_by_cell(
    events_with_targets: pd.DataFrame,
    levels: tuple[float, ...] = SCALED_LEVELS,
) -> pd.DataFrame:
    """One row per `(signal_type, side, dd_bucket, level)`: the fraction of
    events whose own `peak_ret_{h}d` reached `level * sigma_5d`.

    Nulls (a missing `rv_20d`, most often) are dropped from the fraction,
    never counted as a miss — the same null policy `empirical_baseline`
    and `event_weighted_baseline` use elsewhere in `core/baselines.py`.
    """
    group_cols = list(_GROUP_COLS)
    rows: list[dict] = []
    for key, group in events_with_targets.groupby(group_cols, sort=True, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        for level in levels:
            suffix = _level_suffix(level)
            col = group[f"reached_{suffix}sigma"]
            observed = col.dropna()
            n_obs = int(len(observed))
            frac = float(observed.mean()) if n_obs else None
            row = dict(zip(group_cols, key_tuple))
            row.update({"level_sigma": level, "n_obs": n_obs, "fraction_reached": frac})
            rows.append(row)
    columns = group_cols + ["level_sigma", "n_obs", "fraction_reached"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def fixed_reachability_by_cell(events: pd.DataFrame, sp: StatsParams) -> pd.DataFrame:
    """Same shape as `scaled_reachability_by_cell`, over the stored fixed
    ladder (`events.touched_*pct`) — reused, never recomputed."""
    group_cols = list(_GROUP_COLS)
    rows: list[dict] = []
    for key, group in events.groupby(group_cols, sort=True, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        for target in sp.reach_targets:
            col_name = _FIXED_TARGET_COLUMNS.get(float(target))
            if col_name is None or col_name not in group.columns:
                continue
            observed = group[col_name].dropna()
            n_obs = int(len(observed))
            frac = float(observed.astype("boolean").mean()) if n_obs else None
            row = dict(zip(group_cols, key_tuple))
            row.update({"target_pct": float(target), "n_obs": n_obs, "fraction_reached": frac})
            rows.append(row)
    columns = group_cols + ["target_pct", "n_obs", "fraction_reached"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def combined_reachability_table(
    events_with_targets: pd.DataFrame,
    sp: StatsParams,
) -> pd.DataFrame:
    """The fixed and scaled ladders side by side, one `ladder` column
    distinguishing them, both scoped to the fourteen headline cells
    (ADR 108's `core.cells.headline_grid`).

    No `p_value`/`p_value_parametric`/`q_value` column anywhere in this
    frame. The fixed ladder's own q-values live in `research.cell_stats`'s
    stored rows, computed and corrected there; this table never touches
    the Benjamini-Hochberg family and reports fractions only.
    """
    headline = headline_grid(sp)
    keep = {(spec.signal_type, spec.side, spec.dd_bucket) for spec in headline}
    key_cols = events_with_targets[list(_GROUP_COLS)].apply(tuple, axis=1)
    scoped = events_with_targets.loc[key_cols.isin(keep)]

    fixed = fixed_reachability_by_cell(scoped, sp)
    fixed.insert(0, "ladder", "fixed")
    fixed = fixed.rename(columns={"target_pct": "target"})
    fixed["target"] = fixed["target"].map(lambda t: f"{t * 100:g}pct")

    scaled = scaled_reachability_by_cell(scoped)
    scaled.insert(0, "ladder", "scaled")
    scaled = scaled.rename(columns={"level_sigma": "target"})
    scaled["target"] = scaled["target"].map(lambda level: f"{level:.1f}sigma")

    return pd.concat([fixed, scaled], ignore_index=True, sort=False)


def load_events_for_reachability(
    engine: Engine,
    config_hash: str,
    split_key: str,
    bp: BaselineParams,
) -> pd.DataFrame:
    """Report-time read: events joined to their own t-1 `indicators.rv_20d`.

    The join mirrors `research.backtest._prior_indicator` exactly — the
    latest `indicators` row strictly before `signal_date`, same ticker,
    daily interval — expressed as a `LATERAL` join rather than re-deriving
    the date arithmetic in pandas (that would be a second implementation
    of the t-1 lookup, invariant 2).

    `split_key` is a caller-supplied filter on `events.split_key`, not a
    join against any statistics table keyed by it — invariant 5b concerns
    joining *stored statistics* to an event's own `split_key`, which this
    query does not do.
    """
    peak_col = peak_ret_column(bp)
    sql = text(
        f"""
        SELECT e.id AS event_id, e.ticker, e.signal_date, e.signal_type,
               e.side, e.dd_bucket, e.split_key,
               e.touched_2pct, e.touched_3pct, e.touched_5pct, e.touched_10pct,
               e.{peak_col},
               i.rv_20d
        FROM events e
        LEFT JOIN LATERAL (
            SELECT ind.rv_20d
            FROM indicators ind
            WHERE ind.ticker = e.ticker
              AND ind.interval = '1d'
              AND ind.ts < e.signal_date
            ORDER BY ind.ts DESC
            LIMIT 1
        ) i ON true
        WHERE e.config_hash = :config_hash AND e.split_key = :split_key
        """  # noqa: S608 - peak_col is validated against a fixed whitelist above
    )
    with engine.connect() as conn:
        frame = pd.read_sql(sql, conn, params={"config_hash": config_hash, "split_key": split_key})
    return frame
