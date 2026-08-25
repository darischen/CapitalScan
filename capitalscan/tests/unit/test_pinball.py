"""Pinball loss and the unconditional baseline (ADR 113 check 5).

Written before the implementation, per the `core/` rule.

ADR 067's four promotion checks all compare a retrained model against an
incumbent. On a first model there is no incumbent, so a model can pass all
four by default while being worse than a constant. That gap was harmless
while a measured edge was expected. After ADR 112 — three configurations,
630,592 events, zero cells surviving FDR — it is not.

Check 5 closes it: out-of-sample pinball loss must beat the unconditional
baseline, fit with no features. And ADR 113 fixes the consequence in
advance: fail it on validation at every horizon and the two-indicator
hypothesis is retired at the model layer too, with that published.

So this module is the thing the kill criterion is measured with. If it is
wrong in the optimistic direction, the criterion cannot fire.
"""

from __future__ import annotations

import numpy as np
import pytest

from capitalscan.core import pinball

# ---------------------------------------------------------------------------
# The loss
# ---------------------------------------------------------------------------


def test_a_perfect_prediction_costs_nothing():
    assert pinball.pinball_loss([1.0, 2.0], [1.0, 2.0], tau=0.5) == pytest.approx(0.0)


def test_the_median_penalises_both_directions_equally():
    """At tau=0.5 the loss is symmetric — half the absolute error."""
    over = pinball.pinball_loss([0.0], [1.0], tau=0.5)
    under = pinball.pinball_loss([0.0], [-1.0], tau=0.5)
    assert over == pytest.approx(under)
    assert over == pytest.approx(0.5)


def test_a_high_quantile_punishes_under_prediction_harder():
    """tau=0.95 asks for a value exceeded 5% of the time. Predicting too
    low is the expensive error, and the asymmetry is the whole mechanism —
    a symmetric loss fits the mean and produces no fan at all.
    """
    too_low = pinball.pinball_loss([0.0], [1.0], tau=0.95)
    too_high = pinball.pinball_loss([0.0], [-1.0], tau=0.95)
    assert too_low > too_high
    assert too_low == pytest.approx(0.95)
    assert too_high == pytest.approx(0.05)


def test_a_low_quantile_punishes_over_prediction_harder():
    assert pinball.pinball_loss([0.0], [-1.0], tau=0.05) == pytest.approx(0.95)
    assert pinball.pinball_loss([0.0], [1.0], tau=0.05) == pytest.approx(0.05)


def test_the_loss_is_the_mean_not_the_sum():
    """Otherwise train and validate losses are incomparable, and check 5
    compares exactly those."""
    one = pinball.pinball_loss([0.0], [1.0], tau=0.5)
    four = pinball.pinball_loss([0.0] * 4, [1.0] * 4, tau=0.5)
    assert one == pytest.approx(four)


def test_sample_weights_are_respected():
    """DESIGN §7.5 weights cluster members `1/|cluster|`. A loss ignoring
    weights scores a four-event cluster four times, which is the
    correlation the weighting exists to undo."""
    unweighted = pinball.pinball_loss([0.0, 0.0], [1.0, 0.0], tau=0.5)
    weighted = pinball.pinball_loss([0.0, 0.0], [1.0, 0.0], tau=0.5, weights=[0.0, 1.0])
    assert unweighted == pytest.approx(0.25)
    assert weighted == pytest.approx(0.0)


def test_nan_targets_are_excluded_not_imputed():
    """Invariant 4. An unresolved label is absent, and scoring it as zero
    would make a model look better the more unresolved events it saw."""
    got = pinball.pinball_loss([0.0, 0.0], [1.0, float("nan")], tau=0.5)
    assert got == pytest.approx(0.5)


def test_all_nan_targets_raise_rather_than_return_zero():
    """A loss of 0.0 on an empty comparison reads as a perfect model, and
    check 5 would pass on it."""
    with pytest.raises(ValueError, match="no comparable"):
        pinball.pinball_loss([0.0], [float("nan")], tau=0.5)


@pytest.mark.parametrize("tau", [0.0, 1.0, -0.1, 1.5])
def test_tau_outside_the_open_unit_interval_raises(tau: float):
    """tau=0 and tau=1 are degenerate: the loss becomes one-sided and is
    minimised by predicting an infinity."""
    with pytest.raises(ValueError, match="tau"):
        pinball.pinball_loss([0.0], [1.0], tau=tau)


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        pinball.pinball_loss([0.0, 1.0], [1.0], tau=0.5)


# ---------------------------------------------------------------------------
# The unconditional baseline
# ---------------------------------------------------------------------------


def test_the_baseline_is_the_empirical_quantile_of_the_training_labels():
    """No features. The number a model must beat to be worth having."""
    labels = list(range(101))  # 0..100
    assert pinball.unconditional_quantile(labels, tau=0.5) == pytest.approx(50.0)
    assert pinball.unconditional_quantile(labels, tau=0.05) == pytest.approx(5.0, abs=0.5)


def test_the_baseline_ignores_nan_labels():
    assert pinball.unconditional_quantile([1.0, 2.0, 3.0, float("nan")], tau=0.5) == pytest.approx(
        2.0
    )


def test_the_baseline_is_fitted_on_train_and_scored_on_validate():
    """The signature enforces the direction: it returns a scalar from
    training labels, which the caller then scores against held-out ones.

    A baseline fitted on the same split it is scored on is not a baseline —
    it is an oracle, and it would be *harder* to beat than the honest one,
    so check 5 would fire when it should not.
    """
    import inspect

    params = list(inspect.signature(pinball.unconditional_quantile).parameters)
    assert params == ["labels", "tau"]


def test_an_empty_label_set_raises():
    with pytest.raises(ValueError):
        pinball.unconditional_quantile([], tau=0.5)


def test_the_baseline_beats_a_worse_constant():
    """Sanity: the empirical quantile is the loss-minimising constant, so
    nothing constant can beat it on the same data. If this fails, check 5
    is measuring against the wrong thing.
    """
    labels = [float(v) for v in range(101)]
    best = pinball.unconditional_quantile(labels, tau=0.5)
    best_loss = pinball.pinball_loss([best] * len(labels), labels, tau=0.5)
    for worse in (best - 10, best + 10, 0.0, 100.0):
        assert pinball.pinball_loss([worse] * len(labels), labels, tau=0.5) >= best_loss


@pytest.mark.parametrize("tau", [0.05, 0.25, 0.5, 0.75, 0.95])
def test_the_optimal_constant_property_holds_at_every_tau(tau: float):
    """The same argument across ADR 113's whole fan, on an asymmetric
    distribution where a mean-fitting mistake would show."""
    rng = np.random.default_rng(0)
    labels = list(rng.lognormal(size=2000))
    best = pinball.unconditional_quantile(labels, tau=tau)
    best_loss = pinball.pinball_loss([best] * len(labels), labels, tau=tau)
    for delta in (-0.5, -0.1, 0.1, 0.5):
        shifted = pinball.pinball_loss([best + delta] * len(labels), labels, tau=tau)
        assert shifted >= best_loss - 1e-9
