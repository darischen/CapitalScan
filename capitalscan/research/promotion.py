"""ADR 067/113's promotion gate, evaluated on the **validate** split.

**What this adds that `research/train.py` does not.** `fit_head` answers
"is this head worth having" with walk-forward CV *inside train*, and
deliberately returns no model object so nobody serves a booster before the
gate has run. Check 5 and the coverage check both need the opposite: a
model fitted once on all of train, asked about a split it has never seen.

**Two checks live here because they need the same fit.**

- **Check 5 (ADR 113, amended by ADR 167).** Out-of-sample pinball loss
  must beat the unconditional baseline. The kill criterion hangs on it: a
  model no better than a constant retires the two-indicator hypothesis at
  the model layer, as ADR 112 already retired it at the cell layer.
- **Coverage (DESIGN §7.6, gate check 3).** The fraction of realised
  returns at or below `Q_tau` should be `tau`, within 5 points. A head can
  post a good pinball loss and still be miscalibrated, which is exactly the
  failure a sharpness metric cannot see.

**The baseline is global, per ADR 167.** ADR 113 wrote "per-ticker-year",
inherited from the cell grid's vocabulary. Across ADR 019's temporal split
the ticker-year overlap is **zero** (2,089 train against 550 validate) and
always will be, and a faithful reconstruction would be contemporaneous --
an oracle fitted on the labels being scored. Per-sector and per-ticker are
computed alongside and gate nothing: `sector` is a model feature, so a head
clearing global but not per-sector has learned sector and little else.

**Rounds come from CV, not from early stopping on validate.** Stopping on
the split being scored would fit the model to it. The final fit uses the
median `best_iteration` across that head's folds, which is the honest
estimate available from train alone.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from capitalscan.core import folds as core_folds
from capitalscan.core import pinball
from capitalscan.research import features as feat
from capitalscan.research import train

#: Gate check 3 (DESIGN §7.7): coverage within 5 points of nominal.
COVERAGE_TOLERANCE = 0.05

#: Fallback when CV produced no usable `best_iteration` for a head.
DEFAULT_ROUNDS = 300


@dataclass(frozen=True)
class HeadEvaluation:
    """One head, fitted on train and scored on validate."""

    head: str
    family: str
    horizon: int
    tau: float
    n_train: int
    n_validate: int
    rounds: int
    model_loss: float
    baseline_global: float
    baseline_sector: float
    baseline_ticker: float
    baseline_scaled: float
    coverage: float

    @property
    def beats_global(self) -> bool:
        """Check 5. The only comparison the kill criterion may read."""
        return self.model_loss < self.baseline_global

    @property
    def beats_scaled(self) -> bool:
        """**Vacuous on loss, and kept only so the vacuity is visible.**

        Built to ask whether the model's wins were crude volatility
        scaling: a raw constant cannot follow a regime whose volatility
        rose 12-28%, so beating it is compatible with the model doing
        nothing else.

        Measured 2026-09-02, it cannot answer that. The scaled constant has
        **higher pinball loss than the raw one on all 20 heads**, by up to
        3.6x (`peak_h10_q95`, 0.008052 -> 0.029068), so this is an *easier*
        bar and passing it means nothing.

        The cause is the trade DESIGN §7.6 keeps two metrics for.
        Multiplying a constant by each row's own sigma improves **coverage**
        and worsens **sharpness**: the prediction gains variance, which
        pinball loss charges for and coverage does not. Compare
        `baseline_scaled` against `baseline_global` before reading this.

        The question is still open and is answerable only by fitting the
        model in scale-free units too, so both sides are in the same units
        -- item 2 of the Phase 6 refinement plan.
        """
        return self.model_loss < self.baseline_scaled

    @property
    def beats_sector(self) -> bool:
        """Diagnostic. `sector` is a feature, so this is the fair harder bar."""
        return self.model_loss < self.baseline_sector

    @property
    def improvement(self) -> float:
        """Fraction of the global baseline loss removed. Negative is worse."""
        if self.baseline_global == 0:
            return 0.0
        return (self.baseline_global - self.model_loss) / self.baseline_global

    @property
    def coverage_error(self) -> float:
        return self.coverage - self.tau

    @property
    def coverage_ok(self) -> bool:
        return abs(self.coverage_error) <= COVERAGE_TOLERANCE


@dataclass(frozen=True)
class GateReport:
    """Every head, plus the two verdicts that matter."""

    evaluations: tuple[HeadEvaluation, ...]

    @property
    def check5_passes(self) -> bool:
        """ADR 113's kill criterion, read literally.

        > If the model fails check 5 on the validation split -- **no better
        > than the unconditional baseline in pinball loss at any horizon**
        > -- the two-indicator hypothesis is retired at the model layer.

        "At any horizon" makes the failure condition universal: the
        hypothesis dies only if *nothing* beats the baseline. So passing
        requires at least one head to beat it. That is a deliberately low
        bar, and it is the bar a hypothesis is retired against; the
        per-head detail below is what anyone should actually read.
        """
        return any(e.beats_global for e in self.evaluations)

    @property
    def coverage_passes(self) -> bool:
        """Gate check 3: every tau within tolerance, not merely most."""
        return all(e.coverage_ok for e in self.evaluations)

    def summary(self) -> pd.DataFrame:
        rows = [
            {
                "head": e.head,
                "n_val": e.n_validate,
                "rounds": e.rounds,
                "model_loss": round(e.model_loss, 6),
                "baseline": round(e.baseline_global, 6),
                "improve_pct": round(e.improvement * 100, 2),
                "beats_global": e.beats_global,
                "beats_sector": e.beats_sector,
                # The loss, not only the verdict: the scaled constant is
                # uniformly worse on pinball, so the boolean alone would
                # read as a passed test rather than an easier bar.
                "baseline_scaled": round(e.baseline_scaled, 6),
                "beats_scaled": e.beats_scaled,
                "coverage": round(e.coverage, 4),
                "cov_err": round(e.coverage_error, 4),
                "cov_ok": e.coverage_ok,
            }
            for e in self.evaluations
        ]
        return pd.DataFrame(rows)


def _matrix(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame[list(feat.FEATURE_COLS)].copy()
    for col in feat.CATEGORICAL_COLS:
        x[col] = x[col].astype("category")
    return x


def grouped_baseline(
    train_frame: pd.DataFrame,
    validate_frame: pd.DataFrame,
    label: str,
    tau: float,
    by: str,
) -> np.ndarray:
    """Per-group empirical quantile from train, aligned to validate rows.

    Groups absent from train fall back to the global quantile — 56 of 392
    validate tickers are new, and a NaN there would silently drop rows from
    the comparison rather than scoring them.
    """
    y_train = pd.to_numeric(train_frame[label], errors="coerce")
    overall = pinball.unconditional_quantile(list(y_train.dropna()), tau)

    per_group: dict[object, float] = {}
    for key, chunk in train_frame.groupby(by, observed=True):
        labels = pd.to_numeric(chunk[label], errors="coerce").dropna()
        if len(labels):
            per_group[key] = pinball.unconditional_quantile(list(labels), tau)

    return np.asarray([per_group.get(key, overall) for key in validate_frame[by]], dtype=float)


def _fit_and_predict(
    train_frame,
    validate_frame,
    family: str,
    horizon: int,
    tau: float,
    calendar: Sequence[date],
    rounds: int | None,
) -> tuple[np.ndarray, int]:
    """Fit one head on all of train, return its raw validate predictions.

    Separated from scoring because **quantile crossing is repaired across
    τ, not within one head** (DESIGN §7.4), so the five heads of a
    (family, horizon) have to be predicted before any of them is scored.
    """
    import lightgbm as lgb

    label = train.label_for(family, horizon)

    if rounds is None:
        cv = train.fit_head(train_frame, family, horizon, tau, calendar)
        iters = [r.best_iteration for r in cv if r.best_iteration > 0]
        rounds = int(statistics.median(iters)) if iters else DEFAULT_ROUNDS

    y_tr = pd.to_numeric(train_frame[label], errors="coerce").to_numpy(float)
    tr_ok = ~np.isnan(y_tr)
    w_tr = np.asarray(core_folds.cluster_weights(list(train_frame["cluster_id"])))

    params = {**train.LGBM_PARAMS, "objective": "quantile", "alpha": tau}
    booster = lgb.train(
        params,
        lgb.Dataset(
            _matrix(train_frame)[tr_ok],
            label=y_tr[tr_ok],
            weight=w_tr[tr_ok],
            free_raw_data=False,
        ),
        num_boost_round=rounds,
    )
    return np.asarray(booster.predict(_matrix(validate_frame)), dtype=float), int(rounds)


#: Feature carrying the volatility scale, known at entry. Used to build a
#: baseline that can *rescale*, which a tree cannot: it splits on
#: `rv_pct_252d`, it does not multiply by it.
SCALE_COL = "rv_pct_252d"


def scaled_baseline(
    train_frame: pd.DataFrame,
    validate_frame: pd.DataFrame,
    label: str,
    tau: float,
) -> np.ndarray:
    """A constant in scale-free units, rescaled per validate row.

    **Why check 5 needs this and ADR 167's raw constant is not enough.**
    The measured shift from train (2010-2021) to validate (2022-2023) is a
    volatility increase -- `fwd_ret_5d` sd +12%, `peak_ret_5d` q75 +28%. A
    raw constant cannot follow it, so *any* predictor that scales with
    volatility beats it, whether or not it knows anything about direction.
    The peak family's 10-20% gains are exactly that shape.

    This baseline removes the excuse: `quantile(R / sigma)` fitted on
    train, multiplied by each validate row's own `sigma`. It is still
    featureless in every sense that matters -- one number, plus a scale the
    model already has as an input.

    Rows with a missing or non-positive scale fall back to the raw
    constant rather than being dropped, so both baselines are measured on
    the same rows (the ADR 165 lesson: which rows are averaged is where the
    errors live).
    """
    y_tr = pd.to_numeric(train_frame[label], errors="coerce")
    s_tr = pd.to_numeric(train_frame[SCALE_COL], errors="coerce")
    ok = y_tr.notna() & s_tr.notna() & (s_tr > 0)

    raw_constant = pinball.unconditional_quantile(list(y_tr.dropna()), tau)
    if not ok.any():
        return np.full(len(validate_frame), raw_constant, dtype=float)

    z_constant = pinball.unconditional_quantile(list((y_tr[ok] / s_tr[ok])), tau)

    s_va = pd.to_numeric(validate_frame[SCALE_COL], errors="coerce").to_numpy(float)
    usable = ~np.isnan(s_va) & (s_va > 0)
    return np.where(usable, z_constant * np.nan_to_num(s_va), raw_constant)


def evaluate_family(
    train_frame: pd.DataFrame,
    validate_frame: pd.DataFrame,
    family: str,
    horizon: int,
    calendar: Sequence[date],
    rounds: dict[str, int] | None = None,
) -> list[HeadEvaluation]:
    """The five τ of one (family, horizon), fitted and scored together.

    **Sorted across τ before scoring, per DESIGN §7.4.** The heads are
    fitted independently and carry no monotonicity constraint, so a fitted
    `Q_0.25` can exceed `Q_0.50` on some feature vectors. `sort_quantiles`
    is the documented repair and is applied at prediction time only --
    reordering training labels would be fabricating outcomes.

    This is why the unit of evaluation is a *family and horizon* rather
    than a head. The first version of this module scored each head alone,
    which skipped the sort entirely: `train.sort_quantiles` had no
    production caller at all, having been written in Session 23 and never
    wired in. Coverage computed on unsorted predictions is measuring a fan
    the design does not ship.
    """
    raw: dict[float, np.ndarray] = {}
    used_rounds: dict[float, int] = {}
    for tau in train.TAUS:
        name = train.head_name(family, horizon, tau)
        pred, n_rounds = _fit_and_predict(
            train_frame,
            validate_frame,
            family,
            horizon,
            tau,
            calendar,
            (rounds or {}).get(name),
        )
        raw[tau] = pred
        used_rounds[tau] = n_rounds

    sorted_pred = train.sort_quantiles(raw)
    return score_family(
        train_frame, validate_frame, family, horizon, sorted_pred, rounds=used_rounds
    )


def score_family(
    train_frame: pd.DataFrame,
    validate_frame: pd.DataFrame,
    family: str,
    horizon: int,
    sorted_pred: dict[float, np.ndarray],
    rounds: dict[float, int] | None = None,
) -> list[HeadEvaluation]:
    """Score an already-built fan against every baseline. Fits nothing.

    **Split out of `evaluate_family` so a challenger is scored by the same
    code as the incumbent.** The gate's meaning lives in these baselines
    and in the cluster weighting, not in LightGBM, and an experiment that
    reimplements the scoring is comparing two measurements rather than two
    models. Session 24 needed exactly that: TabFM and the neural fans
    produce `sorted_pred` by entirely different means and then arrive here.

    `sorted_pred` must already be monotone across τ (DESIGN §7.4). Sorting
    is the *producer's* job because a model with a monotone parameterisation
    has nothing to repair, and silently re-sorting here would hide a
    challenger whose fan crosses.

    `rounds` is LightGBM's `num_boost_round` and is reported, never used.
    A model with no such notion passes nothing and records 0.
    """
    label = train.label_for(family, horizon)
    y_va = pd.to_numeric(validate_frame[label], errors="coerce").to_numpy(float)
    va_ok = ~np.isnan(y_va)
    w_va = np.asarray(core_folds.cluster_weights(list(validate_frame["cluster_id"])))
    used_rounds = rounds or {}

    out: list[HeadEvaluation] = []
    for tau in train.TAUS:
        pred = sorted_pred[tau][va_ok]
        y = y_va[va_ok]
        w = w_va[va_ok]

        model_loss = pinball.pinball_loss(pred, y, tau, weights=w)

        y_tr = pd.to_numeric(train_frame[label], errors="coerce").to_numpy(float)
        constant = pinball.unconditional_quantile(list(y_tr[~np.isnan(y_tr)]), tau)
        b_global = pinball.pinball_loss([constant] * int(va_ok.sum()), y, tau, weights=w)
        b_sector = pinball.pinball_loss(
            grouped_baseline(train_frame, validate_frame, label, tau, "sector")[va_ok],
            y,
            tau,
            weights=w,
        )
        b_ticker = pinball.pinball_loss(
            grouped_baseline(train_frame, validate_frame, label, tau, "ticker")[va_ok],
            y,
            tau,
            weights=w,
        )
        b_scaled = pinball.pinball_loss(
            scaled_baseline(train_frame, validate_frame, label, tau)[va_ok],
            y,
            tau,
            weights=w,
        )

        # Coverage: the weighted fraction of realised returns at or below
        # the predicted quantile (DESIGN §7.6). Cluster-weighted for the
        # same reason the loss is -- a four-event cluster must not vote
        # four times here either.
        below = (y <= pred).astype(float)
        coverage = float((below * w).sum() / w.sum())

        out.append(
            HeadEvaluation(
                head=train.head_name(family, horizon, tau),
                family=family,
                horizon=horizon,
                tau=tau,
                n_train=int((~np.isnan(y_tr)).sum()),
                n_validate=int(va_ok.sum()),
                rounds=int(used_rounds.get(tau, 0)),
                model_loss=model_loss,
                baseline_global=b_global,
                baseline_sector=b_sector,
                baseline_ticker=b_ticker,
                baseline_scaled=b_scaled,
                coverage=coverage,
            )
        )
    return out


def crossing_rate(predictions: dict[float, np.ndarray]) -> float:
    """Fraction of rows where the raw fan is not monotone across τ.

    Reported rather than merely repaired: a high rate means the heads
    disagree with each other, which `sort_quantiles` hides without fixing.
    """
    taus = sorted(predictions)
    stacked = np.vstack([predictions[t] for t in taus])
    return float((np.diff(stacked, axis=0) < 0).any(axis=0).mean())


def run_gate(
    train_frame: pd.DataFrame,
    validate_frame: pd.DataFrame,
    calendar: Sequence[date],
    rounds: dict[str, int] | None = None,
) -> GateReport:
    """Every head in `train.all_heads()`, grouped by (family, horizon).

    Grouped rather than iterated head by head because the five τ of one
    horizon must be sorted against each other before scoring (DESIGN §7.4).
    The order of the output still matches `train.all_heads()`.
    """
    pairs: list[tuple[str, int]] = []
    for family, horizon, _ in train.all_heads():
        if (family, horizon) not in pairs:
            pairs.append((family, horizon))

    evaluations: list[HeadEvaluation] = []
    for family, horizon in pairs:
        evaluations.extend(
            evaluate_family(train_frame, validate_frame, family, horizon, calendar, rounds)
        )
    return GateReport(evaluations=tuple(evaluations))
