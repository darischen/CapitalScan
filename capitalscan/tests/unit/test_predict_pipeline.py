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
from capitalscan.research import features as feat
from capitalscan.research import predict as rp
from capitalscan.tests.unit._probe import code_of

REPO = Path(__file__).resolve().parents[3]


class TestTheTargetsAreChosenNotDefaulted:
    def test_the_headline_is_a_real_target(self) -> None:
        assert rp.HEADLINE in {suffix for suffix, _, _ in rp.TOUCH_TARGETS}

    def test_ten_percent_reads_the_ten_day_head(self) -> None:
        """A 10% excursion in five sessions is 1.4% of events.

        Estimating a probability against a base rate that thin is dominated
        by its own sampling noise, so the threshold reads the 10-day head
        where the rate is 7.1%. This asserts the horizon is picked per
        threshold rather than fixed, which is easy to "simplify" away.
        """
        horizons = {suffix: h for suffix, _, h in rp.TOUCH_TARGETS}
        assert horizons[10] == 10
        assert horizons[2] == horizons[3] == horizons[5] == 5

    def test_the_thresholds_match_the_field_names(self) -> None:
        """`p_touch_3` must mean 3%, or the column is a lie."""
        for suffix, threshold, _ in rp.TOUCH_TARGETS:
            assert threshold == pytest.approx(suffix / 100.0)


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
        ts = (REPO / "web" / "lib" / "screen.ts").read_text(encoding="utf-8")
        block = re.search(r"export const PREDICTION_CAVEAT =(.+?);", ts, re.S)
        assert block, "PREDICTION_CAVEAT is gone from screen.ts"
        copy = block.group(1).lower()
        for claim in ("validate", "lower bound", "advisory", "coverage"):
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
    return pd.DataFrame(
        {
            "p_touch_2": [0.7, 0.7],
            "p_touch_3": p3,
            "p_touch_5": [0.3, 0.3],
            "p_touch_10": [0.08, 0.08],
            "p_touch_3_raw": [0.61, 0.61],
            "calib_bucket": ["p_touch_3:b5", "p_touch_3:b5"],
            "calib_n_eff": [800.0, 800.0],
            "ci_low": [0.58, 0.58],
            "ci_high": [0.66, 0.66],
        }
    )


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
            bucket = table.lookup(float(p))
            assert bucket.ci_low <= table.calibrate(float(p)) <= bucket.ci_high
            # Kish is bounded above by the count of the population it was
            # computed over, and after pooling both fields describe the
            # merged block. Strictly less, because these weights are unequal.
            assert bucket.n_eff < bucket.n, "clustered events must lose effective sample"
        assert all(not math.isnan(v) for v in published)
