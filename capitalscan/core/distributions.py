"""Discrete predictive distributions: grids, CDF inversion, CRPS.

Pure computation. No IO, no clock, no torch (invariant 1).

**Why this module exists next to `core/pinball.py`.** ADR 113's twenty
heads predict five points of a distribution. A model that predicts the
*whole* distribution instead -- a probability mass function over return
bins -- can produce those five points and several things they cannot, and
the conversions have to live somewhere both `research/` and `jobs/` can
reach without either owning them.

Two quantities, and the difference between them is the reason for the file:

    pinball  scores five chosen points of the fan
    CRPS     scores the entire predicted CDF against the outcome

        CRPS = integral (F(z) - 1[y <= z])^2 dz

Both are strictly proper. Pinball at tau is minimised by the conditional
tau-quantile; CRPS is minimised by the true conditional distribution. A
model can win one and lose the other, which is exactly why both are
reported rather than one standing in for the other.

**What a pmf buys that a fan does not.** `handlers.types.Prediction`
already asks for `p_touch_2/3/5/10` and `p_adverse_3/5` alongside
`q05..q95`. A fan gives values at fixed probabilities; those fields need
probabilities at fixed values, which is the same object read the other way
round. `exceedance` reads them straight off a predicted CDF. The
twenty-head architecture cannot produce them at all without fitting more
heads.
"""

from __future__ import annotations

from typing import Sequence, Union

import numpy as np

Floats = Union[Sequence[float], np.ndarray]

#: Span of a CRPS integration grid, as quantiles of the training labels.
#: Wide enough that tau=0.05 and tau=0.95 sit well inside it, narrow enough
#: that the integral is not dominated by two enormous outer bins.
DEFAULT_CRPS_SPAN = (0.005, 0.995)

#: Outer anchors for a quantile grid, used when interpolating past the
#: first and last edge. Train's 0.1th and 99.9th percentile rather than the
#: min and max: one outlier would otherwise set the scale for every
#: tau=0.05 read.
DEFAULT_OUTER = (0.001, 0.999)


def crps_grid(
    labels: Floats,
    n_bins: int,
    span: tuple[float, float] = DEFAULT_CRPS_SPAN,
) -> np.ndarray:
    """Equal-**width** bin edges for integrating CRPS. Returns `n_bins + 1`.

    **Equal width, not equal mass, and this was measured rather than
    assumed.** Session 24 first built this grid from label quantiles,
    reusing the spacing that is right for *reading* a fan. It is wrong for
    *integrating* one. Returns are concentrated, so equal-mass edges made
    the interior bins about 0.003 wide and the two outer bins about 0.18 --
    **71.5% of the total dz in two bins.** CRPS became almost entirely a
    tail-fitting problem, and the symptom was unmistakable: the model
    reached its best score after a single pass and never improved, having
    matched the marginal tails immediately with no gradient left pointing
    anywhere else.

    Equal width makes each bin contribute in proportion to the range it
    covers, which is what the integral means.

    **Truncated at `span`, deliberately.** Mass outside contributes nothing.
    Every model compared must use the same grid, so the truncation is a
    shared constant; widening it hands the loss back to the tails for the
    sake of a region holding one percent of events.
    """
    if n_bins < 2:
        raise ValueError(f"need at least 2 bins; got {n_bins}")
    lo_q, hi_q = span
    if not 0.0 <= lo_q < hi_q <= 1.0:
        raise ValueError(f"span must be an increasing pair inside [0, 1]; got {span}")

    values = np.asarray(labels, dtype=float)
    values = values[~np.isnan(values)]
    if values.size == 0:
        raise ValueError("cannot build a grid from an empty label set")

    lo, hi = np.quantile(values, (lo_q, hi_q))
    if not hi > lo:
        raise ValueError(
            f"degenerate span [{lo}, {hi}]: the labels are constant across "
            "the requested quantiles, so no grid has positive width"
        )
    return np.linspace(float(lo), float(hi), n_bins + 1)


def quantile_grid(
    labels: Floats,
    edge_quantiles: Sequence[float],
    outer: tuple[float, float] = DEFAULT_OUTER,
) -> np.ndarray:
    """Bin edges placed at chosen label quantiles, plus outer anchors.

    **For a model whose class count is capped**, where the bins cannot be
    spent uniformly and should sit where the answers are read. Put the
    reported tau among `edge_quantiles` and the predicted CDF is known
    exactly at every point the gate looks at, so interpolation error never
    lands on a reported number.
    """
    values = np.asarray(labels, dtype=float)
    values = values[~np.isnan(values)]
    if values.size == 0:
        raise ValueError("cannot build a grid from an empty label set")
    edges = np.quantile(values, list(edge_quantiles))
    lo, hi = np.quantile(values, outer)
    return np.concatenate([[lo], edges, [hi]])


