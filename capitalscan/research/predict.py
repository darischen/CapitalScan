"""Fit, calibrate, and turn the excursion heads into publishable probabilities.

ADR 174 (`p_touch`) and ADR 175 (`p_adverse`). This is the whole shipping
path for Phase 6's product, and it is short because the model already
existed -- what was missing was never a network, it was the three steps
between a softmax and a number a reader can act on.

    fit          the multi-task ensemble, on train only
    calibrate    reliability tables on validate, one per published field
    apply        raw probability -> calibrated probability + interval

**Every published field is read off a predicted CDF, not fitted as its own
classifier.** `touched_3pct` is exactly `peak_ret_5d >= 0.03` -- agreement
1.000 across 163,424 train events, because that is how the label is defined
-- so `exceedance(peak_pmf, grid, 0.03)` is not an approximation of
`Prediction.p_touch_3`, it *is* that quantity. The same holds at every
other threshold, above and below. Six contract fields, no extra heads
beyond the two ADR 175 adds for the adverse family.

**Why the thresholds map onto two horizons.** 2/3/5% read the 5-day peak
head; 10% reads the 10-day. A 10% favourable excursion inside five sessions
happens in 1.4% of events, and a probability estimated against a base rate
that thin is dominated by its own sampling noise. At ten days it is 7.1%,
which the calibration buckets can resolve.

**The realised outcome is computed from the label, never queried
separately.** An earlier version read `events.touched_*pct` by id. That is
a second definition of the same fact, and two definitions can disagree
after a config change while both look fine. The frame already carries
`peak_ret_5d` and `trough_ret_5d`; thresholding them *is* the definition.

**Calibration is fitted on validate and never on train.** A reliability
table fitted on the rows the network minimised its loss on measures how
well it memorised them. Validate is the only split available -- the holdout
was spent under ADR 172 -- and its numbers are optimistic by an unmeasured
amount because validate has been scored many times across many
architectures. That caveat travels into every row through `MODEL_CAVEAT`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core import calibration as calib
from capitalscan.core import distributions as dist
from capitalscan.core import folds as core_folds
from capitalscan.research import features as feat
from capitalscan.research import neural, train


@dataclass(frozen=True)
class Target:
    """One published probability, and everything needed to compute it.

    `direction` is the reason this is a dataclass rather than a tuple.
    "above" and "below" produce numbers in the same range, monotone in the
    same direction, so a swapped one is not a crash -- it silently reports
    the probability that the trade did *not* go against you, which would
    survive a reliability check and be wrong. Naming the field makes its
    pairing with `family` explicit: a peak head is asked how likely a rise
    past a threshold is, a trough head how likely a fall past one.
    """

    field: str
    family: str
    horizon: int
    threshold: float
    direction: str

    def realised(self, labels: np.ndarray) -> np.ndarray:
        """The 0/1 outcome for this target, from its own label column."""
        if self.direction == "above":
            return (labels >= self.threshold).astype(float)
        return (labels <= self.threshold).astype(float)

    def probability(self, pmf: np.ndarray, grid: np.ndarray) -> np.ndarray:
        """The raw model probability, read off the predicted CDF."""
        if self.direction == "above":
            return dist.exceedance(pmf, grid, self.threshold)
        return dist.shortfall(pmf, grid, self.threshold)

    @property
    def label_column(self) -> str:
        return train.label_for(self.family, self.horizon)


#: The six fields `Prediction` declares and Phase 5 left empty.
TARGETS: tuple[Target, ...] = (
    Target("p_touch_2", "peak", 5, 0.02, "above"),
    Target("p_touch_3", "peak", 5, 0.03, "above"),
    Target("p_touch_5", "peak", 5, 0.05, "above"),
    Target("p_touch_10", "peak", 10, 0.10, "above"),
    # ADR 175. `trough_ret_5d` is negative when the position went against
    # you, so these are shortfall probabilities and their thresholds are
    # negative. Both read the 5-day head, because the adverse question a
    # reader has is about the window the exit policy acts on.
    Target("p_adverse_3", "trough", 5, -0.03, "below"),
    Target("p_adverse_5", "trough", 5, -0.05, "below"),
)

#: The field whose bucket becomes the row's headline `ci_low`/`ci_high`.
#: 3% is the strongest combination of skill and sample: +5.54% Brier skill
#: on a 0.516 base rate, so both outcomes are well populated in every
#: bucket. `p_touch_10` has the higher AUC but a 0.071 base rate, which
#: leaves its lower buckets nearly empty of positives.
HEADLINE = "p_touch_3"

#: Which head backs `Prediction.q05..q95`. The terminal family is the
#: return actually realised at the horizon, which is what a quantile fan
#: over "the return" means; the peak and trough families are extremes and
#: their quantiles would answer a different question under the same name.
FAN_TASK: tuple[str, int] = ("terminal", 5)

#: The taus `predictions` has columns for.
FAN_TAUS: tuple[float, ...] = (0.05, 0.25, 0.50, 0.75, 0.95)

#: Re-exported from `core`, which is where it lives so that `handlers`
#: can reach it without importing this module. See the definition there.
MODEL_CAVEAT = calib.MODEL_CAVEAT

#: `q05`..`q95`, the column names the fan lands in.
_FAN_COLUMNS: tuple[str, ...] = tuple(f"q{int(round(t * 100)):02d}" for t in FAN_TAUS)


@dataclass(frozen=True)
class FittedPredictor:
    """An ensemble plus the reliability tables that make it publishable.

    The two travel together on purpose. A table fitted against a different
    ensemble miscalibrates silently -- it produces plausible numbers, never
    an error -- so `model_version` covers both, and ADR 175's move from
    four heads to six invalidates ADR 174's tables rather than letting them
    be reused.
    """

    ensemble: neural.Ensemble
    tables: dict[str, calib.ReliabilityTable]
    model_version: str
    n_train: int
    n_calibrate: int
    #: The signal types present in the training frame. Carried so serving
    #: can restrict to them: a probability for a type the model never saw
    #: is extrapolation wearing a calibrated number, and until 2026-09-08
    #: 48% of predictions were exactly that.
    trained_signal_types: tuple[str, ...] = ()

    def apply(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Raw and calibrated probabilities for every row of `frame`.

        One `predict_pmf` for all six fields rather than one per field:
        the ensemble runs every head in a single forward pass, and calling
        it per target would repeat that six times for identical output.
        """
        pmf = self.ensemble.predict_pmf(frame)
        out = pd.DataFrame(index=frame.index)

        # The quantile fan, from the terminal 5-day head. DESIGN 7.4
        # defines the field and it is read off a CDF the forward pass has
        # already produced, so it costs one interpolation.
        #
        # **Stored, not displayed.** `terminal_h5_q50` is negative out of
        # sample (ADR 172) and no surface renders it. It is written because
        # `outcomes` scores a pinball loss against it, which is how the
        # forward log measures the distribution rather than only the
        # thresholds -- and a fan that is never recorded can never be
        # scored, which is the state ADR 174 described the code as being in
        # while the code in fact wrote NULLs.
        for tau, values in self.ensemble.fan(frame, *FAN_TASK).items():
            out[f"q{int(round(tau * 100)):02d}"] = values

        for target in TARGETS:
            k = neural.TASKS.index((target.family, target.horizon))
            raw = target.probability(pmf[:, k, :], self.ensemble.grids[k])
            table = self.tables[target.field]
            # **One call for the point and its interval (ADR 192).** They
            # are interpolated together, so taking the point from `band`
            # and the bounds from `lookup` would pair a value with an
            # interval that need not contain it -- the exact failure the
            # old piecewise-constant design existed to avoid.
            #
            # `lookup` is still the source of the *bucket index*, which is
            # a statement about which block the raw score fell in and stays
            # discrete by nature.
            bands = [table.band(float(v)) for v in raw]
            buckets = [table.lookup(float(v)) for v in raw]
            out[f"{target.field}_raw"] = raw
            out[target.field] = [b[0] for b in bands]
            # Every field keeps its own interval, not just the headline.
            # Invariant 8 attaches to each published probability, and an
            # interval borrowed from another field's reliability table is
            # an interval for a different quantity that renders correctly.
            out[f"{target.field}__lo"] = [b[1] for b in bands]
            out[f"{target.field}__hi"] = [b[2] for b in bands]
            out[f"{target.field}__n_eff"] = [b[3] for b in bands]
            out[f"{target.field}__bucket"] = [b.index for b in buckets]
            if target.field == HEADLINE:
                out["calib_bucket"] = [f"{target.field}:b{b.index}" for b in buckets]
                out["calib_n_eff"] = [b[3] for b in bands]
                out["ci_low"] = [b[1] for b in bands]
                out["ci_high"] = [b[2] for b in bands]
        return out


