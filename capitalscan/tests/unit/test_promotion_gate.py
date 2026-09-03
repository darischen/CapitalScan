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
        "baseline_scaled": 1.6,
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
        src = inspect.getsource(promotion._fit_and_predict)
        assert "early_stopping" not in src
        assert "statistics.median" in inspect.getsource(promotion)


class TestWeighting:
    def test_coverage_is_cluster_weighted(self) -> None:
        """DESIGN §7.5's `1/|cluster|`. Unweighted, a four-event cluster
        votes four times in the coverage fraction — the same correlation
        the weights exist to undo in the loss."""
        src = inspect.getsource(promotion.score_family)
        below = src[src.index("below =") :]
        assert "* w).sum() / w.sum()" in below, "coverage must use the cluster weights"


class TestScoringIsSharedWithChallengers:
    """Session 24 split `score_family` out so a non-LightGBM model is
    scored by the same code as the incumbent.

    The gate's meaning lives in the baselines and the cluster weighting.
    A challenger that reimplemented scoring would be comparing two rulers,
    which is the failure that keeps recurring in this project: a broken
    instrument agreeing with itself.
    """

    def test_score_family_fits_nothing(self) -> None:
        src = inspect.getsource(promotion.score_family)
        for forbidden in ("lgb.train", "lightgbm", "fit_head", "_fit_and_predict"):
            assert forbidden not in src, f"{forbidden} makes the scorer a fitter"

    def test_evaluate_family_delegates_to_it(self) -> None:
        """Not two code paths that agree today."""
        assert "return score_family(" in inspect.getsource(promotion.evaluate_family)

    def test_it_takes_predictions_rather_than_a_model(self) -> None:
        params = inspect.signature(promotion.score_family).parameters
        assert "sorted_pred" in params
        assert not any(p in params for p in ("model", "booster", "estimator"))

    def test_it_does_not_resort_the_fan(self) -> None:
        """Sorting is the producer's job (DESIGN §7.4). A model with a
        monotone parameterisation has nothing to repair, and re-sorting
        here would hide a challenger whose fan actually crosses."""
        assert "sort_quantiles" not in inspect.getsource(promotion.score_family)

    def test_rounds_is_optional_and_defaults_to_zero(self) -> None:
        """A neural or in-context model has no `num_boost_round`. Reporting
        a fabricated one would read as a fitted hyperparameter."""
        params = inspect.signature(promotion.score_family).parameters
        assert params["rounds"].default is None
        assert "used_rounds.get(tau, 0)" in inspect.getsource(promotion.score_family)


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


class TestQuantileCrossingIsRepaired:
    """DESIGN §7.4: independent heads carry no monotonicity constraint, so a
    fitted `Q_0.25` can exceed `Q_0.50` on some feature vectors. Sorting is
    the documented repair.

    **`train.sort_quantiles` had no production caller until 2026-09-02.**
    It was written in Session 23 and never wired in, and the first version
    of this module scored each head alone -- which skipped the sort by
    construction, since sorting is across τ. Every coverage number in the
    first gate run was measured on a fan the design does not ship.
    """

    def test_the_gate_sorts_across_tau(self) -> None:
        assert "sort_quantiles" in inspect.getsource(promotion.evaluate_family)

    def test_it_sorts_before_scoring(self) -> None:
        """Sorting after the loss is computed repairs nothing.

        Since Session 24 the loss lives in `score_family`, so the ordering
        is across the delegation rather than inside one function: sort,
        then hand the sorted fan over. Asserting both halves keeps the
        property pinned now that a second caller of the scorer exists.
        """
        src = inspect.getsource(promotion.evaluate_family)
        assert src.index("sort_quantiles") < src.index("return score_family(")
        assert "sorted_pred" in src[src.index("return score_family(") :]
        assert "pinball_loss" in inspect.getsource(promotion.score_family)

    def test_the_unit_of_evaluation_is_a_family_and_horizon(self) -> None:
        """Head by head cannot sort: the five taus have to exist together."""
        sig = inspect.signature(promotion.evaluate_family)
        assert "family" in sig.parameters
        assert "horizon" in sig.parameters
        assert "tau" not in sig.parameters

    def test_run_gate_still_emits_every_head(self) -> None:
        src = inspect.getsource(promotion.run_gate)
        assert "all_heads()" in src
        assert "evaluate_family" in src

    def test_crossing_is_measurable_not_only_repaired(self) -> None:
        """A high crossing rate means the heads disagree, which sorting
        hides without fixing. It has to be reportable."""
        # Column 0 descends (0.9, 0.5, 0.1) and crosses; column 1 ascends
        # (0.1, 0.5, 0.9) and does not. Half the rows, and writing the
        # fixture this way is deliberate -- the first version asserted 1.0
        # and was wrong about its own data.
        mixed = {
            0.25: np.array([0.9, 0.1]),
            0.5: np.array([0.5, 0.5]),
            0.75: np.array([0.1, 0.9]),
        }
        assert promotion.crossing_rate(mixed) == 0.5

        clean = {0.25: np.array([0.1]), 0.5: np.array([0.5]), 0.75: np.array([0.9])}
        assert promotion.crossing_rate(clean) == 0.0

        every = {0.25: np.array([0.9]), 0.5: np.array([0.5]), 0.75: np.array([0.1])}
        assert promotion.crossing_rate(every) == 1.0


