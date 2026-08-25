"""Fitting ADR 113's twenty heads (DESIGN §7.5).

Twenty heads: five $\\tau$ by two horizons, for the terminal and peak
families. Each is an independent LightGBM model on the shared feature set
from `research/features.py`, fitted with the pinball objective at its own
$\\tau$.

**Purged walk-forward CV, never random K-fold.** `core/folds.py` carries
the argument and the two-sided repair. This module is the caller that has
to use it correctly, which mostly means: build the fold, purge the training
side, embargo the validation side, and never let a frame reach LightGBM
that has not been through both.

**Every fit is scored against the unconditional baseline.** ADR 113's check
5 is not a post-hoc report — a head that cannot beat a constant has found
nothing, and the fold report says so per head rather than in aggregate. An
average across twenty heads would let four good ones hide sixteen useless
ones, which is the shape of result ADR 112 already warned about.

**Nothing here promotes a model or writes a prediction.** Fitting and
promoting are separate acts (ADR 067), and a module that could do both
would eventually do the second by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from capitalscan.core import folds as core_folds
from capitalscan.core import pinball
from capitalscan.research import features as feat

#: ADR 113's fan.
TAUS: tuple[float, ...] = (0.05, 0.25, 0.50, 0.75, 0.95)

#: ADR 113's horizons. 1, 2 and 3 are dropped deliberately.
HORIZONS: tuple[int, ...] = (5, 10)

#: DESIGN §7.5, conservative given an effective sample near 8,000. Written
#: here rather than in `core/config.py` because these are not sweepable
#: study parameters -- changing them does not move `config_hash`, and
#: invariant 9 is about thresholds the engine compares against.
LGBM_PARAMS: dict[str, object] = {
    "num_leaves": 15,
    "max_depth": 4,
    "min_child_samples": 100,
    "learning_rate": 0.03,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
}

#: Patience for early stopping on fold loss (DESIGN §7.5).
EARLY_STOPPING_ROUNDS = 50

#: Ceiling; early stopping is expected to bite well before this.
MAX_ROUNDS = 2000


def head_name(family: str, horizon: int, tau: float) -> str:
    """`terminal_h5_q50`. Stable, sortable, and readable in a log line."""
    return f"{family}_h{horizon}_q{int(round(tau * 100)):02d}"


def label_for(family: str, horizon: int) -> str:
    """The `events` column backing a head.

    `terminal` -> `fwd_ret_{h}d` ($R_h$), `peak` -> `peak_ret_{h}d` ($M_h$).
    """
    if family == "terminal":
        return f"fwd_ret_{horizon}d"
    if family == "peak":
        return f"peak_ret_{horizon}d"
    raise ValueError(f"unknown family {family!r}; expected 'terminal' or 'peak'")


def all_heads() -> tuple[tuple[str, int, float], ...]:
    """The twenty, in a fixed order so two runs are comparable line by line."""
    return tuple(
        (family, horizon, tau)
        for family in ("terminal", "peak")
        for horizon in HORIZONS
        for tau in TAUS
    )


@dataclass(frozen=True)
class HeadResult:
    """One head, one fold.

    `baseline_loss` sits beside `model_loss` rather than being computed
    later, because the comparison is the point and separating them invites
    reporting the first without the second.
    """

    head: str
    fold_validate_year: int
    n_train: int
    n_validate: int
    model_loss: float
    baseline_loss: float
    best_iteration: int

    @property
    def beats_baseline(self) -> bool:
        return self.model_loss < self.baseline_loss

    @property
    def improvement(self) -> float:
        """Fraction of the baseline loss removed. Negative means worse."""
        if self.baseline_loss == 0:
            return 0.0
        return (self.baseline_loss - self.model_loss) / self.baseline_loss


@dataclass
class FitReport:
    results: list[HeadResult] = field(default_factory=list)

    def by_head(self) -> dict[str, list[HeadResult]]:
        out: dict[str, list[HeadResult]] = {}
        for r in self.results:
            out.setdefault(r.head, []).append(r)
        return out

    def heads_beating_baseline(self) -> dict[str, bool]:
        """Per head, whether it beats the baseline on **every** fold.

        Deliberately not "on average". A head that wins four folds and loses
        three has not shown anything a fold ordering could not produce, and
        averaging is how that gets reported as a win.
        """
        return {head: all(r.beats_baseline for r in rs) for head, rs in self.by_head().items()}


def _fold_masks(
    frame: pd.DataFrame,
    fold: core_folds.Fold,
    calendar: Sequence[date],
    horizon_days: int,
    embargo_days: int = core_folds.DEFAULT_EMBARGO_DAYS,
) -> tuple[np.ndarray, np.ndarray]:
    """Purged training mask and embargoed validation mask for one fold.

    The boundary is 1 January of the validation year. Purge reaches back
    from it into train; embargo reaches forward into validate.
    """
    signal_dates = pd.to_datetime(frame["signal_date"]).dt.date
    boundary = date(fold.validate, 1, 1)

    in_train_years = signal_dates.map(lambda d: fold.train_start <= d.year <= fold.train_end)
    in_validate_year = signal_dates.map(lambda d: d.year == fold.validate)

    kept_train = np.asarray(
        core_folds.purge(list(signal_dates), boundary=boundary, horizon_days=horizon_days)
    )
    kept_validate = np.asarray(
        core_folds.embargo(
            list(signal_dates),
            boundary=boundary,
            calendar=calendar,
            embargo_days=embargo_days,
        )
    )
    return (
        np.asarray(in_train_years) & kept_train,
        np.asarray(in_validate_year) & kept_validate,
    )


def fit_head(
    frame: pd.DataFrame,
    family: str,
    horizon: int,
    tau: float,
    calendar: Sequence[date],
    params: dict[str, object] | None = None,
) -> list[HeadResult]:
    """Fit one head across every walk-forward fold.

    Returns one `HeadResult` per fold. No model object is returned: this
    function answers "is this head worth having", and handing back a fitted
    booster invites someone serving it before the promotion gate has run.
    """
    import lightgbm as lgb

    label = label_for(family, horizon)
    name = head_name(family, horizon, tau)
    merged = {**LGBM_PARAMS, **(params or {}), "objective": "quantile", "alpha": tau}

    years = pd.to_datetime(frame["signal_date"]).dt.year
    ladder = core_folds.walk_forward_folds(int(years.min()), int(years.max()))

    x_all = frame[list(feat.FEATURE_COLS)].copy()
    for col in feat.CATEGORICAL_COLS:
        x_all[col] = x_all[col].astype("category")
    y_all = pd.to_numeric(frame[label], errors="coerce").to_numpy(float)
    w_all = np.asarray(core_folds.cluster_weights(list(frame["cluster_id"])))

    out: list[HeadResult] = []
    for fold in ladder:
        tr_mask, va_mask = _fold_masks(frame, fold, calendar, horizon_days=horizon)
        # A label-less row teaches nothing and cannot be scored.
        tr_mask &= ~np.isnan(y_all)
        va_mask &= ~np.isnan(y_all)
        if tr_mask.sum() == 0 or va_mask.sum() == 0:
            continue

        train_set = lgb.Dataset(
            x_all[tr_mask], label=y_all[tr_mask], weight=w_all[tr_mask], free_raw_data=False
        )
        valid_set = lgb.Dataset(
            x_all[va_mask], label=y_all[va_mask], weight=w_all[va_mask], reference=train_set
        )
        booster = lgb.train(
            merged,
            train_set,
            num_boost_round=MAX_ROUNDS,
            valid_sets=[valid_set],
            callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
        )

        pred = booster.predict(x_all[va_mask], num_iteration=booster.best_iteration)
        model_loss = pinball.pinball_loss(pred, y_all[va_mask], tau, weights=w_all[va_mask])

        # Baseline fitted on this fold's *training* labels only. Fitting it
        # on the validation labels would make it an oracle and a harder bar
        # than the honest one, so check 5 would fire when it should not.
        constant = pinball.unconditional_quantile(list(y_all[tr_mask]), tau)
        baseline_loss = pinball.pinball_loss(
            [constant] * int(va_mask.sum()), y_all[va_mask], tau, weights=w_all[va_mask]
        )

        out.append(
            HeadResult(
                head=name,
                fold_validate_year=fold.validate,
                n_train=int(tr_mask.sum()),
                n_validate=int(va_mask.sum()),
                model_loss=model_loss,
                baseline_loss=baseline_loss,
                best_iteration=int(booster.best_iteration or 0),
            )
        )
    return out


def sort_quantiles(predictions: dict[float, np.ndarray]) -> dict[float, np.ndarray]:
    """Repair quantile crossing by sorting across τ (DESIGN §7.4).

    Independent heads carry no monotonicity constraint, so a fitted
    $\\hat{Q}_{0.25}$ can exceed $\\hat{Q}_{0.50}$ on some feature vectors.
    Sorting is the standard repair and is applied **at prediction time,
    never to the training labels** — reordering labels would be fabricating
    outcomes.
    """
    taus = sorted(predictions)
    stacked = np.vstack([predictions[t] for t in taus])
    stacked.sort(axis=0)
    return {t: stacked[i] for i, t in enumerate(taus)}
