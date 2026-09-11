"""ADR 174's shipping path: what must stay true for a probability to be publishable.

These are the decisions that are invisible once the pipeline works and
expensive when they silently stop holding. None of them needs a database:
the ones that would are asserted structurally instead, because a guard that
only runs when Postgres is up is not a guard the fast tier can enforce.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import calibration as calib
from capitalscan.jobs import predict as jp
from capitalscan.research import features as feat
from capitalscan.research import neural
from capitalscan.research import predict as rp
from capitalscan.tests.unit._probe import code_of

REPO = Path(__file__).resolve().parents[3]


class TestTheTargetsAreChosenNotDefaulted:
    def test_the_headline_is_a_real_target(self) -> None:
        assert rp.HEADLINE in {t.field for t in rp.TARGETS}

    def test_ten_percent_reads_the_ten_day_head(self) -> None:
        """A 10% excursion in five sessions is 1.4% of events.

        Estimating a probability against a base rate that thin is dominated
        by its own sampling noise, so the threshold reads the 10-day head
        where the rate is 7.1%. This asserts the horizon is picked per
        threshold rather than fixed, which is easy to "simplify" away.
        """
        horizons = {t.field: t.horizon for t in rp.TARGETS}
        assert horizons["p_touch_10"] == 10
        assert horizons["p_touch_2"] == horizons["p_touch_3"] == horizons["p_touch_5"] == 5

    def test_the_thresholds_match_the_field_names(self) -> None:
        """`p_touch_3` must mean 3% and `p_adverse_3` must mean -3%."""
        for t in rp.TARGETS:
            magnitude = int(t.field.rsplit("_", 1)[1]) / 100.0
            assert abs(t.threshold) == pytest.approx(magnitude)

    def test_the_adverse_targets_are_negative_and_read_the_trough_head(self) -> None:
        """ADR 175. Sign and family must agree, or the field is a lie.

        `trough_ret_5d` is negative when the position went against you, so
        an adverse threshold is a negative number and the question is a
        shortfall. A positive threshold on a trough head would ask how
        likely it is that the position went against you by *less* than
        nothing, which is nearly always true and looks like a probability.
        """
        adverse = [t for t in rp.TARGETS if t.field.startswith("p_adverse")]
        assert adverse, "ADR 175 fields are missing"
        for t in adverse:
            assert t.family == "trough"
            assert t.threshold < 0
            assert t.direction == "below"

    def test_the_touch_targets_are_positive_and_read_the_peak_head(self) -> None:
        for t in rp.TARGETS:
            if t.field.startswith("p_touch"):
                assert t.family == "peak"
                assert t.threshold > 0
                assert t.direction == "above"

    def test_every_target_names_a_head_the_model_actually_fits(self) -> None:
        """A target whose `(family, horizon)` is not in `TASKS` raises deep
        inside inference. Catching it here names the target."""
        for t in rp.TARGETS:
            assert (t.family, t.horizon) in neural.TASKS, t.field

    def test_direction_decides_both_the_probability_and_the_outcome(self) -> None:
        """The two must agree, or calibration fits a probability against
        the complement of what it predicts and still looks monotone."""
        labels = np.array([-0.05, -0.01, 0.04])
        above = rp.Target("x", "peak", 5, 0.03, "above")
        below = rp.Target("y", "trough", 5, -0.03, "below")
        assert above.realised(labels).tolist() == [0.0, 0.0, 1.0]
        assert below.realised(labels).tolist() == [1.0, 0.0, 0.0]

        grid = np.linspace(-0.2, 0.2, 33)
        pmf = np.full((1, 32), 1 / 32)
        assert above.probability(pmf, grid)[0] == pytest.approx(
            1.0 - below.probability(pmf, grid)[0], abs=0.2
        )


class TestTheCaveatTravels:
    def test_it_says_the_calibration_split_was_reused(self) -> None:
        text = calib.MODEL_CAVEAT.lower()
        assert "validate" in text
        assert "lower bound" in text

    def test_it_says_the_system_is_advisory(self) -> None:
        assert "advisory" in calib.MODEL_CAVEAT.lower()

    def test_research_re_exports_rather_than_redefining(self) -> None:
        """Two spellings of a caveat is how one of them goes stale."""
        assert rp.MODEL_CAVEAT is calib.MODEL_CAVEAT

    def test_the_handler_reaches_it_through_core_not_research(self) -> None:
        """No handler may import the fitting stack.

        `handlers/predict.py` needs the caveat string and nothing else from
        that direction. Importing `research` to get it would pull pandas,
        the feature builder and the torch entry point into the serving
        path, which is why the constant lives in `core`.
        """
        src = (REPO / "capitalscan" / "handlers" / "predict.py").read_text(encoding="utf-8")
        assert "from capitalscan.core.calibration import MODEL_CAVEAT" in src
        assert "capitalscan.research" not in src

    def test_the_typescript_copy_has_not_drifted(self) -> None:
        """The UI keeps its own copy so the tooltip is never async-absent.

        Two copies is one more than ideal, so this pins the claims they
        must share. It compares substance rather than characters: the
        wording differs because the UI string is shorter, and asserting
        equality would fail on a legitimate edit.
        """
        # `format.ts`, not `screen.ts`: the constant moved there on
        # 2026-09-08 because `screen.ts` imports `./db`, so a client
        # component importing this string pulled `pg` into the browser
        # bundle and killed the Pi build. `screen.ts` re-exports it, so
        # server callers were unaffected -- but this test reads the file
        # that defines it.
        ts = (REPO / "web" / "lib" / "format.ts").read_text(encoding="utf-8")
        # **The whole caveat region, not one constant.** It was split into
        # `_SUMMARY` and `_DETAIL` on 2026-09-08 so the modal could collapse
        # it, and `PREDICTION_CAVEAT` is now composed from the two rather
        # than restated. Matching only the composed export would read a
        # template literal naming two identifiers and find none of the
        # claims below -- a test that passes on the wrong text is worse
        # than no test.
        block = re.search(
            r"export const PREDICTION_CAVEAT_SUMMARY =(.+?)export const PREDICTION_CAVEAT =",
            ts,
            re.S,
        )
        assert block, "the PREDICTION_CAVEAT constants are gone from format.ts"
        copy = block.group(1).lower()
        # The composed export must still exist for surfaces that cannot
        # collapse it, and must be built from the halves so they cannot
        # drift apart.
        assert "`${PREDICTION_CAVEAT_SUMMARY} ${PREDICTION_CAVEAT_DETAIL}`" in ts, (
            "PREDICTION_CAVEAT must be composed from its halves, not restated"
        )
        # "rank" and "understate" pin the 2026-09-08 measurement: the
        # ordering held across all eight probability bands while the shipped
        # value missed the band's own 95% interval in six. A caveat that
        # drops it leaves a number the project has measured as biased
        # looking exactly as trustworthy as one it has not.
        for claim in ("validate", "lower bound", "advisory", "coverage", "rank", "understate"):
            assert claim in copy, f"the UI caveat dropped '{claim}'"


class TestTheServingFrameCannotBeScored:
    """Predicting on holdout rows is fine. Scoring them spends the holdout."""

    def test_it_drops_the_label_columns(self) -> None:
        src = code_of(feat.build_serving_frame)
        assert "drop(columns=" in src
        assert "LABEL_COLS" in src

    def test_it_selects_by_date_and_never_by_split(self) -> None:
        """A split filter here could reach `holdout` by name.

        The training builder refuses `split='holdout'` explicitly. This one
        must not take a split at all, so there is no argument to typo.
        """
        src = code_of(feat.build_serving_frame)
        assert "signal_date >= :since" in src
        assert ":split" not in src

    def test_the_training_builder_still_refuses_the_holdout(self) -> None:
        src = code_of(feat.build_training_frame)
        assert "split == 'holdout'" in src


class TestServingScoresOnlyWhatWasTrained:
    """**The population the model saw must bound the population it scores.**

    Found live on 2026-09-08: 4,207 of 8,699 shipped predictions -- 48% --
    were for `stoch_oversold`/`stoch_overbought`, and the training frame
    contained **zero** rows of either. The screener showed a calibrated
    probability with an interval and an `n_eff` for signals the model had
    never been fitted on, and nothing in the output distinguished them from
    the half that were legitimate.

    The cause is structural rather than a typo, which is why it needs a
    test rather than a fix. `build_training_frame` drops rows with NULL
    labels, and stochastic-only signals have no fill price and so no label.
    `build_serving_frame` drops `LABEL_COLS` outright -- ADR 174's guard
    against scoring the holdout -- so it has nothing left to filter on and
    keeps every row. The two builders disagreed *because* of a correct
    safety measure in one of them.
    """

    def test_it_keeps_only_the_types_the_fit_saw(self) -> None:
        frame = pd.DataFrame(
            {"signal_type": ["bb_lower_touch", "stoch_oversold", "confluence_low"]}
        )
        kept, dropped = feat.restrict_to_trained_types(frame, ["bb_lower_touch", "confluence_low"])
        assert list(kept["signal_type"]) == ["bb_lower_touch", "confluence_low"]
        assert dropped == 1

    def test_no_fit_in_hand_means_no_filter(self) -> None:
        frame = pd.DataFrame({"signal_type": ["stoch_oversold"]})
        kept, dropped = feat.restrict_to_trained_types(frame, None)
        assert len(kept) == 1 and dropped == 0

    def test_a_fit_that_saw_nothing_scores_nothing(self) -> None:
        """Empty must not fall through to "no restriction".

        An empty sequence is falsy, so the obvious `if not trained_types`
        would treat a broken fit as an unrestricted one and score the whole
        population off it -- the precise failure this class exists to stop.
        """
        frame = pd.DataFrame({"signal_type": ["bb_lower_touch"]})
        kept, dropped = feat.restrict_to_trained_types(frame, [])
        assert len(kept) == 0 and dropped == 1

    def test_the_serving_builder_applies_it(self) -> None:
        assert "restrict_to_trained_types" in code_of(feat.build_serving_frame)

    def test_the_job_passes_the_types_the_predictor_actually_saw(self) -> None:
        """Read off the fit, never hardcoded.

        Which types training contains moves as the backtest prices more
        events: stochastic rows gain labels once they carry a fill, and
        then they belong in both frames. A literal list would freeze that
        and drift silently in whichever direction is worse.
        """
        src = code_of(jp.run_predict)
        assert "predictor.trained_signal_types" in src
        assert "trained_types=trained_types" in src


def _fake(applied: pd.DataFrame) -> rp.FittedPredictor:
    """A stand-in with just the surface `build_rows` touches.

    `cast` rather than a real `FittedPredictor`: constructing one needs a
    fitted `Ensemble`, which needs torch, which would put a 2GB optional
    dependency on the fast tier for a test about dictionary shapes.
    """
    return cast(rp.FittedPredictor, _FakePredictor(applied))


class _FakePredictor:
    """Enough of `FittedPredictor` for `build_rows`, with no torch."""

    model_version = "test-model"
    n_train = 100
    n_calibrate = 50

    def __init__(self, applied: pd.DataFrame) -> None:
        self._applied = applied

    def apply(self, frame: pd.DataFrame) -> pd.DataFrame:
        return self._applied


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "signal_date": [pd.Timestamp("2026-08-20").date(), pd.Timestamp("2026-08-21").date()],
            "id": [11, 22],
            "signal_type": ["confluence_low", "confluence_low"],
            "side": ["long", "long"],
        }
    )


def _applied(p3: list[float]) -> pd.DataFrame:
    """The shape `FittedPredictor.apply` returns, generated from `TARGETS`.

    Built from the target list rather than written out, so adding a
    published field cannot leave this fixture describing the old set while
    the tests below still pass.
    """
    n = len(p3)
    defaults = {
        "p_touch_2": 0.70,
        "p_touch_3": None,  # filled from the argument
        "p_touch_5": 0.30,
        "p_touch_10": 0.08,
        "p_adverse_3": 0.41,
        "p_adverse_5": 0.22,
    }
    frame = pd.DataFrame(index=range(n))
    for target in rp.TARGETS:
        values = p3 if target.field == rp.HEADLINE else [defaults[target.field]] * n
        frame[target.field] = values
        frame[f"{target.field}_raw"] = [0.61] * n
        # Bracket each field's OWN value. `core.calibration` guarantees a
        # published probability sits inside its bucket's interval, so a
        # fixture that shares one interval across fields would be a shape
        # the real pipeline cannot produce.
        frame[f"{target.field}__lo"] = [
            (v - 0.02) if v is not None and v == v else 0.0 for v in values
        ]
        frame[f"{target.field}__hi"] = [
            (v + 0.02) if v is not None and v == v else 1.0 for v in values
        ]
        frame[f"{target.field}__n_eff"] = [800.0] * n
        frame[f"{target.field}__bucket"] = [5] * n
    # The quantile fan, generated from `FAN_TAUS` for the same reason the
    # targets are: adding a tau must not leave this fixture describing the
    # old set while the tests below still pass.
    for offset, col in enumerate(rp._FAN_COLUMNS):
        frame[col] = [-0.06 + 0.03 * offset] * n
    frame["calib_bucket"] = [f"{rp.HEADLINE}:b5"] * n
    frame["calib_n_eff"] = [800.0] * n
    frame["ci_low"] = [0.58] * n
    frame["ci_high"] = [0.66] * n
    return frame


class TestBuildRows:
    def test_a_row_carries_its_run_and_config(self) -> None:
        """Invariant 6, and the generation scoping migration 174 added."""
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        assert len(rows) == 2
        for row in rows:
            assert row["run_id"] == "run-1"
            assert row["git_sha"] == "abc1234"
            assert row["config_hash"] == "chash123"

    def test_a_non_finite_headline_is_dropped_not_written_as_null(self) -> None:
        """`v_screen` LEFT JOINs this table.

        A row of NULLs looks identical on screen to no prediction, while
        still occupying the unique `(ticker, as_of)` key a later good
        prediction would need. Dropping is the honest write.
        """
        rows = rp.build_rows(
            _fake(_applied([float("nan"), 0.62])),
            _frame(),
            "chash123",
            "run-1",
            "abc1234",
        )
        assert [r["ticker"] for r in rows] == ["BBB"]

    def test_the_caveat_is_written_into_every_row(self) -> None:
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        assert all(r["features_json"]["caveat"] == calib.MODEL_CAVEAT for r in rows)

    def test_every_published_field_carries_its_own_interval(self) -> None:
        """Invariant 8 attaches to each probability, not to the row.

        Before ADR 175 the row held one interval, from `p_touch_3`'s
        bucket. Surfacing `p_adverse_3` beside that interval would render
        correctly and describe a different quantity computed over a
        different reliability table.
        """
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        for row in rows:
            evidence = row["calibration_json"]
            assert {t.field for t in rp.TARGETS} == set(evidence)
            for field, block in evidence.items():
                assert block["lo"] <= block["p"] <= block["hi"], field
                assert block["n_eff"] > 0

    def test_the_published_probability_is_inside_its_published_interval(self) -> None:
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        for row in rows:
            assert row["ci_low"] <= row["p_touch_3"] <= row["ci_high"]


class TestTheJobGuards:
    def test_it_upserts_on_the_event_and_not_on_ticker_and_date(self) -> None:
        """`(ticker, as_of)` is not a key, and the failure is not loud.

        Fourteen ticker-days in two months carry two events, and they are
        opposite sides -- a `bb_upper_touch` short and a `stoch_oversold`
        long on the same name, same day. Keying on the pair forces a
        choice between them, and the screener would then render one side's
        row beside the other side's probability. `event_id` is exact.
        """
        from capitalscan.jobs import predict as jp

        src = code_of(jp.run_predict)
        assert "conflict_cols=['event_id']" in src
        assert "'ticker', 'as_of'" not in src

    def test_it_refuses_to_publish_off_a_thin_calibration_sample(self) -> None:
        from capitalscan.jobs import predict as jp

        assert jp.MIN_CALIBRATION_ROWS >= 1000
        assert "MIN_CALIBRATION_ROWS" in code_of(jp.run_predict)

    def test_the_window_is_anchored_on_the_data_not_on_today(self) -> None:
        """A stale database should write a small correct set, not an empty one."""
        from capitalscan.jobs import predict as jp

        src = code_of(jp.run_predict)
        assert "latest_signal_date" in src
        assert "date.today" not in src


class TestCalibrationHoldsOnRealShapedData:
    """An end-to-end property on the pure layer, with no model involved."""

    def test_a_skilful_overconfident_model_becomes_publishable(self) -> None:
        rng = np.random.default_rng(174)
        n = 20_000
        raw = rng.beta(2.0, 2.0, n)
        realised = (rng.uniform(size=n) < 0.2 + 0.6 * raw).astype(float)
        # Clustered, as real events are: co-firing names share a weight.
        weights = np.repeat(1.0 / rng.integers(1, 12, n // 20 + 1), 20)[:n]

        table = calib.build_reliability("p_touch_3", raw, realised, weights=weights)

        published = np.array([table.calibrate(float(p)) for p in raw])
        base = float((weights * realised).sum() / weights.sum())
        brier = float((weights * (published - realised) ** 2).sum() / weights.sum())
        ref = float((weights * (base - realised) ** 2).sum() / weights.sum())

        assert brier < ref, "calibration must beat the base rate"
        for p in raw[:500]:
            # **Against `band`, not against `lookup` (ADR 192).**
            #
            # This asserted the point against its *bucket's* interval, and
            # that is precisely the pairing ADR 174 warned interpolation
            # would break -- correctly: it broke here first, on raw
            # 0.2726, publishing 0.3486 against a bucket ceiling of
            # 0.3464.
            #
            # The property is unchanged and the interval is the one that
            # actually ships. `band` interpolates the point and both
            # bounds together, so containment holds everywhere rather than
            # only at anchors. Reverting this to `lookup` would assert a
            # contract nothing renders.
            value, lo, hi, n_eff = table.band(float(p))
            assert lo <= value <= hi
            assert value == table.calibrate(float(p)), "the two must not diverge"
            bucket = table.lookup(float(p))
            # Kish is bounded above by the count of the population it was
            # computed over, and after pooling both fields describe the
            # merged block. Strictly less, because these weights are unequal.
            assert bucket.n_eff < bucket.n, "clustered events must lose effective sample"
            assert n_eff <= bucket.n, "an interpolated point cannot gain support"
        assert all(not math.isnan(v) for v in published)

    def test_interpolation_did_not_cost_calibration(self) -> None:
        """**The reversal has to pay for itself in the metric, not only in
        distinctness.** ADR 192 buys ordering; it must not buy it with
        skill. Same fixture, comparing the shipped map against the
        piecewise-constant one it replaced.
        """
        rng = np.random.default_rng(174)
        n = 20_000
        raw = rng.beta(2.0, 2.0, n)
        realised = (rng.uniform(size=n) < 0.2 + 0.6 * raw).astype(float)
        weights = np.repeat(1.0 / rng.integers(1, 12, n // 20 + 1), 20)[:n]
        table = calib.build_reliability("p_touch_3", raw, realised, weights=weights)

        def brier(values: np.ndarray) -> float:
            return float((weights * (values - realised) ** 2).sum() / weights.sum())

        interpolated = np.array([table.calibrate(float(p)) for p in raw])
        stepwise = np.array([table.lookup(float(p)).p_hat for p in raw])

        assert brier(interpolated) <= brier(stepwise) + 1e-6, (
            "interpolation made the published probabilities worse"
        )
        assert len(set(interpolated.round(6))) > len(set(stepwise.round(6))), (
            "interpolation must produce more distinct values than the step map"
        )


class TestTheQuantileFanIsActuallyWritten:
    """ADR 174 said the fan "stays in the payload". It did not.

    All 4,264 rows of the first production run carried NULL `q05..q95`,
    because `build_rows` never emitted them -- the ADR described code that
    was not there. The fan is not decoration: `outcomes` scores a pinball
    loss against it, so a fan that is never recorded can never be scored.
    """

    def test_every_stored_quantile_reaches_the_row(self) -> None:
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        for row in rows:
            for col in rp._FAN_COLUMNS:
                assert col in row, f"{col} missing from the written row"
                assert row[col] is not None

    def test_the_columns_match_the_taus(self) -> None:
        assert rp._FAN_COLUMNS == ("q05", "q25", "q50", "q75", "q95")
        assert len(rp._FAN_COLUMNS) == len(rp.FAN_TAUS)

    def test_the_fan_comes_from_the_terminal_head(self) -> None:
        """The peak and trough families are extremes; their quantiles would
        answer a different question under the same column name."""
        assert rp.FAN_TASK == ("terminal", 5)
        assert rp.FAN_TASK in neural.TASKS

    def test_the_resolver_scores_the_same_taus_the_writer_stores(self) -> None:
        """A quantile scored against the wrong tau gives a plausible loss
        and no error."""
        from capitalscan.jobs import outcomes as oc

        assert tuple(col for col, _ in oc.FAN) == rp._FAN_COLUMNS
        assert tuple(tau for _, tau in oc.FAN) == rp.FAN_TAUS


class TestTheNaturalKeyIsWritten:
    """The screener views join on it, and the writer used to omit it.

    Migration `e4b19c86d275` added `predictions.signal_type` and
    `.entry_kind` and backfilled them from `events`. `build_rows` was never
    taught to set them, so every prediction written afterwards carried
    NULLs. Measured 2026-09-09: **11,006 of 19,705 rows**, invisible to
    `v_screen` and `v_screen_live` while looking perfectly healthy in the
    table -- a `SELECT count(*)` said the predictions existed, and the
    screener showed an empty Inference cell.

    That is the shape worth testing for. A missing column would be loud; a
    NULL in a join key is silent, and the surface degrades without an
    error anywhere.
    """

    def test_rows_carry_signal_type_and_entry_kind(self) -> None:
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        assert rows, "no rows to check"
        for row in rows:
            assert row["signal_type"], "signal_type missing; the view join will not match"
            assert row["entry_kind"], "entry_kind missing; the view join will not match"

    def test_entry_kind_falls_back_to_the_filtered_value(self) -> None:
        """`_SQL` filters on `entry_kind` without selecting it.

        So the frame usually lacks the column, and the fallback must be the
        value that filter used rather than a guess -- otherwise the written
        key would not match the events the frame came from.
        """
        from capitalscan.research import features as feat

        assert "entry_kind" not in _frame().columns
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        assert all(r["entry_kind"] == feat.TRAINING_ENTRY_KIND for r in rows)

    def test_the_key_matches_what_the_views_join_on(self) -> None:
        """Pins the tuple, so a view change and the writer cannot drift.

        `events` keys on five columns and serving assigns its own `id`, so
        `event_id` is useless across a sync -- which is why this key exists
        at all (migration `e4b19c86d275`).
        """
        rows = rp.build_rows(
            _fake(_applied([0.62, 0.62])), _frame(), "chash123", "run-1", "abc1234"
        )
        for name in ("config_hash", "ticker", "as_of", "signal_type", "entry_kind"):
            assert name in rows[0], f"{name} is part of the view join and is not written"


class TestTheNightlyFetchesAPublishedArtifact:
    """**The missing half of ADR 185.**

    `publish()` writes the model into serving's `model_artifact` so a
    machine without the file can get it. Until 2026-09-10 the only caller
    of `fetch()` was `cscan predict --serving`; `nightly` runs against the
    research engine, never took that branch, and so never fetched.

    It surfaced at the `wivie` cutover: a database dump carries no files,
    so the new research box reported `skip predict: no artifact` and
    produced no predictions at all until the file was copied by hand.
    """

    def test_a_present_artifact_is_left_alone(self, tmp_path, monkeypatch):
        """Staleness is `artifact.load`'s decision. Re-downloading over a
        present file would paper over exactly the mismatch it exists to
        catch -- and the poller asks on a 20-second cadence."""
        from capitalscan.jobs import artifact
        from capitalscan.jobs import predict as jp

        local = tmp_path / "predictor.npz"
        local.write_bytes(b"not empty")
        monkeypatch.setattr(artifact, "DEFAULT_PATH", local)

        called = []
        monkeypatch.setattr(artifact, "fetch", lambda *a, **k: called.append(1))

        assert jp._fetch_if_absent("abc123") is False
        assert called == [], "must not fetch when a local artifact exists"

    def test_an_absent_artifact_is_fetched(self, tmp_path, monkeypatch):
        from capitalscan.jobs import artifact
        from capitalscan.jobs import predict as jp
        from capitalscan.jobs import sync as sync_job

        missing = tmp_path / "predictor.npz"
        monkeypatch.setattr(artifact, "DEFAULT_PATH", missing)
        monkeypatch.setattr(sync_job, "serving_engine", lambda: object())
        monkeypatch.setattr(artifact, "fetch", lambda _e, _h: missing)

        assert jp._fetch_if_absent("abc123") is True

    def test_no_published_row_reports_false_rather_than_raising(self, tmp_path, monkeypatch):
        """`fetch` returns None on a store that has never had a weekly.
        That is an ordinary state, and the caller's StaleArtifact carries
        the right remedy."""
        from capitalscan.jobs import artifact
        from capitalscan.jobs import predict as jp
        from capitalscan.jobs import sync as sync_job

        monkeypatch.setattr(artifact, "DEFAULT_PATH", tmp_path / "predictor.npz")
        monkeypatch.setattr(sync_job, "serving_engine", lambda: object())
        monkeypatch.setattr(artifact, "fetch", lambda _e, _h: None)

        assert jp._fetch_if_absent("abc123") is False

    def test_an_unreachable_serving_store_is_not_fatal(self, tmp_path, monkeypatch):
        """**A research box with no serving configured is normal (ADR 053).**
        Raising here would turn a non-fatal 'skip predict' into a failed
        nightly, which is strictly worse than the gap being fixed."""
        from capitalscan.jobs import artifact
        from capitalscan.jobs import predict as jp
        from capitalscan.jobs import sync as sync_job

        monkeypatch.setattr(artifact, "DEFAULT_PATH", tmp_path / "predictor.npz")

        def _boom():
            raise RuntimeError("DATABASE_URL_SERVING not set")

        monkeypatch.setattr(sync_job, "serving_engine", _boom)
        assert jp._fetch_if_absent("abc123") is False
