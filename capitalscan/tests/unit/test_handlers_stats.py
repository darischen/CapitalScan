"""`get_stats`: the union, the cell key, and the refusal to widen.

The rows below are shaped like the ones the live config actually produces
under `86e91448a65aa40b` - a suppressed cell at `n_eff` 14 against a floor of
30, and an unsuppressed one at q 0.849. ADR 112 measured 100 of 224 train
cells in the first state and 0 of 124 surviving correction in the second, so
a fixture with a healthy significant cell would be testing a branch the
product does not take.
"""

from __future__ import annotations

import pytest

from capitalscan.core.cells import cell_key
from capitalscan.core.config import StatsParams
from capitalscan.handlers.errors import HoldoutRequested, InvalidEnum
from capitalscan.handlers.stats import get_stats
from capitalscan.handlers.types import CellStats, Suppressed

SP = StatsParams()

LIVE_CELL = {
    "cell_id": "will-be-overwritten",
    "run_id": "run-abc",
    "config_hash": "testhash",
    "signal_type": "confluence_low",
    "side": "long",
    "dd_bucket": "0-10",
    "signal_strength": None,
    "entry_kind": "next_open",
    "split_key": "train",
    "era": None,
    "horizon_days": 5,
    "target_pct": 0.03,
    "arm": "signal",
    "n_events": 412,
    "n_eff": 93,
    "n_tickers": 88,
    "mean_cofire": 6.0,
    "p_hit": 0.51,
    "baseline_empirical": 0.39,
    "edge": 0.12,
    "ci_low": 0.46,
    "ci_high": 0.56,
    "q_value": 0.8492,
    "p_value_randomization": 0.31,
    "mean_ret": 0.004,
    "median_ret": 0.002,
    "mean_mfe": 0.03,
    "mean_mae": -0.02,
    "capture_ratio": 0.4,
    "exit_mix": {"target": 0.4, "timeout": 0.6},
    "earnings_frac": 0.07,
    "suppressed": False,
    "suppress_reason": None,
}

SUPPRESSED_CELL = dict(
    LIVE_CELL,
    n_events=19,
    n_eff=14,
    p_hit=None,
    baseline_empirical=None,
    edge=None,
    ci_low=None,
    ci_high=None,
    q_value=None,
    earnings_frac=None,
    suppressed=True,
    suppress_reason="n_eff 14.0 below min_n_eff 30",
)


def _key(**over):
    args = dict(
        signal_type="confluence_low",
        side="long",
        dd_bucket="0-10",
        strength=None,
        entry_kind="next_open",
        split="train",
        era=None,
        horizon=5,
        target=0.03,
    )
    args.update(over)
    return cell_key(**args)


def _call(fake_db, **over):
    kwargs = dict(
        signal_type="confluence_low",
        target_pct=0.03,
        dd_bucket="0-10",
        split="train",
        engine=object(),
    )
    kwargs.update(over)
    return get_stats(**kwargs)


# ---------------------------------------------------------------------------
# The union
# ---------------------------------------------------------------------------


def test_an_unsuppressed_cell_returns_cell_stats(fake_db):
    fake_db.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=_key())])
    result = _call(fake_db)
    assert isinstance(result, CellStats)
    assert result.p_hit == 0.51
    assert result.baseline == 0.39
    assert (result.ci_low, result.ci_high) == (0.46, 0.56)


def test_a_suppressed_cell_returns_suppressed_with_the_stored_reason(fake_db):
    """The stored reason, not a fresh one.

    `research/cell_stats.py` wrote it against the `min_n_eff` in force at
    compute time. A reason regenerated here would quote today's floor
    against yesterday's counts.
    """
    fake_db.on("FROM cell_stats", [dict(SUPPRESSED_CELL, cell_id=_key())])
    result = _call(fake_db)
    assert isinstance(result, Suppressed)
    assert result.reason == "n_eff 14.0 below min_n_eff 30"
    assert (result.n_events, result.n_eff) == (19, 14)


def test_a_suppressed_result_carries_no_rate_under_any_name(fake_db):
    fake_db.on("FROM cell_stats", [dict(SUPPRESSED_CELL, cell_id=_key())])
    result = _call(fake_db)
    assert not hasattr(result, "p_hit")


def test_a_cell_that_was_never_computed_returns_suppressed_not_an_error(fake_db):
    """ADR 101 permanently suppresses `20-35` and `35+`.

    Those cells are outside the headline grid and were never written, and
    saying so is more useful than either raising or answering with the
    nearest cell that does exist.
    """
    fake_db.on("FROM cell_stats", [])
    result = _call(fake_db, dd_bucket="35+")
    assert isinstance(result, Suppressed)
    assert result.reason == "cell not computed for this config"
    assert result.min_n_eff == SP.min_n_eff


# ---------------------------------------------------------------------------
# The refusal to widen
# ---------------------------------------------------------------------------


