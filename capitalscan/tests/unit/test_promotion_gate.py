"""ADR 113 check 5 and DESIGN §7.6 coverage, on the validate split.

**What these pin is the shape of the bar, not the numbers.** The gate's
result depends on a fitted model and a live database; what must not drift
is *which* comparison gates, *what* the kill criterion reads, and that the
baseline is never fitted on the labels being scored.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from capitalscan.research import promotion


def _evaluation(**overrides) -> promotion.HeadEvaluation:
    base = {
        "head": "terminal_h5_q50",
        "family": "terminal",
        "horizon": 5,
        "tau": 0.50,
        "n_train": 1000,
        "n_validate": 200,
        "rounds": 120,
        "model_loss": 1.0,
        "baseline_global": 2.0,
        "baseline_sector": 1.5,
        "baseline_ticker": 1.8,
        "coverage": 0.50,
    }
    return promotion.HeadEvaluation(**{**base, **overrides})


class TestCheck5GatesOnTheGlobalBaseline:
    def test_beats_global_is_the_gated_comparison(self) -> None:
        assert _evaluation(model_loss=1.0, baseline_global=2.0).beats_global
        assert not _evaluation(model_loss=2.0, baseline_global=1.0).beats_global

    def test_the_sector_baseline_does_not_gate(self) -> None:
        """ADR 167. `sector` is a model feature, so per-sector is the fair
        harder bar and worth reporting — but the kill criterion must not
        turn on it, because retiring a hypothesis is the most consequential
        act available here.
        """
        report = promotion.GateReport(
            evaluations=(_evaluation(model_loss=1.0, baseline_global=2.0, baseline_sector=0.5),)
        )
        assert report.check5_passes, "a head beating global must pass even if it loses to sector"

    def test_the_ticker_baseline_does_not_gate(self) -> None:
        """The model's 21 features do not include ticker identity, so a
        per-ticker gate would fail it for information it was never given."""
        report = promotion.GateReport(
            evaluations=(_evaluation(model_loss=1.0, baseline_global=2.0, baseline_ticker=0.1),)
        )
        assert report.check5_passes


class TestTheKillCriterionIsReadLiterally:
    def test_one_head_beating_the_baseline_passes(self) -> None:
        """ADR 113: the hypothesis is retired only when the model is "no
        better than the unconditional baseline **at any horizon**". That
        makes the failure condition universal, so one survivor passes."""
        report = promotion.GateReport(
            evaluations=(
                _evaluation(head="a", model_loss=3.0, baseline_global=2.0),
                _evaluation(head="b", model_loss=1.0, baseline_global=2.0),
            )
        )
        assert report.check5_passes

    def test_no_head_beating_the_baseline_fails(self) -> None:
        report = promotion.GateReport(
            evaluations=(
                _evaluation(head="a", model_loss=3.0, baseline_global=2.0),
                _evaluation(head="b", model_loss=2.5, baseline_global=2.0),
            )
        )
        assert not report.check5_passes

    def test_an_empty_report_does_not_pass(self) -> None:
        """A gate that has evaluated nothing has not been cleared. `any()`
        on empty is False, which is the safe direction, and this pins it
        against someone 'fixing' it to a vacuous True."""
        assert not promotion.GateReport(evaluations=()).check5_passes


class TestCoverage:
    def test_within_tolerance_passes(self) -> None:
        assert _evaluation(tau=0.25, coverage=0.28).coverage_ok
        assert _evaluation(tau=0.25, coverage=0.22).coverage_ok

    def test_outside_tolerance_fails(self) -> None:
        assert not _evaluation(tau=0.25, coverage=0.35).coverage_ok

    def test_every_tau_must_pass_not_most(self) -> None:
        """DESIGN §7.7 check 3: "within 5 points of nominal for every τ"."""
        report = promotion.GateReport(
            evaluations=(
                _evaluation(head="a", tau=0.25, coverage=0.25),
                _evaluation(head="b", tau=0.95, coverage=0.50),
            )
        )
        assert not report.coverage_passes

    def test_the_tolerance_is_five_points(self) -> None:
        assert promotion.COVERAGE_TOLERANCE == 0.05


class TestTheBaselineIsNeverFittedOnValidate:
    def test_grouped_baseline_reads_only_the_train_frame(self) -> None:
        """The validate frame supplies group *keys*, never labels.

        Fitting on the scored labels is the oracle ADR 167 rejects, and
        `research/train.py` already refuses it inside CV for the same
        reason.
        """
        src = inspect.getsource(promotion.grouped_baseline)
        assert "train_frame[label]" in src
        assert "validate_frame[label]" not in src

    def test_unseen_groups_fall_back_rather_than_dropping(self) -> None:
        """56 of 392 validate tickers are absent from train. A NaN there
        would silently drop rows from the comparison instead of scoring
        them, which flatters whichever side has fewer rows."""
        train_frame = pd.DataFrame({"sector": ["Tech", "Tech", "Energy"], "y": [1.0, 2.0, 3.0]})
        val_frame = pd.DataFrame({"sector": ["Tech", "Utilities"]})

        out = promotion.grouped_baseline(train_frame, val_frame, "y", 0.5, "sector")

        assert len(out) == 2
        assert not np.isnan(out).any(), "an unseen group must fall back, not produce NaN"

    def test_no_early_stopping_on_the_scored_split(self) -> None:
        """Early stopping needs a stopping set. Using validate for it fits
        the model to the split under test; rounds come from CV instead."""
        src = inspect.getsource(promotion.evaluate_head)
        assert "early_stopping" not in src
        assert "statistics.median" in inspect.getsource(promotion)


class TestWeighting:
    def test_coverage_is_cluster_weighted(self) -> None:
        """DESIGN §7.5's `1/|cluster|`. Unweighted, a four-event cluster
        votes four times in the coverage fraction — the same correlation
        the weights exist to undo in the loss."""
        src = inspect.getsource(promotion.evaluate_head)
        below = src[src.index("below =") :]
        assert "w_va" in below, "coverage must use the cluster weights"


class TestWiredIntoTheCli:
    def test_the_gate_command_exists(self) -> None:
        from capitalscan.jobs import cli

        src = inspect.getsource(cli)
        assert 'model_app.command("gate")' in src

    def test_it_exits_nonzero_when_check5_fails(self) -> None:
        """A gate that reports failure and exits 0 is a gate a script
        ignores."""
        from capitalscan.jobs import cli

        src = inspect.getsource(cli.model_gate)
        assert "code=0 if report.check5_passes else 1" in src

    def test_it_refuses_an_empty_validate_split(self) -> None:
        from capitalscan.jobs import cli

        assert "validate split is empty" in inspect.getsource(cli.model_gate)


class TestItCannotReachHoldout:
    def test_the_frame_builder_refuses_holdout(self) -> None:
        """ADR 019: the holdout is evaluated exactly once, at the end. The
        gate builds frames by name, so this is the guard that keeps a typo
        from spending it."""
        from capitalscan.research import features

        src = inspect.getsource(features.build_training_frame)
        assert 'split == "holdout"' in src
        assert "raise ValueError" in src

    def test_the_gate_asks_only_for_train_and_validate(self) -> None:
        from capitalscan.jobs import cli

        src = inspect.getsource(cli.model_gate)
        assert 'split="train"' in src
        assert 'split="validate"' in src
        assert "holdout" not in src


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_coverage_error_is_signed(bad: float) -> None:
    """Sign matters: over-coverage and under-coverage are different
    failures and a reader needs to see which."""
    ev = _evaluation(tau=0.5, coverage=0.5 + bad)
    assert ev.coverage_error == pytest.approx(bad)


class TestBooleanDtypeSurvivesTheRowDrops:
    """The bug that broke every fit, found 2026-09-02.

    `read_sql` returns `object` for a boolean column containing any NULL.
    `build_training_frame` then drops the offending rows -- and pandas does
    not re-infer, so the column stays `object` holding only True/False.
    LightGBM refuses it:

        ValueError: pandas dtypes must be int, float or bool.
        Fields with bad pandas dtypes: above_sma200: object

    Measured on the live config: `train` came back `object` and `validate`
    came back `bool` from the same query, purely because one split happened
    to contain a dropped row with a NULL. So it failed on one split and not
    the other, and took `research/train.py::fit_head` down with it -- the
    whole Phase 6 training path, silently, some time after Session 23 fitted
    cleanly.
    """

    def test_object_dtype_is_coerced_back_to_bool(self) -> None:
        from capitalscan.research import features

        frame = pd.DataFrame({"above_sma200": pd.Series([True, False], dtype=object)})

        out = features._coerce_boolean_features(frame)

        assert out["above_sma200"].dtype == bool

    def test_a_surviving_null_raises_rather_than_becoming_true(self) -> None:
        """`astype(bool)` turns None into True. That is invariant 4's exact
        prohibition wearing a cast, so it has to raise."""
        from capitalscan.research import features

        frame = pd.DataFrame({"above_sma200": pd.Series([True, None], dtype=object)})

        with pytest.raises(ValueError, match="NULL value"):
            features._coerce_boolean_features(frame)

    def test_every_boolean_feature_is_listed(self) -> None:
        """Named rather than sniffed, so a new boolean feature has to be
        added deliberately. These three are `boolean` in `events`."""
        from capitalscan.research import features

        assert set(features.BOOL_FEATURE_COLS) == {
            "above_sma200",
            "k_cross_up",
            "k_cross_down",
        }
        for col in features.BOOL_FEATURE_COLS:
            assert col in features.FEATURE_COLS

    def test_the_builder_coerces_before_deriving(self) -> None:
        """`_add_derived` reads feature columns, so the cast has to happen
        first or a derived column inherits the object dtype."""
        from capitalscan.research import features

        src = inspect.getsource(features.build_training_frame)
        assert src.index("_coerce_boolean_features") < src.index("_add_derived")
