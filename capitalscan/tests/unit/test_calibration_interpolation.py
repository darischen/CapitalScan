"""Interpolated calibration keeps the point inside its own interval.

**ADR 192 reverses ADR 174 on this, and the reversal is only safe because
of one thing**, which is what most of this file tests.

The old design was piecewise constant, and its stated reason was sound:

    Interpolating between bucket centres ... quietly breaks the one
    property that matters for display: the published point must lie inside
    its own published interval.

True of interpolating the *point alone*. `band()` interpolates `p_hat`,
`ci_low` and `ci_high` together, so the containment holds at every anchor
and a convex combination preserves it. If a future change interpolates the
point while taking bounds from `lookup`, these tests are what should fail.

What forced the change: on serving 2026-09-09, 498 predictions carried 498
distinct raw scores and **9 distinct published values**, with 142 tickers
pinned to exactly 0.4780 across raw 0.375-0.495. Inside that block the
model's ordering was gone, and ordering is the output the level's own
instability makes durable.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from capitalscan.core.calibration import Bucket, ReliabilityTable, build_reliability


def _table(rows: list[tuple[float, float, float, float, float]]) -> ReliabilityTable:
    """`(lo, hi, p_hat, ci_low, ci_high)` per bucket, n and n_eff filled in."""
    return ReliabilityTable(
        target="p_touch_3",
        buckets=tuple(
            Bucket(
                index=i,
                lo=lo,
                hi=hi,
                n=1000,
                n_eff=800.0,
                p_hat=p_hat,
                ci_low=ci_low,
                ci_high=ci_high,
            )
            for i, (lo, hi, p_hat, ci_low, ci_high) in enumerate(rows)
        ),
    )


#: Three separated blocks, the shape that makes interpolation visible.
SIMPLE = _table(
    [
        (-math.inf, 0.4, 0.30, 0.27, 0.33),
        (0.4, 0.6, 0.50, 0.47, 0.53),
        (0.6, math.inf, 0.70, 0.67, 0.73),
    ]
)

#: Two adjacent buckets pooled to one rate -- the 142-ticker shape.
POOLED = _table(
    [
        (-math.inf, 0.35, 0.40, 0.37, 0.43),
        (0.35, 0.45, 0.478, 0.45, 0.50),
        (0.45, 0.55, 0.478, 0.45, 0.50),
        (0.55, math.inf, 0.60, 0.57, 0.63),
    ]
)


class TestTheContainmentPropertyTheOldDesignProtected:
    """`ci_low <= p_hat <= ci_high`, everywhere, not only at anchors."""

    @pytest.mark.parametrize("table", [SIMPLE, POOLED])
    def test_the_point_lies_inside_its_interval_across_the_whole_range(self, table) -> None:
        for raw in np.linspace(0.0, 1.0, 501):
            p, lo, hi, _ = table.band(float(raw))
            assert lo <= p <= hi, f"raw={raw:.4f} published {p} outside [{lo}, {hi}]"

    def test_it_would_fail_if_the_bounds_came_from_the_bucket(self) -> None:
        """The specific mistake, demonstrated rather than described.

        Pairing the interpolated point with `lookup`'s bucket interval is
        what ADR 174 warned about. Between two well-separated blocks the
        point moves by the gap between rates, which exceeds the
        half-width.
        """
        raw = 0.5999  # near the top of the middle bucket, below block 3
        p, _, _, _ = SIMPLE.band(raw)
        bucket = SIMPLE.lookup(raw)
        assert p > bucket.ci_high, (
            "this raw score should expose the mistake; if it no longer does, "
            "the fixture changed and the test needs a new witness"
        )
        _, lo, hi, _ = SIMPLE.band(raw)
        assert lo <= p <= hi, "the interpolated interval must still contain it"


class TestOrderingSurvives:
    def test_two_different_raw_scores_give_two_different_published_values(self) -> None:
        """The 142-ticker failure, at its smallest."""
        low = POOLED.calibrate(0.375)
        high = POOLED.calibrate(0.495)
        assert low != high, "a pooled block still collapses distinct scores"
        assert low < high

    def test_a_pooled_block_no_longer_flattens(self) -> None:
        """Anchoring per bucket rather than per block would fail this.

        Two buckets sharing a rate would place two anchors at the same
        height, and the segment between them would be flat -- reproducing
        the tie.
        """
        values = [POOLED.calibrate(v) for v in (0.36, 0.40, 0.44, 0.48, 0.52)]
        assert len(set(values)) == len(values), f"flat segment: {values}"

    def test_the_map_is_monotone_non_decreasing(self) -> None:
        """PAVA's guarantee must survive interpolation.

        A higher raw score may never publish a lower probability; that is
        the property isotonic pooling exists to enforce.
        """
        for table in (SIMPLE, POOLED):
            out = [table.calibrate(float(v)) for v in np.linspace(0.0, 1.0, 401)]
            assert all(a <= b + 1e-12 for a, b in zip(out, out[1:], strict=False))

    def test_ordering_is_preserved_from_a_real_fit(self) -> None:
        """End to end through `build_reliability`, not a hand fixture."""
        rng = np.random.default_rng(11)
        p = rng.uniform(0.1, 0.9, 4000)
        y = (rng.uniform(size=4000) < p).astype(float)
        table = build_reliability("p_touch_3", p, y)

        probe = np.linspace(0.15, 0.85, 200)
        out = [table.calibrate(float(v)) for v in probe]
        assert all(a <= b + 1e-12 for a, b in zip(out, out[1:], strict=False))
        assert len(set(out)) > len(table.buckets), (
            "interpolation should produce more distinct values than there are buckets"
        )


class TestTheEdgesAreClampedNotExtrapolated:
    def test_below_the_first_anchor_uses_the_first_block(self) -> None:
        """Inventing a rate beyond the sample is the one thing the module
        refuses; the nearest measured evidence is the honest answer."""
        p, lo, hi, _ = SIMPLE.band(0.0)
        first = SIMPLE.buckets[0]
        assert (p, lo, hi) == (first.p_hat, first.ci_low, first.ci_high)

    def test_above_the_last_anchor_uses_the_last_block(self) -> None:
        p, lo, hi, _ = SIMPLE.band(1.0)
        last = SIMPLE.buckets[-1]
        assert (p, lo, hi) == (last.p_hat, last.ci_low, last.ci_high)

    def test_it_never_leaves_zero_one(self) -> None:
        for table in (SIMPLE, POOLED):
            for raw in np.linspace(-0.5, 1.5, 401):
                p, lo, hi, _ = table.band(float(raw))
                assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0
                assert 0.0 <= p <= 1.0


class TestTheWeakerClaimWins:
    def test_n_eff_takes_the_smaller_bracketing_block(self) -> None:
        """An interpolated point is supported by neither block alone."""
        table = _table(
            [
                (-math.inf, 0.5, 0.30, 0.27, 0.33),
                (0.5, math.inf, 0.70, 0.60, 0.80),
            ]
        )
        table = ReliabilityTable(
            target=table.target,
            buckets=(
                table.buckets[0],
                Bucket(
                    index=1,
                    lo=0.5,
                    hi=math.inf,
                    n=100,
                    n_eff=90.0,
                    p_hat=0.70,
                    ci_low=0.60,
                    ci_high=0.80,
                ),
            ),
        )
        _, _, _, n_eff = table.band(0.5)
        assert n_eff == 90.0, "took the stronger block's support"


class TestNaNStaysNaN:
    def test_a_missing_prediction_never_becomes_a_number(self) -> None:
        p, _, _, _ = SIMPLE.band(float("nan"))
        assert math.isnan(p)
        assert math.isnan(SIMPLE.calibrate(float("nan")))

    def test_nan_takes_the_widest_interval(self) -> None:
        """An absent prediction must not surface as a confident one."""
        table = _table(
            [
                (-math.inf, 0.5, 0.30, 0.29, 0.31),
                (0.5, math.inf, 0.70, 0.50, 0.90),
            ]
        )
        _, lo, hi, _ = table.band(float("nan"))
        assert (lo, hi) == (0.50, 0.90)


class TestItStillAgreesWithTheTableAtTheAnchors:
    def test_a_block_midpoint_publishes_that_blocks_measured_rate(self) -> None:
        """Interpolation must not move the numbers that were measured.

        At a block's own anchor the published value is the observed rate,
        unchanged -- so the reliability page still describes the same
        curve.
        """
        # Middle block of SIMPLE spans [0.4, 0.6]; its anchor is 0.5.
        p, lo, hi, _ = SIMPLE.band(0.5)
        assert p == pytest.approx(0.50)
        assert (lo, hi) == pytest.approx((0.47, 0.53))