class TestTheVolatilityScaledBaseline:
    """The bar that separates volatility scaling from skill (item 1 of the
    Phase 6 refinement plan).

    The measured train->validate shift is a volatility increase --
    `fwd_ret_5d` sd +12%, `peak_ret_5d` q75 +28%. ADR 167's raw constant
    cannot follow it, so *any* predictor that scales with volatility beats
    it whether or not it knows anything about direction. The peak family's
    10-20% gains are exactly that shape.
    """

    def test_the_summary_shows_the_scaled_loss_not_only_the_verdict(self) -> None:
        """Measured 2026-09-02: the scaled constant has HIGHER pinball loss
        than the raw one on all 20 heads, so `beats_scaled` is an easier
        bar. A boolean alone would read as a passed test. The value has to
        be visible beside it."""
        frame = promotion.GateReport(evaluations=(_evaluation(),)).summary()
        assert "baseline_scaled" in frame.columns

    def test_it_is_reported_but_does_not_gate(self) -> None:
        """ADR 167 fixed what the kill criterion reads. Widening the gate
        after seeing the numbers would be choosing the bar to suit the
        result."""
        report = promotion.GateReport(
            evaluations=(_evaluation(model_loss=1.0, baseline_global=2.0, baseline_scaled=0.5),)
        )
        assert report.check5_passes
        assert not report.evaluations[0].beats_scaled

    def test_it_rescales_per_row(self) -> None:
        """A constant in scale-free units times each row's own sigma."""
        train_frame = pd.DataFrame({"y": [1.0, 2.0, 3.0, 4.0], "rv_pct_252d": [1.0, 1.0, 1.0, 1.0]})
        val_frame = pd.DataFrame({"rv_pct_252d": [1.0, 2.0]})

        out = promotion.scaled_baseline(train_frame, val_frame, "y", 0.5)

        assert out[1] == pytest.approx(2 * out[0]), "double the scale, double the quantile"

    def test_a_missing_scale_falls_back_rather_than_dropping(self) -> None:
        """Both baselines must be measured on the same rows. A NaN here
        would silently drop rows from one side of the comparison -- the
        ADR 165 lesson about which rows are averaged."""
        train_frame = pd.DataFrame({"y": [1.0, 2.0, 3.0], "rv_pct_252d": [1.0, 1.0, 1.0]})
        val_frame = pd.DataFrame({"rv_pct_252d": [1.0, float("nan"), 0.0, -1.0]})

        out = promotion.scaled_baseline(train_frame, val_frame, "y", 0.5)

        assert len(out) == 4
        assert not np.isnan(out).any()

    def test_it_reads_the_scale_the_model_already_has(self) -> None:
        """Giving the baseline a feature the model lacks would make it an
        unfair bar. `rv_pct_252d` is in FEATURE_COLS."""
        from capitalscan.research import features

        assert promotion.SCALE_COL in features.FEATURE_COLS

    def test_the_baseline_never_reads_validate_labels(self) -> None:
        src = inspect.getsource(promotion.scaled_baseline)
        assert "train_frame[label]" in src
        assert "validate_frame[label]" not in src


