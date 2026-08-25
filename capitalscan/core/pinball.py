"""Pinball loss and the unconditional baseline (DESIGN §7.6, ADR 113).

Pure computation. No IO, no clock.

**This module is what ADR 113's kill criterion is measured with**, so its
failure mode is worth stating before the code. ADR 067's four promotion
checks all compare a retrained model against an incumbent, and on a first
model there is no incumbent — so a model passes all four by default while
being worse than a constant. Check 5 closes that: out-of-sample pinball
loss must beat the unconditional baseline, fit with no features.

ADR 113 then fixes the consequence in advance: fail check 5 on validation
at every horizon and the two-indicator hypothesis is retired at the model
layer as well as the cell layer, and Phase 6 closes with that recorded.

If anything here is wrong in the optimistic direction, the criterion cannot
fire and the project loses its ability to say no.

**Why pinball and not squared error.** The product is a distribution, not a
point. Squared error is minimised by the conditional mean, so a model fitted
on it produces one number per event and no fan. Pinball at $\\tau$ is
minimised by the conditional $\\tau$-quantile, which is exactly the
twenty heads ADR 113 asks for.

    L_tau(y, q) = tau * (y - q)        when y >= q   (under-predicted)
                  (1 - tau) * (q - y)  when y <  q   (over-predicted)

The asymmetry *is* the mechanism. At $\\tau = 0.95$, predicting too low
costs nineteen times as much as predicting too high, which is what pushes
the fitted value out to the upper tail.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def _clean(
    predictions: Sequence[float],
    targets: Sequence[float],
    weights: Sequence[float] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Align, drop unresolved labels, and refuse an empty comparison.

    **NaN targets are excluded, never imputed** (invariant 4). Scoring an
    unresolved label as zero would make a model look better the more
    unresolved events it was shown, which is backwards in the most
    dangerous possible way.
    """
    pred = np.asarray(predictions, dtype=float)
    targ = np.asarray(targets, dtype=float)
    if pred.shape != targ.shape:
        raise ValueError(f"predictions {pred.shape} and targets {targ.shape} differ in length")

    wts = np.ones_like(targ) if weights is None else np.asarray(weights, dtype=float)
    if wts.shape != targ.shape:
        raise ValueError(f"weights {wts.shape} and targets {targ.shape} differ in length")

    usable = ~np.isnan(targ) & ~np.isnan(pred)
    if not usable.any():
        raise ValueError(
            "no comparable prediction/target pairs: every row has a NaN. "
            "Returning 0.0 here would read as a perfect model and pass "
            "ADR 113's check 5 on an empty comparison."
        )
    return pred[usable], targ[usable], wts[usable]


def pinball_loss(
    predictions: Sequence[float],
    targets: Sequence[float],
    tau: float,
    weights: Sequence[float] | None = None,
) -> float:
    """Weighted mean pinball loss at `tau`.

    **Mean, not sum.** Train and validate splits differ in size and check 5
    compares losses across them; a sum would make the larger split look
    worse for being larger.

    `weights` carries DESIGN §7.5's `1/|cluster|`. A loss ignoring them
    scores a four-event cluster four times, which is precisely the
    correlation the weighting exists to undo.
    """
    if not 0.0 < tau < 1.0:
        raise ValueError(
            f"tau must lie strictly inside (0, 1); got {tau}. At 0 or 1 the "
            "loss is one-sided and is minimised by predicting an infinity."
        )
    pred, targ, wts = _clean(predictions, targets, weights)

    error = targ - pred
    # `np.where` rather than a branch: the two arms are the same expression
    # with tau and (tau - 1), and writing them apart invites one being
    # updated without the other.
    losses = np.where(error >= 0, tau * error, (tau - 1.0) * error)

    total = wts.sum()
    if total == 0:
        raise ValueError("weights sum to zero, so the loss is undefined")
    return float((losses * wts).sum() / total)


def unconditional_quantile(labels: Sequence[float], tau: float) -> float:
    """The empirical `tau`-quantile of `labels`. The no-feature baseline.

    This is the loss-minimising constant, which is what makes it the right
    thing for check 5 to compare against: a model that cannot beat it has
    extracted nothing from twenty-one features that a single number does
    not already carry.

    **Fitted on train, scored on validate.** The signature takes labels and
    a tau and returns a scalar, so the caller has to hold that direction
    explicitly. A baseline fitted on the split it is scored on is an oracle,
    not a baseline — and it would be *harder* to beat, so check 5 would fire
    when it should not.
    """
    if not 0.0 < tau < 1.0:
        raise ValueError(f"tau must lie strictly inside (0, 1); got {tau}")
    values = np.asarray(labels, dtype=float)
    values = values[~np.isnan(values)]
    if values.size == 0:
        raise ValueError("cannot fit a baseline on an empty label set")
    return float(np.quantile(values, tau))