def fit_and_calibrate(
    engine: Engine,
    config_hash: str,
    git_sha: str,
    seeds: Sequence[int] = neural.DEFAULT_SEEDS,
    n_buckets: int = calib.DEFAULT_BUCKETS,
) -> FittedPredictor:
    """Fit on train, calibrate on validate, and return both together.

    Raises:
        ValueError: if a target's label is missing from the frame, or is
            constant on validate. Both mean a mis-joined or unbackfilled
            label, and shipping an uncalibrated probability because the
            calibration step quietly no-opped is the failure this prevents.
    """
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())

    train_frame, _ = feat.build_training_frame(engine, config_hash, split="train")
    valid_frame, _ = feat.build_training_frame(engine, config_hash, split="validate")

    ensemble = neural.fit(train_frame, calendar, seeds=seeds)

    pmf = ensemble.predict_pmf(valid_frame)
    weights = np.asarray(core_folds.cluster_weights(list(valid_frame["cluster_id"])))

    tables: dict[str, calib.ReliabilityTable] = {}
    for target in TARGETS:
        if target.label_column not in valid_frame.columns:
            raise ValueError(
                f"{target.label_column} is not on the frame, so {target.field} "
                "cannot be calibrated. It reaches the frame through "
                "`features.LABEL_COLS` only once backfilled -- see "
                "`peak_labels.backfill_extremum_labels`."
            )
        labels = pd.to_numeric(valid_frame[target.label_column], errors="coerce").to_numpy(float)
        k = neural.TASKS.index((target.family, target.horizon))
        raw = target.probability(pmf[:, k, :], ensemble.grids[k])
        keep = ~np.isnan(labels)
        tables[target.field] = calib.build_reliability(
            target.field,
            raw[keep],
            target.realised(labels[keep]),
            weights=weights[keep],
            n_buckets=n_buckets,
        )

    return FittedPredictor(
        ensemble=ensemble,
        tables=tables,
        model_version=f"adr175-{config_hash[:8]}-{git_sha[:7]}",
        n_train=len(train_frame),
        n_calibrate=len(valid_frame),
        trained_signal_types=tuple(sorted(set(train_frame["signal_type"].astype(str)))),
    )


