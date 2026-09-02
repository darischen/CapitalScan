"""The model's training matrix (DESIGN §7.3, ADR 113).

One frame: the features of DESIGN §7.3 that are genuinely on the event
row, the four ADR 113 labels, and the columns a fold splitter and a sample
weighter need. Nothing is fitted here.

**Every feature comes from the event row and nowhere else.** That is the
whole look-ahead argument, and it is structural rather than checked: the
event row is written at signal time from indicators read at t-1 (invariant
3), so a column on it cannot carry information from after the signal. The
moment a feature is sourced by joining another table at prediction time,
that argument stops holding and has to be replaced by a test nobody can
write cheaply.

`FORBIDDEN_COLS` is the other half. `events` carries the *outcome* of each
signal in the same row as its state -- `entry_price`, `gross_ret`, `mfe`,
`touched_5pct`, `exit_reason`. Any of those as a feature is a perfect
leak that would train a model to predict a number it was handed. They are
enumerated rather than filtered by prefix, because `bb_pctb` and
`bb_width_pct` share no prefix with anything and a prefix rule would grow
to admit one of them by accident.

**Three of DESIGN §7.3's twenty-two are not built, and §7.3 is wrong to
say they are.** Measured 2026-08-25 against `information_schema`:

| Feature | Why not |
|---|---|
| distance to mid in ATR units | needs `bb_mid`; absent from `events` |
| `atr_14 / close` | `atr_14` is present, `close` is not |
| `vix_pct_252d` | absent from `events` |

Each is available in `indicators` at t-1, so the fix is a join or three new
event columns. Both are decisions rather than lookups: a join at frame-build
time reintroduces exactly the sourcing question the paragraph above closes,
and `entry_price` cannot serve as the `close` denominator because it is
priced at *t*. Recorded in `BACKLOG.md`; the nineteen below are clean.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.training import partition_for_training

# ---------------------------------------------------------------------------
# The feature set
# ---------------------------------------------------------------------------

#: Read straight off the event row.
RAW_FEATURE_COLS: tuple[str, ...] = (
    # Band state
    "bb_pctb",
    "bb_width_pct",
    # Momentum
    "k_full",
    "d_full",
    "k_fast",
    "k_cross_up",
    "k_cross_down",
    # Volatility
    "rv_pct_252d",
    "vix_close",
    # Trend
    "above_sma200",
    "sma200_slope_60",
    "dd_52w",
    # Flow
    "vol_z_20d",
    "spx_ret_1d",
    "cofire_count",
    # Event
    "signal_strength",
    "seq_in_cluster",
    "days_to_earnings",
    # Static
    "sector",
)

#: Computed from raw columns, never from anything off-row.
DERIVED_FEATURE_COLS: tuple[str, ...] = ("k_minus_d", "mcap_log")

FEATURE_COLS: tuple[str, ...] = RAW_FEATURE_COLS + DERIVED_FEATURE_COLS

#: `sector` is the only categorical. DESIGN §7.3 excludes `ticker` identity
#: deliberately: 60 names over 40k events permits memorising individual
#: histories, and sector is the right granularity.
CATEGORICAL_COLS: tuple[str, ...] = ("sector",)

#: ADR 113's four labels. `fwd_ret_{h}d` is R_h, `peak_ret_{h}d` is M_h.
LABEL_COLS: tuple[str, ...] = (
    "fwd_ret_5d",
    "fwd_ret_10d",
    "peak_ret_5d",
    "peak_ret_10d",
)

#: Carried for folds, weights and provenance. Never features.
META_COLS: tuple[str, ...] = (
    "id",
    "ticker",
    "signal_date",
    "split_key",
    "cluster_id",
    "is_cluster_head",
)

#: Outcome columns. A feature set that touches one of these is not a model.
#:
#: Enumerated, not prefix-matched. `era` is here for a different reason
#: than the rest: DESIGN §7.3 excludes it because it invites memorising
#: regime and is unavailable for a future prediction anyway.
FORBIDDEN_COLS: frozenset[str] = frozenset(
    {
        "entry_price",
        "entry_date",
        "entry_gapped",
        "exit_price",
        "exit_date",
        "exit_reason",
        "holding_days",
        "gross_ret",
        "net_ret",
        "mfe",
        "mae",
        "time_to_mfe",
        "capture_ratio",
        "giveback",
        "is_terminal",
        "ambiguous",
        "earnings_in_window",
        "fwd_window_days",
        "era",
        "touched_2pct",
        "touched_3pct",
        "touched_5pct",
        "touched_10pct",
        "day_touched_2pct",
        "day_touched_3pct",
        "day_touched_5pct",
        "day_touched_10pct",
        "fwd_ret_1d",
        "fwd_ret_2d",
        "fwd_ret_3d",
        "fwd_ret_5d",
        "fwd_ret_10d",
        "peak_ret_1d",
        "peak_ret_2d",
        "peak_ret_3d",
        "peak_ret_5d",
        "peak_ret_10d",
    }
)


@dataclass(frozen=True)
class FrameReport:
    """What the build kept and what it refused.

    `missing_sector` is a count the caller must act on rather than log.
    ADR 147 makes a blank sector stop the build: `sector` is a categorical
    feature, and a NULL level is not a level -- it silently pools every
    unresolved name into one bucket the model then learns.
    """

    rows: int
    dropped_etf: int
    dropped_missing_sector: int
    dropped_no_label: int


# **`sector` is the one column not read from the event row**, and the
# exception is deliberate rather than convenient.
#
# `events.sector` exists and is **NULL on all 227,543 rows** (measured
# 2026-08-25): the denormalised copy was never populated, and ADR 148's
# backfill repaired `tickers.sector`, which is where the 11 GICS levels
# actually live.
#
# Sourcing it from `tickers` is consistent with DESIGN §7.3's own
# classification of `sector` as **Static** -- instrument metadata rather
# than market state, so the t-1 question does not arise the way it does
# for `bb_pctb`.
#
# **The caveat, stated because it is real.** `tickers.sector` is a
# *current* snapshot with no history, so a company reclassified by GICS in
# 2018 carries its post-2018 sector on its 2010 events. That is a mild
# look-ahead of the kind ADR 135 names ("a universe evaluation must rest on
# data from inside the period it describes"). It is accepted here and
# recorded in BACKLOG rather than silently inherited: reclassifications are
# rare, the alternative is dropping the only categorical the design asks
# for, and a point-in-time sector history is a data source this project
# does not have.
# **`mcap_usd` is the second off-row column, and it is point-in-time.**
#
# `events.mcap_usd` exists and is NULL on all 227,543 rows -- the same
# unpopulated-denormalisation defect as `sector`. `universe.mcap_usd`
# carries 47,181 values with quarterly history from 2010-03-31, so the
# lateral below takes the most recent evaluation *on or before* the signal.
#
# Unlike `sector`, this needs no caveat: ADR 014's filter is causal and
# ADR 135 requires an evaluation to rest on data from inside the period it
# describes, so the value read here is one that existed when the signal
# fired. It is the same reading `core.universe.in_trade` uses and the same
# lateral `v_watchlist` uses, rather than a third interpretation of "which
# universe row applies to this event".
_SQL = """
SELECT {cols}
  FROM events e
  JOIN tickers t ON t.ticker = e.ticker
  LEFT JOIN LATERAL (
      SELECT u.mcap_usd
        FROM universe u
       WHERE u.ticker = e.ticker AND u.as_of <= e.signal_date
         AND u.config_hash = :chash
       ORDER BY u.as_of DESC
       LIMIT 1
  ) u ON TRUE
 WHERE e.config_hash = :chash
   AND e.entry_kind = 'next_open'
   AND e.in_trade
   AND e.split_key = :split
