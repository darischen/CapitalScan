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
from datetime import date
from typing import Any, Sequence

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
    # **Which signal fired, and therefore which way it points (ADR 173).**
    # A `confluence_low` is a long and a `confluence_high` is a short; the
    # strategy assigns sides for that reason. Until 2026-09-04 neither this
    # nor `side` was a feature, so the directional heads were asked to
    # predict the median *price* move of a pool that is 38% longs and 62%
    # shorts without being told which -- a question with no answer, on
    # labels that are not position-relative.
    #
    # `signal_type` rather than `side`: it carries the side and also keeps
    # `confluence_low` distinct from `stoch_oversold`, which the premise
    # says are different signals. `side` would collapse that.
    "signal_type",
    # Static
    "sector",
)

#: Computed from raw columns, never from anything off-row.
#: `breach_depth` was here until 2026-09-08 and is deleted, not disabled
#: (ADR 177). It read the signal day's LOW, which a `next_open` entry
#: knows and a `touch` entry does not -- look-ahead the moment the entry
#: convention changed, and invariant 3 is the highest-risk silent failure
#: in this system. It was also worth 0.0003 AUC where it was legal, so
#: nothing is lost. Removed entirely so it cannot be re-added by someone
#: reading a disabled constant as an invitation.
DERIVED_FEATURE_COLS: tuple[str, ...] = ("k_minus_d", "mcap_log")

FEATURE_COLS: tuple[str, ...] = RAW_FEATURE_COLS + DERIVED_FEATURE_COLS

#: `sector` and `signal_type` are the categoricals. DESIGN §7.3 excludes
#: `ticker` identity deliberately: 60 names over 40k events permits
#: memorising individual histories, and sector is the right granularity.
#: `signal_type` joined them on 2026-09-04 (ADR 173) -- seven levels, and
#: the one feature that says which way the signal points.
CATEGORICAL_COLS: tuple[str, ...] = ("sector", "signal_type")

#: ADR 113's four labels. `fwd_ret_{h}d` is R_h, `peak_ret_{h}d` is M_h.
#: Which fill convention the model is fitted on and serves (ADR 177).
#:
#: **`touch` -- same-bar entry at the signal price -- not `next_open`.**
#: With population held constant, `p_touch_3` Brier skill goes +6.91% to
#: +11.92% and `p_adverse_3` +3.65% to +11.69%, every field improving with
#: calibration unchanged. A `next_open` label measures a forward window from
#: a price the features never saw: the overnight gap carries news and index
#: moves nothing in the feature vector predicts, and that is noise in the
#: *label*, which caps discrimination however good the features are.
#:
#: **A module constant, not a `Config` field.** It selects which existing
#: rows are read and changes no row's meaning, so it must not move
#: `config_hash` -- ADR 176 made that mistake once and orphaned every row
#: keyed on `0523841076f47293` until a test caught it.
#:
#: **The cost: stochastic-only signals cannot be scored.** A touch entry
#: needs a band level to fill at, and `stoch_overbought`/`stoch_oversold`
#: carry no `entry_price` in any split. Confluence loses nothing --
#: measured, **zero** of 68,869 stochastic rows share a ticker-date with a
#: confluence row, because DESIGN 4.7's debounce already collapses the
#: coincident rows into the confluence slot.
TRAINING_ENTRY_KIND: str = "touch"

LABEL_COLS: tuple[str, ...] = (
    "fwd_ret_5d",
    "fwd_ret_10d",
    "peak_ret_5d",
    "peak_ret_10d",
    # ADR 175. Same completeness gate as the peak family, so these drop
    # the same rows and the training population is unchanged.
    "trough_ret_5d",
    "trough_ret_10d",
)

#: Carried for folds, weights and provenance. Never features.
META_COLS: tuple[str, ...] = (
    "id",
    "ticker",
    "signal_date",
    "split_key",
    "cluster_id",
    "is_cluster_head",
    # `side` decides the sign of `breach_depth`: a long breaches downward
    # through the lower band, a short upward through the upper. Carried, not
    # a feature -- `signal_type` already encodes direction and a second copy
    # would let the model split on the same fact twice.
    "side",
    # Raw inputs to `breach_depth`, dropped from the matrix once derived.
    "bar_low",
    "bar_high",
    "band_lower",
    "band_upper",
)

