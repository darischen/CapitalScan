"""`docs/model_spec_adr170.json` must describe the code that is actually here.

**A recorded spec that drifts from the code is worse than no spec**, because
it will be trusted. This file exists so the drift is a failing test rather
than a reproduction that quietly produces different numbers a year from now.

The spec is generated from the module, so these assertions are cheap. They
fail the moment someone edits a hyperparameter, adds a feature, or changes
the task list without re-recording.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capitalscan.core import distributions as dist
from capitalscan.research import features as feat
from capitalscan.research import neural

SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "model_spec_adr170.json"


@pytest.fixture(scope="module")
def spec() -> dict:
    if not SPEC_PATH.exists():  # pragma: no cover - repo layout guard
        pytest.fail(f"model spec missing at {SPEC_PATH}")
    loaded = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


class TestTheSpecMatchesTheCode:
    def test_hyperparameters(self, spec: dict) -> None:
        assert spec["hyperparameters"] == dict(neural.TRAINING)

    def test_seeds(self, spec: dict) -> None:
        """Several, and the same several. Session 23 measured an unseeded
        A/B giving 17/20 and 18/20 from identical code."""
        assert spec["seeds"] == list(neural.DEFAULT_SEEDS)

    def test_tasks_and_order(self, spec: dict) -> None:
        """Head index means nothing unless the order does."""
        assert spec["architecture"]["tasks"] == [f"{f}_h{h}" for f, h in neural.TASKS]

    def test_bin_count(self, spec: dict) -> None:
        assert spec["architecture"]["n_bins"] == neural.N_BINS

    def test_feature_set(self, spec: dict) -> None:
        assert spec["features"]["cols"] == list(feat.FEATURE_COLS)
        assert spec["features"]["n"] == len(feat.FEATURE_COLS)

    def test_imputed_columns(self, spec: dict) -> None:
        assert spec["features"]["mean_imputed_with_indicator"] == list(neural.IMPUTE_COLS)

    def test_crps_grid_span(self, spec: dict) -> None:
        assert tuple(spec["grid"]["span"]) == dist.DEFAULT_CRPS_SPAN
        assert spec["grid"]["equal_width"] is True


class TestTheSpecStatesWhatIsNotTrue:
    """The spec is a reproduction record, not a sales sheet."""

    def test_it_records_that_the_gate_fails(self, spec: dict) -> None:
        gate = spec["gate"].lower()
        assert "fails" in gate
        assert "not promoted" in gate

    def test_it_records_the_holdout_is_unspent(self, spec: dict) -> None:
        assert "UNSPENT" in spec["splits"]["holdout"]

    def test_it_records_the_known_limitation(self, spec: dict) -> None:
        """The 2022 finding travels with the model or it will be forgotten."""
        limitation = spec["known_limitation"].lower()
        assert "2022" in limitation
        assert "uptrend" in limitation

    def test_coverage_is_not_claimed_to_pass(self, spec: dict) -> None:
        got = spec["measured_on_validate"]["coverage_within_5pts"]
        assert got != "20/20", "if this ever passes, the gate section must change too"


class TestProvenance:
    def test_it_pins_a_commit_and_a_config_hash(self, spec: dict) -> None:
        """Without both, "reproduce this" is underdetermined."""
        assert len(spec["git_sha"]) == 40
        assert len(spec["config_hash"]) == 16

    def test_it_names_the_entry_point(self, spec: dict) -> None:
        assert "neural.fit" in spec["entry_point"]

    def test_the_ensemble_rule_is_recorded(self, spec: dict) -> None:
        """Averaging pmfs and averaging quantiles are different operations
        and only the first yields a distribution."""
        assert "pmf" in spec["ensemble"].lower()

    def test_the_selection_protocol_is_recorded(self, spec: dict) -> None:
        """It is the half that was wrong twice before it was right."""
        selection = spec["selection"].lower()
        assert "median" in selection
        assert "ladder" in selection or "walk-forward" in selection