class TestBreachDepth:
    """ADR 069's deferred feature (Phase 6 refinement item 3).

    `bb_pctb` is `(close - lower) / (upper - lower)` and already reaches
    -0.588 on train events, so close-based depth ships today. The signal is
    a *touch* — the low crosses the band and the close can be back inside
    by the bell — so the low-based depth is a different quantity, and
    measured on train the two correlate only **-0.054**.
    """

    def test_it_is_signed_by_side(self) -> None:
        """A long breaches downward through the lower band, a short upward
        through the upper. Unsigned, the feature would mean opposite things
        in the two halves of the population."""
        from capitalscan.research import features

        frame = pd.DataFrame(
            {
                "side": ["long", "short"],
                # width 10 in both rows
                "band_lower": [100.0, 100.0],
                "band_upper": [110.0, 110.0],
                # long pierces 2 below the lower; short pierces 3 above the upper
                "bar_low": [98.0, 105.0],
                "bar_high": [105.0, 113.0],
            }
        )

        got = features._breach_depth(frame)

        assert got.iloc[0] == pytest.approx(0.2), "long: (lower - low) / width"
        assert got.iloc[1] == pytest.approx(0.3), "short: (high - upper) / width"

    def test_a_bar_inside_the_band_is_negative(self) -> None:
        """Positive means 'further past the band'. A bar that never reached
        it must not read as a shallow breach."""
        from capitalscan.research import features

        frame = pd.DataFrame(
            {
                "side": ["long"],
                "band_lower": [100.0],
                "band_upper": [110.0],
                "bar_low": [103.0],
                "bar_high": [108.0],
            }
        )
        assert features._breach_depth(frame).iloc[0] < 0

    def test_a_degenerate_band_is_null_not_infinite(self) -> None:
        """A zero width is an absent band, not an infinitely deep breach,
        and `x / 0` would split a tree on something that means nothing."""
        from capitalscan.research import features

        frame = pd.DataFrame(
            {
                "side": ["long", "long"],
                "band_lower": [100.0, 100.0],
                "band_upper": [100.0, 90.0],
                "bar_low": [98.0, 98.0],
                "bar_high": [102.0, 102.0],
            }
        )
        got = features._breach_depth(frame)
        assert got.isna().all(), "zero and inverted widths must both be NaN"

    def test_it_reads_the_band_the_signal_saw(self) -> None:
        """Invariant 3: `detect` compares against t-1's band. Measuring the
        breach against day t's band would use a boundary the signal never
        saw."""
        from capitalscan.research import features

        assert "i.ts < e.signal_date" in features._SQL
        assert "ORDER BY i.ts DESC" in features._SQL

    def test_it_is_causal_for_the_only_entry_kind_built(self) -> None:
        """Day t's low is final before day t+1's open, so a `next_open`
        entry knows it. For a `touch` entry it would be look-ahead, and the
        frame must not build one."""
        from capitalscan.research import features

        assert "e.entry_kind = 'next_open'" in features._SQL

    def test_the_raw_inputs_never_reach_the_matrix(self) -> None:
        """`bar_low` and the rest are meta. A model handed the raw low is
        handed the signal day's outcome in a different shape."""
        from capitalscan.research import features

        for col in ("bar_low", "bar_high", "band_lower", "band_upper"):
            assert col in features.META_COLS
            assert col not in features.FEATURE_COLS

    def test_side_is_carried_but_not_a_feature(self) -> None:
        """`signal_type` already encodes direction; a second copy lets the
        model split on one fact twice."""
        from capitalscan.research import features

        assert "side" in features.META_COLS
        assert "side" not in features.FEATURE_COLS

    def test_it_is_in_the_feature_set(self) -> None:
        from capitalscan.research import features

        assert "breach_depth" in features.DERIVED_FEATURE_COLS
        assert "breach_depth" in features.FEATURE_COLS