def build_rows(
    predictor: FittedPredictor,
    frame: pd.DataFrame,
    config_hash: str,
    run_id: str,
    git_sha: str,
) -> list[dict[str, Any]]:
    """Rows ready for `predictions`, one per event in `frame`.

    Keyed on `event_id`, not on `(ticker, as_of)`. A ticker can fire twice
    in one day and the two can be **opposite sides**, and `p_touch` is the
    probability of a favourable excursion *for the side the signal
    assigned* -- so collapsing them would let the screener render one
    side's row beside the other side's probability.

    A row whose headline probability is not finite is dropped rather than
    written as NULL: the screener views LEFT JOIN this table, and a row of
    NULLs is indistinguishable on screen from no prediction at all while
    still occupying the key a later good prediction needs.
    """
    applied = predictor.apply(frame)

    # Columns as arrays, not `frame.at[i, col]` in a loop. `.at` on a mixed
    # frame returns pandas' full scalar union, so every `float()` around it
    # is unprovable to a type checker and every access re-resolves the
    # dtype. Pulling the columns once is both typed and faster.
    tickers = frame["ticker"].astype(str).tolist()
    dates = frame["signal_date"].tolist()
    event_ids = frame["id"].astype("int64").tolist()
    signal_types = frame["signal_type"].astype(str).tolist()
    sides = frame["side"].astype(str).tolist()
    # Absent when the caller built a trade-only frame, which is the default
    # and the only shape `nightly` produces. Defaulting to True there keeps
    # every existing path writing `cosmetic = false`.
    in_trade = (
        frame["in_trade"].fillna(False).astype(bool).tolist()
        if "in_trade" in frame.columns
        else [True] * len(frame)
    )
    # `_SQL` filters on `entry_kind` rather than selecting it, so the frame
    # carries it only when a caller asked for it. `TRAINING_ENTRY_KIND` is
    # the value that filter used, so it is the right fallback rather than a
    # guess -- but read the column when it is there, so this keeps working
    # if the frame ever spans more than one kind.
    entry_kinds = (
        frame["entry_kind"].astype(str).tolist()
        if "entry_kind" in frame.columns
        else [feat.TRAINING_ENTRY_KIND] * len(frame)
    )
    columns = (
        [t.field for t in TARGETS]
        + [f"{HEADLINE}_raw", "calib_n_eff", "ci_low", "ci_high"]
        + list(_FAN_COLUMNS)
    )
    for t in TARGETS:
        columns += [f"{t.field}__lo", f"{t.field}__hi", f"{t.field}__n_eff", f"{t.field}__bucket"]
        # `apply` names the raw column `<field>_raw`; the per-field block
        # below reads it under a double-underscore alias so one loop can
        # pull every column it needs by a single naming rule.
        applied[f"{t.field}__raw"] = applied[f"{t.field}_raw"]
        columns.append(f"{t.field}__raw")
    probs = {name: applied[name].astype("float64").to_numpy() for name in columns}
    buckets = applied["calib_bucket"].astype(str).tolist()

    rows: list[dict[str, Any]] = []
    for i in range(len(frame)):
        if not np.isfinite(probs[HEADLINE][i]):
            continue
        row: dict[str, Any] = {
            "ticker": tickers[i],
            "as_of": dates[i],
            "event_id": event_ids[i],
            "model_version": predictor.model_version,
            "config_hash": config_hash,
            "run_id": run_id,
            "git_sha": git_sha,
            "p_touch_3_raw": float(probs[f"{HEADLINE}_raw"][i]),
            **{
                name: (float(probs[name][i]) if np.isfinite(probs[name][i]) else None)
                for name in _FAN_COLUMNS
            },
            # **Always true here, and written anyway.** `build_serving_frame`
            # has already dropped every row outside the fitted population,
            # so the writer cannot produce an unscored one. The column
            # defaults to `false` precisely so that a row written by some
            # future path that has *not* been filtered stays out of the
            # screener until someone looks at it. Setting it explicitly is
            # this path asserting it did the check.
            # **The natural key the screener views join on.**
            #
            # Migration `e4b19c86d275` added these columns and backfilled
            # them from `events`; the writer was never taught to set them.
            # So every prediction written after that migration carried NULLs
            # and could not be joined -- measured 2026-09-09, 11,006 of
            # 19,705 rows, invisible on the screener while looking perfectly
            # healthy in the table.
            #
            # `event_id` is still written and still correct. It is useless
            # across a sync, which is the whole reason the natural key
            # exists: `events` keys on a five-column tuple, so serving
            # assigns its own `id` and a research `event_id` points at
            # nothing there.
            "signal_type": signal_types[i],
            "entry_kind": entry_kinds[i],
            "model_scored": True,
            # **Cosmetic when the event was not in the trade universe
            # (ADR 183).** The model is fitted on `in_trade` rows only --
            # `peak_labels` writes labels for those alone, so `in_watch`
            # has 443 labelled rows against 160,473 and there is nothing
            # there to fit on. A probability for one is extrapolation, and
            # ADR 180 is what happens when that ships unflagged.
            #
            # `model_scored` does not cover this. That flag answers "was
            # this signal *type* fitted"; a cosmetic row can be a fitted
            # type on an unfitted population, and both must be visible
            # separately or the reader cannot tell which caveat applies.
            "cosmetic": not bool(in_trade[i]),
            "calib_bucket": buckets[i],
            "calib_n_eff": float(probs["calib_n_eff"][i]),
            "ci_low": float(probs["ci_low"][i]),
            "ci_high": float(probs["ci_high"][i]),
            # The fan stays in the payload because DESIGN §7.4 defines it
            # and it costs nothing to read off the same CDF. It is NOT the
            # product: `terminal_h5_q50` is negative out of sample
            # (ADR 172) and no surface displays it.
            "features_json": {
                "signal_type": signal_types[i],
                "side": sides[i],
                "caveat": MODEL_CAVEAT,
                "n_train": predictor.n_train,
                "n_calibrate": predictor.n_calibrate,
            },
        }
        calibration: dict[str, Any] = {}
        for target in TARGETS:
            value = probs[target.field][i]
            if not np.isfinite(value):
                row[target.field] = None
                continue
            row[target.field] = float(value)
            calibration[target.field] = {
                "p": float(value),
                "raw": float(probs[f"{target.field}__raw"][i]),
                "lo": float(probs[f"{target.field}__lo"][i]),
                "hi": float(probs[f"{target.field}__hi"][i]),
                "n_eff": float(probs[f"{target.field}__n_eff"][i]),
                "bucket": int(probs[f"{target.field}__bucket"][i]),
            }
        row["calibration_json"] = calibration
        rows.append(row)
    return rows


