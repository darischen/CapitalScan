"""The promotion gate must refuse a model that has learned nothing.

**Phase 6's fourth gate, and the only one that tests the gate itself.**
`test_promotion_gate.py` has 45 passing tests and not one of them makes
`run_gate` reject anything. A gate never shown to refuse is a gate nobody
has tested, and this session's recurring failure was exactly that shape: a
measurement agreeing with itself. A void rolling-window run that read as a
clean win, a coverage gate guarding a family no surface displays, 48% of
predictions extrapolating with a calibrated number attached.

**"Flattened" means predicting the unconditional baseline everywhere** --
the model that has learned nothing but the marginal distribution. Its
pinball loss equals the baseline's by construction, so `beats_global` must
be false on every head and ADR 113's kill criterion must fire.

Two levels, because they fail differently:

- The verdict arithmetic (`beats_global`, `check5_passes`), which is where
  a `<=` for a `<` would silently promote a tie.
- The real path through `evaluate_family` on a frame with no signal in it,
  which is where a leak or a mislabelled baseline would show up.

The second needs LightGBM and is skipped without it, so the first runs
everywhere and is the one that must never be weakened.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.research import promotion


def _evaluation(
    model_loss: float, baseline: float = 0.0100, **kw: object
) -> promotion.HeadEvaluation:
    """One head's result, with only the two losses that decide check 5."""
    defaults: dict[str, object] = {
        "head": "peak_h5_q50",
        "family": "peak",
        "horizon": 5,
        "tau": 0.5,
        "n_train": 1000,
        "n_validate": 200,
        "rounds": 100,
        "model_loss": model_loss,
        "baseline_global": baseline,
        "baseline_sector": baseline,
        "baseline_ticker": baseline,
        "baseline_scaled": baseline,
        "coverage": 0.5,
    }
    defaults.update(kw)
    return promotion.HeadEvaluation(**defaults)  # type: ignore[arg-type]


class TestAFlatModelIsRejected:
    """A model whose loss equals the baseline has learned nothing."""

    def test_a_tie_does_not_count_as_beating(self) -> None:
        """**The sharp one.** `<=` here would promote a flat model.

        A flattened model's loss equals the baseline's exactly, so the
        difference between `<` and `<=` is the difference between refusing
        it and shipping it.
        """
        assert _evaluation(model_loss=0.0100, baseline=0.0100).beats_global is False

    def test_a_worse_model_does_not_beat(self) -> None:
        assert _evaluation(model_loss=0.0150, baseline=0.0100).beats_global is False

    def test_a_better_model_does_beat(self) -> None:
        """The gate must still pass something, or it is not a gate."""
        assert _evaluation(model_loss=0.0090, baseline=0.0100).beats_global is True

    def test_check5_fails_when_every_head_is_flat(self) -> None:
        """ADR 113's kill criterion, fired.

        The hypothesis is retired when *nothing* beats the baseline, so a
        report of twenty tied heads must not pass.
        """
        report = promotion.GateReport(
            evaluations=tuple(_evaluation(model_loss=0.0100) for _ in range(20))
        )
        assert report.check5_passes is False

    def test_one_beating_head_is_enough_to_pass(self) -> None:
        """Deliberately a low bar, and pinned so it is not raised by accident.

        "No better than baseline **at any horizon**" makes the failure
        condition universal. Tightening this to "all heads" would retire the
        hypothesis on a model that works somewhere, which is a different
        decision than ADR 113 made.
        """
        evaluations = [_evaluation(model_loss=0.0100) for _ in range(19)]
        evaluations.append(_evaluation(model_loss=0.0090, head="trough_h5_q50"))
        assert promotion.GateReport(evaluations=tuple(evaluations)).check5_passes is True

    def test_improvement_is_zero_for_a_flat_model(self) -> None:
        """Not merely small. A flat model removes none of the baseline loss."""
        assert _evaluation(model_loss=0.0100, baseline=0.0100).improvement == 0.0

    def test_improvement_is_negative_for_a_worse_model(self) -> None:
        """Negative rather than clamped, so a regression is visible."""
        assert _evaluation(model_loss=0.0150, baseline=0.0100).improvement < 0.0