#: Outcome columns. A feature set that touches one of these is not a model.
#:
#: Enumerated, not prefix-matched. `era` is here for a different reason
#: than the rest: DESIGN §7.3 excludes it because it invites memorising
#: regime and is unavailable for a future prediction anyway.
FORBIDDEN_COLS: frozenset[str] = frozenset(
    {
        # ADR 175. Outcomes like every other label here: the worst
        # excursion in the forward window is not knowable at signal
        # time, and one of these as a feature is a perfect leak.
        "trough_ret_1d",
        "trough_ret_2d",
        "trough_ret_3d",
        "trough_ret_5d",
        "trough_ret_10d",
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
    #: Serving only: rows whose `signal_type` the model was never fitted
    #: on. Counted rather than silently filtered, because the number going
    #: up means training and serving have drifted apart.
    dropped_untrained_type: int = 0


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
  -- **The signal day's own bar, for breach depth (ADR 069).**
  --
  -- `bb_pctb` already carries close-based depth. This is the other half:
  -- the signal is a *touch*, the low crosses the band, and the close can be
  -- back inside by the bell. The two are different quantities and ADR 069
  -- names the low explicitly.
  --
  -- **Causal for a `next_open` entry, which is the only entry kind this
  -- frame selects.** Day t's low is complete before day t+1's open, so the
  -- feature is known when the position is taken. It would be look-ahead for
  -- a `touch` entry, and this frame does not build one.
  LEFT JOIN LATERAL (
      SELECT b.low AS bar_low, b.high AS bar_high
        FROM bars b
       WHERE b.ticker = e.ticker AND b.ts = e.signal_date AND b."interval" = '1d'
       LIMIT 1
  ) bar ON TRUE
  -- The t-1 band, which is the band `detect` actually compared against
  -- (invariant 3). Using day t's band would measure the breach against a
  -- boundary the signal never saw.
  LEFT JOIN LATERAL (
      SELECT i.bb_lower AS band_lower, i.bb_upper AS band_upper
        FROM indicators i
       WHERE i.ticker = e.ticker AND i."interval" = '1d' AND i.ts < e.signal_date
       ORDER BY i.ts DESC
       LIMIT 1
  ) ind ON TRUE
 WHERE e.config_hash = :chash
   AND e.entry_kind = :entry_kind
   AND e.in_trade
   {row_filter}
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
    source = {
        "sector": "t",
        "mcap_usd": "u",
        "bar_low": "bar",
        "bar_high": "bar",
        "band_lower": "ind",
        "band_upper": "ind",
    }
    # The bar/band laterals already alias their outputs to the target
    # names, so a `bar.bar_low AS bar_low` would be `bar.bar_low`, which is
    # what the lateral emits. Written out rather than special-cased.
    return tuple(f"{source[n]}.{n} AS {n}" if n in source else f"e.{n}" for n in names)


def training_sql(cols: Sequence[str]) -> str:
    """The training query, rendered.

    `_SQL` became a template when `build_serving_frame` arrived, because
    the two paths differ only in how they choose rows -- by split for
    training, by date for serving. That left the split predicate at the
    call site, where `test_model_features.py` could no longer see it.

    This is where it lives now, so the guard has one thing to assert on
    and there is still exactly one copy of the string.
    """
    return _SQL.format(cols=", ".join(cols), row_filter="AND e.split_key = :split")


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
            text(training_sql(cols)),
            conn,
            params={
                "chash": config_hash,
                "split": split,
                "entry_kind": TRAINING_ENTRY_KIND,
            },
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
            "`ingest.run_sector_backfill` (ADR 148) -- note there is no "
            "`cscan sector-backfill` CLI command; this message named one "
            "until 2026-09-04. If the names are ETFs or structured products "
            "(QQQ, SPY, VOO, IBIT, GJS), they have no sector by nature and "
            "the repair is to exclude them, not to backfill."
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


def restrict_to_trained_types(
    frame: pd.DataFrame, trained_types: Sequence[str] | None
) -> tuple[pd.DataFrame, int]:
    """Keep only rows whose `signal_type` the model was fitted on.

    `None` means no restriction, for callers that have no fit in hand.

    **An empty sequence drops everything, deliberately.** A fit that saw no
    signal types is a broken fit, and scoring the whole population off it
    would be the exact failure this guards against, so the empty case must
    not read as "no filter".
    """
    if trained_types is None:
        return frame, 0
    before = len(frame)
    kept = frame[frame["signal_type"].isin(list(trained_types))].reset_index(drop=True)
    return kept, before - len(kept)


def build_serving_frame(
    engine: Engine,
    config_hash: str,
    since: date,
    require_sector: bool = False,
    trained_types: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, FrameReport]:
    """Recent events, ready for inference, with the labels removed.

    **Predicting on holdout rows is not spending the holdout. Scoring them
    is.** Every event after 2024-01-02 carries `split_key = 'holdout'` by
    date (ADR 019), so anything that serves live predictions necessarily
    runs over holdout rows. That is fine: the holdout is a measurement
    budget, and producing a number for display measures nothing. What would
    spend it is comparing those numbers against their realised outcomes.

    So this frame **drops `LABEL_COLS` entirely** rather than carrying them
    as NULLs. A caller cannot accidentally score it, because the columns a
    loss would need are not present and the failure is an immediate
    `KeyError` instead of a quiet number in a report. `build_training_frame`
    keeps its `split == "holdout"` refusal for the same reason; this is the
    other half of that guard, and the two together mean no code path reaches
    a holdout label without deleting one of them on purpose.

    `require_sector` defaults False, the reverse of the training path. A
    name with an unresolved sector must not silently stop the nightly
    prediction write for every other name; it is dropped and counted in the
    report. Training raises instead, because there the same row would enter
    a categorical as a NULL level and corrupt the fit.
    """
    cols = _select_columns()
    params: dict[str, Any] = {
        "chash": config_hash,
        "since": since,
        "entry_kind": TRAINING_ENTRY_KIND,
    }
    with engine.connect() as conn:
        frame = pd.read_sql(
            text(_SQL.format(cols=", ".join(cols), row_filter="AND e.signal_date >= :since")),
            conn,
            params=params,
        )

    trainable, etf, missing = partition_for_training(
        list(zip(frame["ticker"], frame["sector"], strict=True))
    )
    if missing and require_sector:
        sample = sorted({frame["ticker"].iloc[i] for i in missing})[:10]
        raise ValueError(f"{len(missing)} event(s) carry a blank sector, e.g. {sample}")

    kept = frame.iloc[trainable].reset_index(drop=True)
    kept = _coerce_boolean_features(kept)
    kept = _add_derived(kept)
    kept = kept.drop(columns=[c for c in LABEL_COLS if c in kept.columns])

    # **Only signal types the model was actually fitted on.** Found
    # 2026-09-08: 4,207 of 8,699 predictions -- 48% -- were for
    # `stoch_oversold`/`stoch_overbought`, which the training frame contains
    # ZERO of. The asymmetry is structural: training drops rows with NULL
    # labels, which excludes them, while this frame drops `LABEL_COLS`
    # outright (so a caller cannot score the holdout) and therefore has
    # nothing left to filter on.
    #
    # A probability for a signal type the model never saw is extrapolation
    # wearing a calibrated number, and it reached the screener looking
    # exactly like the other half.
    #
    # Derived from the training frame rather than hardcoded, so the two
    # cannot drift: when stochastic rows start carrying labels they enter
    # training first and become predictable second, in that order.
    kept, dropped_untrained = restrict_to_trained_types(kept, trained_types)

    return kept, FrameReport(
        rows=len(kept),
        dropped_etf=len(etf),
        dropped_missing_sector=len(missing),
        dropped_no_label=0,
        dropped_untrained_type=dropped_untrained,
    )


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

    `breach_depth` was derived here until ADR 177 deleted it. What remains is
    four raw inputs stay meta and never reach the matrix.

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