def test_a_suppressed_cell_never_becomes_a_broader_one(fake_db):
    """One query, one cell. There is no retry path in the handler at all.

    DESIGN §11.2's system prompt asks the chat layer not to substitute.
    Asking is not a guarantee, so this handler has nowhere in it that drops
    a predicate and tries again - which is what this asserts by counting
    the queries rather than by checking the answer.
    """
    fake_db.on("FROM cell_stats", [dict(SUPPRESSED_CELL, cell_id=_key())])
    _call(fake_db)
    assert len(fake_db.sql_containing("FROM cell_stats")) == 1


def test_the_query_pins_the_exact_cell_id(fake_db):
    fake_db.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=_key())])
    _call(fake_db)
    sql, params = next(c for c in fake_db.calls if "FROM cell_stats" in c[0])
    assert params["cell_id"] == _key()
    assert params["config_hash"] == "testhash"
    assert params["arm"] == "signal"


# ---------------------------------------------------------------------------
# The cell key
# ---------------------------------------------------------------------------


def test_the_side_is_derived_from_the_signal_type(fake_db):
    """`cell_stats` is keyed by side and DESIGN §10.1's signature has none.

    Side is a property of the type, not an independent axis, so asking the
    caller for it would let them request a cell that cannot exist.
    """
    fake_db.on("FROM cell_stats", [])
    _call(fake_db, signal_type="confluence_high")
    _, params = next(c for c in fake_db.calls if "FROM cell_stats" in c[0])
    assert "|short|" in params["cell_id"]


def test_signal_strength_pools_by_default(fake_db):
    """ADR 107: the serving cell is the one pooled over strength.

    `signal_strength` is `len(signal_types_all)` and takes two values, so
    splitting on it halves `n_eff` to distinguish confluence from not - a
    distinction `signal_type` already carries.
    """
    fake_db.on("FROM cell_stats", [])
    _call(fake_db)
    _, params = next(c for c in fake_db.calls if "FROM cell_stats" in c[0])
    assert "|all|" in params["cell_id"]


def test_the_horizon_defaults_to_max_hold_days_not_a_literal(fake_db):
    from capitalscan.core.config import ExitParams

    fake_db.on("FROM cell_stats", [])
    _call(fake_db)
    _, params = next(c for c in fake_db.calls if "FROM cell_stats" in c[0])
    assert f"h{ExitParams().max_hold_days}" in params["cell_id"]


def test_an_era_produces_a_descriptive_key(fake_db):
    fake_db.on("FROM cell_stats", [])
    _call(fake_db, era="2010-2014")
    _, params = next(c for c in fake_db.calls if "FROM cell_stats" in c[0])
    assert "2010-2014" in params["cell_id"]
    assert "pooled" not in params["cell_id"]


# ---------------------------------------------------------------------------
# survives_fdr
# ---------------------------------------------------------------------------


def test_survives_fdr_is_false_on_the_live_configs_numbers(fake_db):
    """ADR 112: zero cells survive on either split. This is that, on a row."""
    fake_db.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=_key())])
    result = _call(fake_db)
    assert result.q_value == 0.8492
    assert result.survives_fdr is False


def test_survives_fdr_is_true_only_below_the_configured_alpha(fake_db):
    fake_db.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=_key(), q_value=0.01)])
    assert _call(fake_db).survives_fdr is True


def test_a_row_with_no_q_value_has_not_survived(fake_db):
    """ADR 103: era rows enter no family. "Not tested" is not "survived"."""
    fake_db.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=_key(), q_value=None)])
    assert _call(fake_db).survives_fdr is False


def test_the_baseline_is_the_empirical_one(fake_db):
    """ADR 013 measures edge against the empirical baseline.

    The parametric one is a diagnostic, and surfacing it under the same name
    would make two different numbers look interchangeable.
    """
    fake_db.on(
        "FROM cell_stats",
        [dict(LIVE_CELL, cell_id=_key(), baseline_empirical=0.39, baseline_parametric=0.44)],
    )
    assert _call(fake_db).baseline == 0.39


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def test_holdout_raises_before_any_query_runs(fake_db):
    with pytest.raises(HoldoutRequested):
        _call(fake_db, split="holdout")
    assert not fake_db.sql_containing("FROM cell_stats")


def test_an_unmeasured_target_raises(fake_db):
    with pytest.raises(InvalidEnum, match="not measured"):
        _call(fake_db, target_pct=0.04)


def test_meta_carries_the_config_and_the_split(fake_db):
    fake_db.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=_key())])
    meta = _call(fake_db).meta
    assert meta.config_hash == "testhash"
    assert meta.split == "train"
    assert meta.run_id == "run-abc"


def test_two_identical_calls_return_equal_results(fake_db):
    """Session 15 gate item 10, on the one handler with a union return."""
    fake_db.on("FROM cell_stats", [dict(LIVE_CELL, cell_id=_key())])
    assert _call(fake_db) == _call(fake_db)
