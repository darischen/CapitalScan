import dataclasses
import math

import numpy as np
import pandas as pd

from capitalscan.core.config import Config, VarianceParams
from capitalscan.core.variance import (
    cluster_robust_variance,
    inflate,
    inflation_factor,
    month_block,
    one_sided_p_from_edge,
    robust_n_eff,
    wald_interval,
)


def _ref_clustered(r: np.ndarray, codes: np.ndarray) -> float:
    _, inv = np.unique(codes, return_inverse=True)
    s = np.bincount(inv, weights=r)
    return float(s @ s)


def _ref_cgm_se(d: np.ndarray, block: np.ndarray, clus: np.ndarray) -> float:
    n = len(d)
    r = d - d.mean()
    inter = block.astype(np.int64) * (int(clus.max()) + 1) + clus
    v1, v2, v12 = (
        _ref_clustered(r, block),
        _ref_clustered(r, clus),
        _ref_clustered(r, inter),
    )
    v = v1 + v2 - v12
    if v <= 0:
        v = max(v1, v2)
    return math.sqrt(v) / n


def test_iid_reduces_to_textbook() -> None:
    rng = np.random.default_rng(1)
    y = (rng.random(10_000) < 0.3).astype(float)
    idx = np.arange(10_000)
    v, _, _ = cluster_robust_variance(y, idx, idx)
    assert abs(v - 0.21 / 10_000) / (0.21 / 10_000) < 0.05


def test_perfectly_clustered_month() -> None:
    rng = np.random.default_rng(2)
    months = np.repeat(np.arange(50), 100)
    y = np.repeat((rng.random(50) < 0.5).astype(float), 100)
    cluster = np.arange(5000)
    v, _, _ = cluster_robust_variance(y, months, cluster)
    n_eff = robust_n_eff(float(y.mean()), v)
    assert n_eff is not None
    assert abs(n_eff - 50) / 50 < 0.10


def test_negative_two_way_falls_back() -> None:
    y = np.array([1.0, 0.0, 0.0, 1.0])
    block = np.array([0, 0, 1, 1])
    cluster = np.array([0, 1, 0, 1])
    r = y - y.mean()
    v_b = _ref_clustered(r, block)
    v_c = _ref_clustered(r, cluster)
    assert v_b + v_c - float(r @ r) <= 0
    v, vb, vc = cluster_robust_variance(y, block, cluster)
    assert v == max(vb, vc)


def test_zero_variance_gives_none() -> None:
    y = np.zeros(10)
    v, _, _ = cluster_robust_variance(y, np.arange(10), np.arange(10))
    assert robust_n_eff(0.0, v) is None
    assert robust_n_eff(0.0, 0.0) is None
    assert robust_n_eff(0.5, 0.0) is None


def test_single_month_single_cluster_finite() -> None:
    y = np.array([1.0, 0.0, 1.0, 1.0, 0.0])
    z = np.zeros(5, dtype=np.int64)
    v, vb, vc = cluster_robust_variance(y, z, z)
    assert v >= 0 and math.isfinite(v)
    assert vb >= 0 and vc >= 0


def test_wald_clips() -> None:
    lo, hi = wald_interval(0.01, 0.01, 1.96)
    assert lo == 0.0
    assert hi <= 1.0
    assert wald_interval(0.99, 0.01, 1.96)[1] == 1.0


def test_wald_reads_the_z_it_is_given() -> None:
    """No confidence level lives in `core.variance` (invariant 9)."""
    lo, hi = wald_interval(0.5, 0.0004, 2.0)
    assert (lo, hi) == (0.5 - 0.04, 0.5 + 0.04)


def test_ci_level_defaults_to_95() -> None:
    assert VarianceParams().ci_level == 0.95


def test_inflate() -> None:
    assert inflate(2.0, 1.5) == 2.0 * 2.25


def test_one_sided_p_from_edge() -> None:
    from scipy.stats import norm

    assert one_sided_p_from_edge(0.0, 0.01) == 0.5
    assert one_sided_p_from_edge(0.02, 0.0001) == norm.sf(2.0)
    assert one_sided_p_from_edge(-0.02, 0.0001) == norm.sf(-2.0)
    assert one_sided_p_from_edge(0.02, 0.0) is None
    assert one_sided_p_from_edge(0.02, -1.0) is None
    assert one_sided_p_from_edge(0.02, float("nan")) is None


def test_month_block() -> None:
    s = pd.Series(pd.to_datetime(["2020-01-31", "2020-02-01", "2021-12-15"]))
    out = month_block(s)
    assert out.dtype == np.int64
    assert out.tolist() == [2020 * 12, 2020 * 12 + 1, 2021 * 12 + 11]


def _edges(target: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(3)
    groups = np.repeat(np.arange(4), 200)
    edges = np.empty(800)
    for g in range(4):
        e = rng.standard_normal(200)
        edges[groups == g] = (e - e.mean()) / e.std(ddof=1) * target
    return edges, np.ones(800), groups


def test_inflation_floor() -> None:
    edges, ses, groups = _edges(0.8)
    assert inflation_factor(edges, ses, groups, floor=1.0) == 1.0
    edges, ses, groups = _edges(1.2)
    assert abs(inflation_factor(edges, ses, groups, floor=1.0) - 1.2) < 1e-9


def test_matches_reference_script() -> None:
    rng = np.random.default_rng(4)
    n = 2000
    block = rng.integers(0, 60, n)
    clus = rng.integers(0, 15, n)
    y = (rng.random(n) < 0.4).astype(float) + 0.3 * (block % 3 == 0)
    v, _, _ = cluster_robust_variance(y, block, clus)
    assert abs(v - _ref_cgm_se(y, block, clus) ** 2) < 1e-12


def test_variance_params_not_in_config() -> None:
    types = [f.type for f in dataclasses.fields(Config)]
    assert VarianceParams not in types
    assert "VarianceParams" not in [str(t) for t in types]