def quantiles_from_pmf(
    pmf: np.ndarray,
    grid: np.ndarray,
    taus: Sequence[float],
) -> dict[float, np.ndarray]:
    """Invert each row's discrete CDF at every tau. Linear inside a bin.

    `pmf` is `(n, len(grid) - 1)`. The CDF is known exactly at the interior
    grid points; a tau landing between two of them is interpolated in
    *value* space, which is the piecewise-uniform assumption and the only
    one available without reintroducing a parametric family.

    **Monotone in tau by construction**, so the caller has nothing to sort:
    the CDF is nondecreasing and `np.interp` against a nondecreasing table
    is nondecreasing. `train.sort_quantiles` exists to repair independently
    fitted heads that cross; a fan read off one CDF cannot cross, and
    sorting it would mask a bug rather than fix one.
    """
    pmf = np.asarray(pmf, dtype=float)
    grid = np.asarray(grid, dtype=float)
    if pmf.ndim != 2:
        raise ValueError(f"pmf must be 2-D (rows, bins); got shape {pmf.shape}")
    if pmf.shape[1] != len(grid) - 1:
        raise ValueError(
            f"pmf has {pmf.shape[1]} bins but the grid has {len(grid)} edges, "
            f"which describes {len(grid) - 1} bins"
        )
    for tau in taus:
        if not 0.0 < tau < 1.0:
            raise ValueError(f"tau must lie strictly inside (0, 1); got {tau}")

    cdf = np.clip(np.cumsum(pmf, axis=1), 0.0, 1.0)
    # Force the last cumulative value to exactly 1 so a tau above the
    # numerical total cannot fall off the end of the interpolation table.
    cdf[:, -1] = 1.0
    table = np.concatenate([np.zeros((len(pmf), 1)), cdf], axis=1)

    return {
        float(tau): np.asarray(
            [np.interp(tau, table[i], grid) for i in range(len(pmf))], dtype=float
        )
        for tau in taus
    }


def exceedance(pmf: np.ndarray, grid: np.ndarray, threshold: float) -> np.ndarray:
    """`P(Y > threshold)` per row, from the predicted CDF.

    **This is what fills `Prediction.p_touch_*` and `p_adverse_*`.** Those
    fields want a probability at a fixed *value*; a quantile fan gives
    values at fixed probabilities. Reading them off a pmf is one
    interpolation, and no additional head has to be fitted.

    Interpolated within the containing bin, consistent with
    `quantiles_from_pmf`, so the two are inverses of one another rather
    than two slightly different pictures of the same distribution.
    """
    pmf = np.asarray(pmf, dtype=float)
    grid = np.asarray(grid, dtype=float)
    if pmf.shape[1] != len(grid) - 1:
        raise ValueError(f"pmf has {pmf.shape[1]} bins but the grid describes {len(grid) - 1}")
    cdf = np.clip(np.cumsum(pmf, axis=1), 0.0, 1.0)
    cdf[:, -1] = 1.0
    table = np.concatenate([np.zeros((len(pmf), 1)), cdf], axis=1)
    below = np.asarray([np.interp(threshold, grid, table[i]) for i in range(len(pmf))], dtype=float)
    return 1.0 - below


def crps(
    pmf: np.ndarray,
    targets: Floats,
    grid: np.ndarray,
    weights: Floats | None = None,
) -> float:
    """Weighted mean CRPS of a discretised predictive CDF.

    **Both terms are evaluated at each bin's right edge**, which makes this
    a right-endpoint Riemann sum of the integral. `grid` carries `K + 1`
    points for `K` bins: the cumulative masses, the right edges and the bin
    widths then all have length `K` and line up. Slicing the CDF instead
    silently misaligns them by one, which in Session 24 raised a shape
    error rather than a wrong number -- the lucky version of that mistake.

    **NaN targets are excluded, never imputed** (invariant 4), for the same
    reason `pinball._clean` excludes them: scoring an unresolved label as
    zero would make a model look better the more unresolved events it saw.

    Mean rather than sum, so splits of different sizes stay comparable.
    """
    pmf = np.asarray(pmf, dtype=float)
    grid = np.asarray(grid, dtype=float)
    targ = np.asarray(targets, dtype=float)
    if pmf.shape[1] != len(grid) - 1:
        raise ValueError(f"pmf has {pmf.shape[1]} bins but the grid describes {len(grid) - 1}")
    if len(pmf) != len(targ):
        raise ValueError(f"pmf has {len(pmf)} rows and targets {len(targ)}")

    wts = np.ones_like(targ) if weights is None else np.asarray(weights, dtype=float)
    if wts.shape != targ.shape:
        raise ValueError(f"weights {wts.shape} and targets {targ.shape} differ in length")

    usable = ~np.isnan(targ)
    if not usable.any():
        raise ValueError(
            "no comparable prediction/target pairs: every target is NaN. "
            "Returning 0.0 here would read as a perfect forecast."
        )

    cdf = np.cumsum(pmf[usable], axis=1)
    step = (grid[1:] - grid[:-1])[None, :]
    indicator = (targ[usable][:, None] <= grid[None, 1:]).astype(float)
    per_row = (((cdf - indicator) ** 2) * step).sum(axis=1)

    w = wts[usable]
    total = w.sum()
    if total == 0:
        raise ValueError("weights sum to zero, so the score is undefined")
    return float((per_row * w).sum() / total)


def unconditional_pmf(labels: Floats, grid: np.ndarray) -> np.ndarray:
    """Train's own histogram on `grid`: the featureless distribution.

    The CRPS analogue of `pinball.unconditional_quantile`, and the baseline
    a distributional model has to beat before its fan means anything. One
    row, which the caller tiles -- returning `n` identical copies would
    allocate a matrix to say a vector.
    """
    values = np.asarray(labels, dtype=float)
    values = values[~np.isnan(values)]
    if values.size == 0:
        raise ValueError("cannot fit a baseline on an empty label set")
    counts, _ = np.histogram(values, bins=np.asarray(grid, dtype=float))
    total = counts.sum()
    if total == 0:
        raise ValueError(
            "every label fell outside the grid, so the baseline distribution "
            "is empty. Build the grid from the same labels."
        )
    return np.asarray(counts / total, dtype=float)
