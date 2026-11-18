"""Cluster-robust variance of a cell's hit rate (ADR 209). Pure, no IO.

Two-way clustering by calendar block and by ticker cluster, following
Cameron-Gelbach-Miller: `v = v_block + v_cluster - v_intersection`.
"""

import math

import numpy as np
import pandas as pd
from scipy.stats import norm


def _group_sum_sq(r: np.ndarray, codes: np.ndarray) -> float:
    _, inv = np.unique(codes, return_inverse=True)
    s = np.bincount(inv.ravel(), weights=r)
    return float(s @ s)


def cluster_robust_variance(
    y: np.ndarray, block: np.ndarray, cluster: np.ndarray
) -> tuple[float, float, float]:
    """Return `(v, v_block, v_cluster)`, the variance of `mean(y)`."""
    n = len(y)
    r = np.asarray(y, dtype=np.float64) - float(np.mean(y))
    block = np.asarray(block).astype(np.int64)
    cluster = np.asarray(cluster).astype(np.int64)
    inter = block * (int(cluster.max()) + 1) + cluster
    n2 = float(n) * float(n)
    v_block = _group_sum_sq(r, block) / n2
    v_cluster = _group_sum_sq(r, cluster) / n2
    v_inter = _group_sum_sq(r, inter) / n2
    v = v_block + v_cluster - v_inter
    if v <= 0:
        v = max(v_block, v_cluster)
    return v, v_block, v_cluster


def month_block(dates: pd.Series) -> np.ndarray:
    """Calendar month index, `year * 12 + month - 1`, as int64."""
    d = pd.to_datetime(dates)
    year = d.dt.year.to_numpy(dtype=np.int64)
    month = d.dt.month.to_numpy(dtype=np.int64)
    return year * 12 + month - 1


def robust_n_eff(p: float, v: float) -> float | None:
    """`p (1 - p) / v`, or None when it is undefined."""
    if v <= 0 or p <= 0.0 or p >= 1.0:
        return None
    return p * (1.0 - p) / v


def inflate(v: float, factor: float) -> float:
    return v * factor**2


def one_sided_p_from_edge(edge: float, v: float) -> float | None:
    """`P(Z >= edge / sqrt(v))`, one-sided; None when `v <= 0`.

    `v` is the variance of the mean edge itself, so the test reads the
    same variance the placebo calibrated (ADR 209).
    """
    if not v > 0:
        return None
    return float(norm.sf(edge / math.sqrt(v)))


def wald_interval(p: float, v: float, z: float) -> tuple[float, float]:
    """`p +/- z sqrt(v)`, clipped to [0, 1].

    `z` has no default: the caller derives it from `VarianceParams.ci_level`
    (invariant 9), so no confidence level is written down here.
    """
    half = z * math.sqrt(max(v, 0.0))
    return max(0.0, p - half), min(1.0, p + half)


def inflation_factor(
    placebo_edges: np.ndarray,
    placebo_ses: np.ndarray,
    groups: np.ndarray,
    floor: float,
) -> float:
    """Per group `SD(edges) / median(ses)`; the median over groups, floored."""
    ratios: list[float] = []
    for g in np.unique(groups):
        m = groups == g
        se = float(np.median(placebo_ses[m]))
        if se > 0:
            ratios.append(float(np.std(placebo_edges[m], ddof=1)) / se)
    if not ratios:
        return floor
    return max(floor, float(np.median(ratios)))