def expected_net_return(
    p_target: float,
    p_stop: float,
    target_pct: float,
    stop_pct: float,
    timeout_pct: float,
) -> float:
    """`E[net_ret]`, which is the reason ADR 175 exists.

        E[net_ret] = P(target) x target_pct
                   + P(stop) x stop_pct
                   + P(neither) x timeout_pct

    Measured over 163,424 train exits the three payoffs are +5.30%, -4.38%
    and -0.05%. With only `p_touch` fitted, two of the three terms were
    unavailable and a probability could not become a number to act on.

    **`p_stop` is not `p_adverse_*` unmodified**, and this function does
    not pretend otherwise -- it takes both probabilities from the caller.
    A trade can reach the target before it reaches the stop, so the two
    events are not independent and `P(stop)` is not `P(trough <= stop)`.
    The ordering is in `path` and measuring it is separate work. Until
    then this is the arithmetic, not an estimate, and nothing calls it from
    the serving path. → `BACKLOG.md`
    """
    p_neither = max(0.0, 1.0 - p_target - p_stop)
    return p_target * target_pct + p_stop * stop_pct + p_neither * timeout_pct


def latest_signal_date(engine: Engine, config_hash: str) -> date | None:
    """The most recent `signal_date` with events, for the default window.

    Scoped to `in_trade`, matching the population `build_serving_frame`
    scores. An unfiltered max could sit days ahead of the newest scorable
    event and silently shrink the lookback window to nothing.
    """
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT max(signal_date) FROM events "
                "WHERE config_hash = :c AND in_trade AND entry_kind = 'next_open'"
            ),
            {"c": config_hash},
        ).scalar()
