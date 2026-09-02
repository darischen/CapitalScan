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
    coverage: float

    @property
    def beats_global(self) -> bool:
        """Check 5. The only comparison the kill criterion may read."""
        return self.model_loss < self.baseline_global

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


def evaluate_head(
    train_frame: pd.DataFrame,
    validate_frame: pd.DataFrame,
    family: str,
    horizon: int,
    tau: float,
    calendar: Sequence[date],
    rounds: int | None = None,
) -> HeadEvaluation:
    """Fit one head on all of train, score it on validate.

    `rounds` defaults to the median `best_iteration` across this head's
    walk-forward folds. Early stopping is deliberately not used: its
    stopping set would be the split under test.
    """
    import lightgbm as lgb

    label = train.label_for(family, horizon)
    name = train.head_name(family, horizon, tau)

    if rounds is None:
        cv = train.fit_head(train_frame, family, horizon, tau, calendar)
        iters = [r.best_iteration for r in cv if r.best_iteration > 0]
        rounds = int(statistics.median(iters)) if iters else DEFAULT_ROUNDS

    y_tr = pd.to_numeric(train_frame[label], errors="coerce").to_numpy(float)
    y_va = pd.to_numeric(validate_frame[label], errors="coerce").to_numpy(float)
    tr_ok = ~np.isnan(y_tr)
    va_ok = ~np.isnan(y_va)

    w_tr = np.asarray(core_folds.cluster_weights(list(train_frame["cluster_id"])))
    w_va = np.asarray(core_folds.cluster_weights(list(validate_frame["cluster_id"])))

    x_tr = _matrix(train_frame)[tr_ok]
    x_va = _matrix(validate_frame)[va_ok]

    params = {**train.LGBM_PARAMS, "objective": "quantile", "alpha": tau}
    booster = lgb.train(
        params,
        lgb.Dataset(x_tr, label=y_tr[tr_ok], weight=w_tr[tr_ok], free_raw_data=False),
        num_boost_round=rounds,
    )
    pred = booster.predict(x_va)

    model_loss = pinball.pinball_loss(pred, y_va[va_ok], tau, weights=w_va[va_ok])

    constant = pinball.unconditional_quantile(list(y_tr[tr_ok]), tau)
    b_global = pinball.pinball_loss(
        [constant] * int(va_ok.sum()), y_va[va_ok], tau, weights=w_va[va_ok]
    )
    b_sector = pinball.pinball_loss(
        grouped_baseline(train_frame, validate_frame, label, tau, "sector")[va_ok],
        y_va[va_ok],
        tau,
        weights=w_va[va_ok],
    )
    b_ticker = pinball.pinball_loss(
        grouped_baseline(train_frame, validate_frame, label, tau, "ticker")[va_ok],
        y_va[va_ok],
        tau,
        weights=w_va[va_ok],
    )

    # Coverage: the fraction of realised returns at or below the predicted
    # quantile (DESIGN §7.6). Weighted by the same cluster weights, so a
    # four-event cluster does not vote four times here either.
    below = (y_va[va_ok] <= np.asarray(pred, dtype=float)).astype(float)
    coverage = float((below * w_va[va_ok]).sum() / w_va[va_ok].sum())

    return HeadEvaluation(
        head=name,
        family=family,
        horizon=horizon,
        tau=tau,
        n_train=int(tr_ok.sum()),
        n_validate=int(va_ok.sum()),
        rounds=int(rounds),
        model_loss=model_loss,
        baseline_global=b_global,
        baseline_sector=b_sector,
        baseline_ticker=b_ticker,
        coverage=coverage,
    )


def run_gate(
    train_frame: pd.DataFrame,
    validate_frame: pd.DataFrame,
    calendar: Sequence[date],
    rounds: dict[str, int] | None = None,
) -> GateReport:
    """Every head in `train.all_heads()`, in its fixed order."""
    evaluations = [
        evaluate_head(
            train_frame,
            validate_frame,
            family,
            horizon,
            tau,
            calendar,
            rounds=(rounds or {}).get(train.head_name(family, horizon, tau)),
        )
        for family, horizon, tau in train.all_heads()
    ]
    return GateReport(evaluations=tuple(evaluations))
