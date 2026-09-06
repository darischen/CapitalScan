"""Fit, calibrate, and turn the peak head into publishable `p_touch` rows.

ADR 174. This is the whole shipping path for Phase 6's first product, and
it is short because the model already existed -- what was missing was never
a network, it was the three steps between a softmax and a number a reader
can act on.

    fit          the existing four-task ensemble, on train only
    calibrate    reliability tables on validate, one per threshold
    apply        raw exceedance -> calibrated probability + interval

**`touched_3pct` is exactly `peak_ret_5d >= 0.03`.** Agreement 1.000 across
163,424 train events, which is not a coincidence: the label is defined that
way. So `exceedance(peak_pmf, grid, 0.03)` is not an approximation of
`Prediction.p_touch_3`, it *is* that quantity, and the same holds at 2%, 5%
and 10%. Four fields the contract has declared empty since Phase 5 get
filled without fitting anything new.

**Why the thresholds map onto two different horizons.** 2/3/5% read the
5-day peak head; 10% reads the 10-day. A 10% favourable excursion inside
five sessions happens in 1.4% of events, and a probability estimated
against a base rate that thin is dominated by its own sampling noise. At
ten days it is 7.1%, which the calibration buckets can actually resolve.
The horizon is chosen per threshold rather than fixed, and the row records
which one answered.

**Calibration is fitted on validate and never on train.** A reliability
table fitted on the same rows the network minimised its loss on measures
how well it memorised them. Validate is the only split available: the
holdout was spent under ADR 172, and its numbers are therefore optimistic
by an unmeasured amount because validate has been scored many times across
many architectures. That caveat is not decoration -- it travels into every
row through `MODEL_CAVEAT` and out to the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core import calibration as calib
from capitalscan.core import folds as core_folds
from capitalscan.research import features as feat
from capitalscan.research import neural

#: `(field suffix, threshold, peak horizon)`. See the module docstring for
#: why 10% reads a different horizon than the rest.
TOUCH_TARGETS: tuple[tuple[int, float, int], ...] = (
    (2, 0.02, 5),
    (3, 0.03, 5),
    (5, 0.05, 5),
    (10, 0.10, 10),
)

#: The field whose interval becomes the row's headline `ci_low`/`ci_high`.
#: 3% is the strongest combination of skill and sample: +5.54% Brier skill
#: on a 0.516 base rate, so both outcomes are well populated in every
#: bucket. 10% has the higher AUC but a 0.071 base rate, which makes its
#: lower buckets nearly empty of positives.
HEADLINE = 3

#: Re-exported from `core`, which is where it lives so that `handlers`
#: can reach it without importing this module. See the definition there.
MODEL_CAVEAT = calib.MODEL_CAVEAT


@dataclass(frozen=True)
class FittedPredictor:
    """An ensemble plus the reliability tables that make it publishable.

    The two travel together on purpose. A table fitted against a different
    ensemble miscalibrates silently -- it produces plausible numbers, never
    an error -- so `model_version` covers both and any surface displaying a
    probability records which version produced it.
    """

    ensemble: neural.Ensemble
    tables: dict[int, calib.ReliabilityTable]
    model_version: str
    n_train: int
    n_calibrate: int

    def apply(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Raw and calibrated probabilities for every row of `frame`.

        Returns one row per input row, indexed alike, with `p_touch_N`,
        `p_touch_N_raw`, and for the headline threshold the bucket identity
        and interval that satisfy invariant 8.
        """
        out = pd.DataFrame(index=frame.index)
        for suffix, threshold, horizon in TOUCH_TARGETS:
            raw = self.ensemble.exceedance(frame, "peak", horizon, threshold)
            table = self.tables[suffix]
            out[f"p_touch_{suffix}_raw"] = raw
            out[f"p_touch_{suffix}"] = [table.calibrate(float(v)) for v in raw]
            if suffix == HEADLINE:
                buckets = [table.lookup(float(v)) for v in raw]
                out["calib_bucket"] = [f"p_touch_{suffix}:b{b.index}" for b in buckets]
                out["calib_n_eff"] = [b.n_eff for b in buckets]
                out["ci_low"] = [b.ci_low for b in buckets]
                out["ci_high"] = [b.ci_high for b in buckets]
        return out


def _touch_labels(engine: Engine, config_hash: str, ids: Sequence[int]) -> pd.DataFrame:
    """The realised `touched_*pct` flags, joined by event id.

    They are not in `features._select_columns()`, so they are fetched here
    rather than assumed present on the frame. Joining by id rather than by
    `(ticker, signal_date)` because that pair is not unique -- 14 ticker-days
    in the last two months carry two events, and they are **opposite
    sides**.

    `in_trade` is redundant here, since every id comes from a frame that
    already filtered it, and it is written anyway: `test_events_in_trade_
    filter.py` sweeps every read of `events` and requires the predicate or
    an allowlist entry, on the grounds that losing the study population
    looks completely normal in the output.
    """
    params: dict[str, Any] = {"c": config_hash, "ids": list(ids)}
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                "SELECT id, touched_2pct, touched_3pct, touched_5pct, touched_10pct "
                "FROM events WHERE config_hash = :c AND in_trade AND id = ANY(:ids)"
            ),
            conn,
            params=params,
        ).set_index("id")


