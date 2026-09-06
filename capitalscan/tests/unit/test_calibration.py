"""ADR 174: the interval on a shipped probability is empirical, not modelled.

The thing under test is the piece of ADR 174 that is easy to get subtly
wrong. A model emits a point probability. Invariant 8 demands an interval.
The interval must come from *measured* reliability -- how predictions in
this band actually resolved -- not from the ensemble's seed spread, which
measures seed choice and would be confidently wrong.

Two properties carry the load here:

    calibration    a prediction and its interval must be coherent. If the
                   point estimate can land outside its own interval, the
                   display is nonsense, and that is exactly what happens if
                   you keep the raw p and take the interval from the
                   realised rate.
    n_eff          the interval is sized on Kish effective n, never on the
                   row count. Clustered events make the raw count a lie,
                   and a too-narrow interval is the specific failure
                   invariant 8 exists to prevent.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from capitalscan.core import calibration as calib
from capitalscan.tests.unit._probe import code_of


class TestKishEffectiveN:
    def test_equal_weights_give_back_the_count(self) -> None:
        assert calib.kish_n_eff([1.0] * 40) == pytest.approx(40.0)
        assert calib.kish_n_eff([0.25] * 40) == pytest.approx(40.0)

    def test_unequal_weights_are_always_smaller_than_the_count(self) -> None:
        w = [1.0, 1.0, 1.0, 10.0]
        assert calib.kish_n_eff(w) < len(w)

    def test_one_dominant_weight_collapses_toward_one(self) -> None:
        assert calib.kish_n_eff([1e6] + [1.0] * 99) == pytest.approx(1.0, abs=0.01)

    def test_empty_and_zero_weight_are_zero_not_a_zero_division(self) -> None:
        assert calib.kish_n_eff([]) == 0.0
        assert calib.kish_n_eff([0.0, 0.0]) == 0.0


class TestBuildingTheTable:
    @staticmethod
    def _skilful(n: int = 4000, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
        """Predictions that are ranked well but systematically overconfident."""
        rng = np.random.default_rng(seed)
        p = rng.uniform(0.05, 0.95, n)
        # realised rate is a shrunk version of p, so the model is skilful
        # and miscalibrated at once -- the case calibration must fix.
        y = (rng.uniform(size=n) < 0.25 + 0.5 * p).astype(float)
        return p, y

    def test_bucket_edges_are_equal_mass_and_beat_equal_width(self) -> None:
        """The ADR 170 failure, in miniature.

        Predicted probabilities pile up near the base rate, so equal-WIDTH
        bins leave the tails at a handful of rows and every interval there
        is useless. Measured against the same data, equal-mass must spread
        the sample far more evenly. Exact ties are the one thing no scheme
        can split, so the fixture is continuous and skewed rather than
        clumped on a single value.
        """
        rng = np.random.default_rng(19)
        p = rng.beta(6.0, 2.0, 6000)  # heaped high, thin left tail
        y = (rng.uniform(size=6000) < p).astype(float)
        t = calib.build_reliability("p_touch_3", p, y, n_buckets=10)

        # Measured from the EDGES, not from `Bucket.n`. After isotonic
        # pooling `n` reports the merged block's count and is duplicated
        # across the buckets in it, so it no longer answers "how much mass
        # landed in this band" -- which is the property under test.
        mass_edges = [-np.inf] + [b.hi for b in t.buckets[:-1]] + [np.inf]
        mass = [int(v) for v in np.histogram(p, bins=mass_edges)[0]]

        width_edges = np.linspace(p.min(), p.max(), 11)
        width = [int(v) for v in np.histogram(p, bins=width_edges)[0]]

        assert max(mass) / min(mass) < 1.5, f"not equal-mass: {mass}"
        skew = max(width) / max(min(width), 1)
        assert skew > 10, "fixture is not skewed enough to prove anything"
        assert max(mass) / min(mass) < skew

    def test_every_bucket_carries_an_interval_that_brackets_its_rate(self) -> None:
        p, y = self._skilful()
        t = calib.build_reliability("p_touch_3", p, y, n_buckets=10)
        for b in t.buckets:
            assert b.ci_low <= b.p_hat <= b.ci_high
            assert 0.0 <= b.ci_low and b.ci_high <= 1.0

    def test_the_interval_widens_when_events_are_clustered(self) -> None:
        """Same rows, correlated -- the interval must get wider, not stay."""
        p, y = self._skilful()
        flat = calib.build_reliability("p_touch_3", p, y, n_buckets=5)
        w = np.tile([1.0, 0.2, 0.2, 0.2, 0.2], len(p) // 5 + 1)[: len(p)]
        clustered = calib.build_reliability("p_touch_3", p, y, weights=w, n_buckets=5)
        for a, b in zip(flat.buckets, clustered.buckets):
            assert b.n_eff < a.n_eff
            assert (b.ci_high - b.ci_low) > (a.ci_high - a.ci_low)

    def test_n_eff_is_used_for_the_interval_and_not_the_row_count(self) -> None:
        """A signature probe: the raw count must not reach `wilson_ci`."""
        src = code_of(calib.build_reliability)
        assert "wilson_ci" in src
        assert "n_eff" in src

    def test_it_refuses_a_target_with_no_variation(self) -> None:
        with pytest.raises(ValueError, match="both outcomes"):
            calib.build_reliability("p_touch_3", np.linspace(0, 1, 50), np.zeros(50))

    def test_it_refuses_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError, match="length"):
            calib.build_reliability("p_touch_3", np.zeros(5), np.zeros(4))

    def test_it_refuses_probabilities_outside_the_unit_interval(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            calib.build_reliability("p_touch_3", np.array([0.5, 1.7]), np.array([0.0, 1.0]))


class TestCalibrateAndLookup:
    @staticmethod
    def _table(seed: int = 3) -> calib.ReliabilityTable:
        rng = np.random.default_rng(seed)
        p = rng.uniform(0.05, 0.95, 6000)
        y = (rng.uniform(size=6000) < 0.25 + 0.5 * p).astype(float)
        return calib.build_reliability("p_touch_3", p, y, n_buckets=10)

    def test_the_calibrated_point_lies_inside_its_own_interval(self) -> None:
        """The coherence property. Shipping raw p with an empirical CI breaks it."""
        t = self._table()
        for raw in np.linspace(0.0, 1.0, 41):
            b = t.lookup(raw)
            assert b.ci_low <= t.calibrate(raw) <= b.ci_high, f"incoherent at {raw}"

    def test_calibration_pulls_an_overconfident_model_toward_the_truth(self) -> None:
        t = self._table()
        # true rate at p=0.9 is 0.25 + 0.45 = 0.70, so 0.9 must come down
        assert t.calibrate(0.9) < 0.9
        assert t.calibrate(0.9) == pytest.approx(0.70, abs=0.06)
        # and at p=0.1 the true rate is 0.30, so it must come up
        assert t.calibrate(0.1) > 0.1

    def test_it_is_monotone_non_decreasing(self) -> None:
        t = self._table()
        vals = [t.calibrate(x) for x in np.linspace(0.0, 1.0, 200)]
        assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:]))

    def test_it_never_leaves_the_unit_interval(self) -> None:
        t = self._table()
        for raw in (-5.0, 0.0, 0.5, 1.0, 5.0, math.inf):
            assert 0.0 <= t.calibrate(raw) <= 1.0

    def test_a_prediction_beyond_the_observed_range_clamps_to_an_end_bucket(self) -> None:
        t = self._table()
        assert t.lookup(-1.0) is t.buckets[0]
        assert t.lookup(2.0) is t.buckets[-1]

    def test_nan_gets_the_widest_bucket_rather_than_a_confident_answer(self) -> None:
        t = self._table()
        b = t.lookup(float("nan"))
        assert b.ci_high - b.ci_low == max(x.ci_high - x.ci_low for x in t.buckets)
        assert math.isnan(t.calibrate(float("nan")))


class TestItRoundTrips:
    def test_a_table_survives_json_unchanged(self) -> None:
        """It is persisted per prediction run, so this is load-bearing."""
        rng = np.random.default_rng(11)
        p = rng.uniform(0.05, 0.95, 3000)
        y = (rng.uniform(size=3000) < p).astype(float)
        t = calib.build_reliability("p_touch_5", p, y, n_buckets=8)
        back = calib.ReliabilityTable.from_dict(t.to_dict())
        assert back == t
        assert back.calibrate(0.42) == t.calibrate(0.42)


class TestCoreStaysPure:
    def test_the_module_performs_no_io(self) -> None:
        src = code_of(calib)
        for banned in ("import requests", "sqlalchemy", "open(", "datetime.now", "psycopg"):
            assert banned not in src, f"invariant 1: {banned} in core/calibration.py"