"""


def _select_columns() -> tuple[str, ...]:
    """Qualified column list. `mcap_usd` backs `mcap_log`; `d_full` is both
    a feature and the second half of `k_minus_d`.

    Every name is prefixed, because `sector` and `mcap_usd` each exist on
    **two** of the joined tables and an unqualified reference resolves to
    the `events` copy, which is NULL everywhere. Reading a column that
    exists, is spelled correctly, and is empty is exactly how both of these
    were nearly shipped as all-NULL features.
    """
    names = dict.fromkeys(META_COLS + RAW_FEATURE_COLS + LABEL_COLS + ("mcap_usd",))
    source = {"sector": "t", "mcap_usd": "u"}
    return tuple(f"{source[n]}.{n} AS {n}" if n in source else f"e.{n}" for n in names)


def build_training_frame(
    engine: Engine,
    config_hash: str,
    split: str = "train",
    require_labels: bool = True,
) -> tuple[pd.DataFrame, FrameReport]:
    """Assemble the model's input for one split.

    **`split` is explicit and has no default of `holdout`.** ADR 019 assigns
    splits at event creation, and the holdout is evaluated exactly once at
    the end. A frame builder that could reach it by omission is one typo
    from spending that.

    Raises when any surviving row has a blank or non-canonical sector
    (ADR 147/148). That is a data defect repaired at the source, and
    training through it means training on a categorical whose NULL level
    pools every unresolved name.
    """
    if split == "holdout":
        raise ValueError(
            "refusing to build a training frame on the holdout split. It is "
            "evaluated exactly once, at the end, and published whatever it "
            "says (ADR 019). Pass split='train' or 'validate'."
        )

    cols = _select_columns()
    with engine.connect() as conn:
        frame = pd.read_sql(
            text(_SQL.format(cols=", ".join(cols))),
            conn,
            params={"chash": config_hash, "split": split},
        )

    trainable, etf, missing = partition_for_training(
        list(zip(frame["ticker"], frame["sector"], strict=True))
    )
    if missing:
        sample = sorted({frame["ticker"].iloc[i] for i in missing})[:10]
        raise ValueError(
            f"{len(missing)} event(s) carry a blank or non-canonical sector, "
            f"e.g. {sample}. ADR 147 stops the build rather than training on "
            "a categorical with a NULL level. Repair with "
            "`cscan sector-backfill` (ADR 148)."
        )

    kept = frame.iloc[trainable].reset_index(drop=True)

    before = len(kept)
    if require_labels:
        kept = kept.dropna(subset=list(LABEL_COLS)).reset_index(drop=True)
    dropped_no_label = before - len(kept)

    kept = _coerce_boolean_features(kept)
    kept = _add_derived(kept)
    report = FrameReport(
        rows=len(kept),
        dropped_etf=len(etf),
        dropped_missing_sector=len(missing),
        dropped_no_label=dropped_no_label,
    )
    return kept, report


#: Feature columns that are `boolean` in Postgres. Named rather than
#: sniffed, so a new boolean feature has to be added here deliberately.
BOOL_FEATURE_COLS: tuple[str, ...] = ("above_sma200", "k_cross_up", "k_cross_down")


def _coerce_boolean_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Restore `bool` dtype to boolean columns after the row drops.

    **This is a dtype-survival bug, and it broke every fit.** `read_sql`
    returns `object` for a boolean column containing any NULL. The drops
    above then remove the offending rows -- but pandas does not
    re-infer, so the column stays `object` with only `True`/`False` in it.
    LightGBM refuses:

        ValueError: pandas dtypes must be int, float or bool.
        Fields with bad pandas dtypes: above_sma200: object

    Measured 2026-09-02 on the live config: `train` came back `object` and
    `validate` came back `bool`, from the same query, purely because one
    split happened to contain a dropped row with a NULL. So it failed on
    one split and not the other, and `research/train.py::fit_head` failed
    with it -- the whole Phase 6 training path.

    **Raises rather than coercing a surviving NULL.** After the drops there
    should be none; if there is, `astype(bool)` would silently turn it into
    `True`, which is invariant 4's exact prohibition wearing a cast.
    """
    out = frame.copy()
    for col in BOOL_FEATURE_COLS:
        if col not in out.columns:
            continue
        nulls = int(out[col].isna().sum())
        if nulls:
            raise ValueError(
                f"{col} has {nulls} NULL value(s) after the training-frame drops. "
                "Casting them to bool would invent True; drop the rows or repair "
                "the source (invariant 4)."
            )
        out[col] = out[col].astype(bool)
    return out


def _add_derived(frame: pd.DataFrame) -> pd.DataFrame:
    """Never mutate in place (project convention).

    `k_minus_d` is the stochastic spread, which a tree can only express as a
    difference by splitting twice; giving it directly is the standard
    treatment and costs nothing.

    `mcap_log` rather than `mcap_usd`: market cap spans four orders of
    magnitude, and while a tree is scale-invariant, the log makes a split
    point mean the same thing across the range. NULL stays NULL -- an ETF
    with no share count has no market cap, and invariant 4 says absent
    stays absent.
    """
    out = frame.copy()
    out["k_minus_d"] = pd.to_numeric(out["k_full"], errors="coerce") - pd.to_numeric(
        out["d_full"], errors="coerce"
    )
    mcap = pd.to_numeric(out["mcap_usd"], errors="coerce")
    # `where(mcap > 0)` before the log: a non-positive market cap is not a
    # small one, it is an absent or corrupted one, and `log(0)` is `-inf`,
    # which a tree happily splits on as though it meant something.
    out["mcap_log"] = np.log(mcap.where(mcap > 0))
    return out