class TestCoverageIsASeparateVerdict:
    """A flat model can be perfectly *calibrated* and still worthless.

    Predicting the unconditional quantile everywhere gives exactly nominal
    coverage, because that is what a quantile is. So coverage cannot be the
    thing that rejects it, and a gate reading only coverage would promote
    it. The two verdicts are independent on purpose.
    """

    def test_a_flat_model_can_pass_coverage_while_failing_check5(self) -> None:
        report = promotion.GateReport(
            evaluations=tuple(
                _evaluation(model_loss=0.0100, tau=0.5, coverage=0.5) for _ in range(20)
            )
        )
        assert report.coverage_passes is True
        assert report.check5_passes is False

    def test_coverage_fails_outside_the_tolerance(self) -> None:
        off = promotion.COVERAGE_TOLERANCE + 0.001
        report = promotion.GateReport(
            evaluations=(_evaluation(model_loss=0.0090, tau=0.5, coverage=0.5 + off),)
        )
        assert report.coverage_passes is False


class TestTheRealPathOnASignalFreeFrame:
    """`evaluate_family` end to end, where the label owes nothing to the features.

    The unit tests above pin the arithmetic. This pins that the arithmetic
    is fed correctly: a mislabelled baseline or a leaked feature would pass
    every test above and fail here.
    """

    @staticmethod
    def _frame(n: int, seed: int) -> pd.DataFrame:
        """Every feature the model reads, all of it noise.

        **Built from `feat.FEATURE_COLS` rather than a hand-picked list.**
        A hardcoded frame goes stale the moment a feature is added, and it
        fails as a `KeyError` in the test rather than as a finding -- which
        is how a test like this quietly stops being run.
        """
        from capitalscan.research import features as feat

        rng = np.random.default_rng(seed)
        data: dict[str, object] = {}
        for col in feat.FEATURE_COLS:
            if col in feat.CATEGORICAL_COLS:
                data[col] = rng.choice(["Tech", "Energy"], size=n)
            elif col.startswith("k_cross") or col == "above_sma200":
                data[col] = rng.integers(0, 2, size=n).astype(bool)
            else:
                data[col] = rng.normal(size=n)

        data["ticker"] = rng.choice(["AAA", "BBB", "CCC"], size=n)
        data["signal_date"] = pd.to_datetime("2020-01-01") + pd.to_timedelta(
            rng.integers(0, 500, size=n), unit="D"
        )
        data["cluster_id"] = rng.integers(0, 50, size=n)
        # **Independent of every feature above.** No model can beat the
        # unconditional quantile on this, so a head that claims to is
        # reading something it should not.
        data["peak_ret_5d"] = rng.normal(size=n) * 0.02
        data["peak_ret_10d"] = rng.normal(size=n) * 0.03
        return pd.DataFrame(data)

    def test_no_head_beats_the_baseline_on_pure_noise(self) -> None:
        pytest.importorskip("lightgbm", reason="the promotion gate needs LightGBM")

        train_frame = self._frame(4000, seed=1)
        validate_frame = self._frame(1500, seed=2)
        calendar = sorted(
            set(train_frame["signal_date"].dt.date) | set(validate_frame["signal_date"].dt.date)
        )
        evaluations = promotion.evaluate_family(
            train_frame, validate_frame, "peak", 5, calendar, rounds={"peak_h5": 30}
        )
        assert evaluations, "the family produced no heads to judge"
        # **Negligible, not negative.** A single tau can win by chance on
        # noise, and asserting every head loses outright would make this
        # test flaky for a reason that is not a defect. What must not happen
        # is a real improvement, which on a label independent of every
        # feature could only come from a leak.
        assert max(e.improvement for e in evaluations) < 0.02