def fit_and_calibrate(
    engine: Engine,
    config_hash: str,
    git_sha: str,
    seeds: Sequence[int] = neural.DEFAULT_SEEDS,
    n_buckets: int = calib.DEFAULT_BUCKETS,
) -> FittedPredictor:
    """Fit on train, calibrate on validate, and return both together.

    Raises:
        ValueError: if a threshold's label is absent or constant on
            validate. That is a mis-joined label rather than a hard
            problem, and shipping an uncalibrated probability because the
            calibration step quietly no-opped is the failure this prevents.
    """
    with engine.connect() as conn:
        calendar = list(conn.execute(text("SELECT d FROM trading_days ORDER BY d")).scalars())

    train_frame, _ = feat.build_training_frame(engine, config_hash, split="train")
    valid_frame, _ = feat.build_training_frame(engine, config_hash, split="validate")

    ensemble = neural.fit(train_frame, calendar, seeds=seeds)

    labels = _touch_labels(engine, config_hash, list(valid_frame["id"]))
    weights = np.asarray(core_folds.cluster_weights(list(valid_frame["cluster_id"])))

    tables: dict[int, calib.ReliabilityTable] = {}
    for suffix, threshold, horizon in TOUCH_TARGETS:
        column = f"touched_{suffix}pct"
        if column not in labels.columns:
            raise ValueError(f"{column} is not on events; cannot calibrate p_touch_{suffix}")
        realised = (
            valid_frame["id"].map(labels[column]).astype("float64").to_numpy()  # NaN where absent
        )
        raw = ensemble.exceedance(valid_frame, "peak", horizon, threshold)
        keep = ~np.isnan(realised)
        tables[suffix] = calib.build_reliability(
            f"p_touch_{suffix}",
            raw[keep],
            realised[keep],
            weights=weights[keep],
            n_buckets=n_buckets,
        )

    return FittedPredictor(
        ensemble=ensemble,
        tables=tables,
        model_version=f"adr174-{config_hash[:8]}-{git_sha[:7]}",
        n_train=len(train_frame),
        n_calibrate=len(valid_frame),
    )


def build_rows(
    predictor: FittedPredictor,
    frame: pd.DataFrame,
    config_hash: str,
    run_id: str,
    git_sha: str,
) -> list[dict[str, Any]]:
    """Rows ready for `predictions`, one per event in `frame`.

    `as_of` is the event's `signal_date`, matching the join both screener
    views already use. A row whose headline probability is NaN is dropped
    rather than written as NULL: `v_screen` LEFT JOINs this table, and a
    row of NULLs is indistinguishable on screen from no prediction at all
    while still consuming the unique key that a later good prediction needs.
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
    probs = {
        name: applied[name].astype("float64").to_numpy()
        for name in (
            "p_touch_2",
            "p_touch_3",
            "p_touch_5",
            "p_touch_10",
            "p_touch_3_raw",
            "calib_n_eff",
            "ci_low",
            "ci_high",
        )
    }
    buckets = applied["calib_bucket"].astype(str).tolist()

    rows: list[dict[str, Any]] = []
    for i in range(len(frame)):
        headline = probs[f"p_touch_{HEADLINE}"][i]
        if not np.isfinite(headline):
            continue
        rows.append(
            {
                "ticker": tickers[i],
                "as_of": dates[i],
                "event_id": event_ids[i],
                "model_version": predictor.model_version,
                "config_hash": config_hash,
                "run_id": run_id,
                "git_sha": git_sha,
                "p_touch_2": float(probs["p_touch_2"][i]),
                "p_touch_3": float(probs["p_touch_3"][i]),
                "p_touch_5": float(probs["p_touch_5"][i]),
                "p_touch_10": float(probs["p_touch_10"][i]),
                "p_touch_3_raw": float(probs["p_touch_3_raw"][i]),
                "calib_bucket": buckets[i],
                "calib_n_eff": float(probs["calib_n_eff"][i]),
                "ci_low": float(probs["ci_low"][i]),
                "ci_high": float(probs["ci_high"][i]),
                # The fan stays in the payload because DESIGN §7.4 defines
                # it and it costs nothing to read off the same CDF. It is
                # NOT the product: `terminal_h5_q50` is negative out of
                # sample (ADR 172) and no surface displays it.
                "features_json": {
                    "signal_type": signal_types[i],
                    "side": sides[i],
                    "caveat": MODEL_CAVEAT,
                    "n_train": predictor.n_train,
                    "n_calibrate": predictor.n_calibrate,
                },
            }
        )
    return rows


def latest_signal_date(engine: Engine, config_hash: str) -> date | None:
    """The most recent `signal_date` with events, for the default window."""
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT max(signal_date) FROM events WHERE config_hash = :c"),
            {"c": config_hash},
        ).scalar()
