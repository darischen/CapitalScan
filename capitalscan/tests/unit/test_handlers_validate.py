"""The response validator: one test per rule it enforces, and per rule it does not.

The guard's own coverage has to be complete. A guard with untested branches
is not a guard - it is a function that happens to run.

Every failure mode below is constructed by hand rather than produced by a
handler. The handlers cannot currently build these objects, which is the
point: the validator exists for the version of the code that can.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import pytest

import capitalscan.handlers.validate as V
from capitalscan.core.config import StatsParams
from capitalscan.handlers.errors import ResponseInvalid
from capitalscan.handlers.types import (
    CellStats,
    Meta,
    ScreenResult,
    ScreenRow,
    Suppressed,
)

META = Meta(config_hash="testhash")
SP = StatsParams()


def cell(**over) -> CellStats:
    """A coherent `CellStats`, close to what the live config actually returns.

    `q_value=0.849` and `survives_fdr=False` are ADR 112's train-split
    minimum, not invented numbers. A fixture that used `q=0.01` would make
    every test here exercise the branch the product never takes.
    """
    base: dict[str, Any] = dict(
        cell_id="confluence_low|long|0-10|all|next_open|train|pooled|h5|t0.03",
        signal_type="confluence_low",
        side="long",
        dd_bucket="0-10",
        signal_strength=None,
        entry_kind="next_open",
        split_key="train",
        era=None,
        horizon_days=5,
        target_pct=0.03,
        arm="signal",
        n_events=412,
        n_tickers=88,
        n_eff=93,
        ci_low=0.46,
        ci_high=0.56,
        q_value=0.8492,
        p_hit=0.51,
        baseline=0.39,
        edge=0.12,
        earnings_frac=0.07,
        p_value_randomization=0.31,
        mean_ret=0.004,
        median_ret=0.002,
        mean_mfe=0.03,
        mean_mae=-0.02,
        capture_ratio=0.4,
        mean_cofire=6.0,
        exit_mix=None,
        survives_fdr=False,
        meta=META,
    )
    base.update(over)
    return CellStats(**base)


# ---------------------------------------------------------------------------
# Rule: a probability may not leave without n_eff and an interval
# ---------------------------------------------------------------------------


def test_a_coherent_cell_passes():
    V.validate(cell(), SP)


def test_a_probability_without_n_eff_raises():
    with pytest.raises(ResponseInvalid, match="no n_eff"):
        V.validate(cell(n_eff=None), SP)


def test_a_probability_with_half_an_interval_raises():
    with pytest.raises(ResponseInvalid, match="Half an interval"):
        V.validate(cell(ci_high=None), SP)


def test_a_probability_with_no_interval_raises():
    with pytest.raises(ResponseInvalid, match="Half an interval"):
        V.validate(cell(ci_low=None, ci_high=None), SP)


def test_nan_counts_as_absent_not_as_a_value():
    """`n_eff = nan` renders as a blank and passes `is not None`.

    A value routed through pandas arrives as NaN where Postgres sent NULL,
    and treating the two differently would let a rate escape with a blank
    sample size attached - which on screen is indistinguishable from no
    sample size at all.
    """
    with pytest.raises(ResponseInvalid, match="no n_eff"):
        V.validate(cell(n_eff=float("nan")), SP)


def test_a_cell_that_claims_nothing_needs_nothing():
    """No probability stated, so no companions required.

    A cell with no events is a coherent object: it makes no claim, so there
    is nothing for an interval to be an interval *of*. Refusing it would
    force every empty cell to carry fabricated companions.
    """
    V.validate(
        cell(
            p_hit=None,
            baseline=None,
            edge=None,
            earnings_frac=None,
            n_eff=None,
            ci_low=None,
            ci_high=None,
            q_value=None,
            n_events=0,
            survives_fdr=False,
        ),
        SP,
    )


# ---------------------------------------------------------------------------
# Rule: intervals must be coherent
# ---------------------------------------------------------------------------


def test_an_inverted_interval_raises():
    with pytest.raises(ResponseInvalid, match="inverted"):
        V.validate(cell(ci_low=0.6, ci_high=0.4), SP)


def test_a_point_estimate_outside_its_own_interval_raises():
    """A Wilson interval contains its point estimate by construction.

    A violation means the rate and the interval came from different samples,
    which is what a join matching the wrong cell looks like from the outside.
    """
    with pytest.raises(ResponseInvalid, match="outside its own interval"):
        V.validate(cell(p_hit=0.9), SP)


def test_containment_tolerates_the_stored_decimal_precision():
    """`numeric(12,6)` round-trips, so exact float containment is too strict."""
    V.validate(cell(p_hit=0.46, ci_low=0.460000, ci_high=0.56), SP)
    V.validate(cell(p_hit=0.4600004, ci_low=0.46, ci_high=0.56), SP)


# ---------------------------------------------------------------------------
# Rule: q > alpha is flagged, not suppressed
# ---------------------------------------------------------------------------


def test_a_cell_that_did_not_survive_fdr_still_returns():
    """ADR 112: on the live config this is every cell that returns.

    Refusing them would empty the product. The reader learns it did not
    survive from `survives_fdr`, which is on the object rather than in a
    footnote.
    """
    result = cell(q_value=0.8492, survives_fdr=False)
    V.validate(result, SP)
    assert result.survives_fdr is False


def test_the_survives_flag_must_agree_with_the_q_value():
    with pytest.raises(ResponseInvalid, match="disagrees with"):
        V.validate(cell(q_value=0.8492, survives_fdr=True), SP)


def test_a_surviving_cell_must_say_so():
    with pytest.raises(ResponseInvalid, match="disagrees with"):
        V.validate(cell(q_value=0.01, survives_fdr=False), SP)


def test_a_cell_with_no_q_value_has_not_survived():
    """ADR 103: an era row enters no test family, so it carries no q-value.

    "Not tested" is not "survived", and a True flag with nothing behind it
    is a claim the reader cannot check.
    """
    V.validate(cell(era="2010-2014", q_value=None, survives_fdr=False), SP)
    with pytest.raises(ResponseInvalid, match="disagrees with"):
        V.validate(cell(era="2010-2014", q_value=None, survives_fdr=True), SP)


def test_the_flag_follows_the_configured_alpha_not_a_literal():
    """`fdr_alpha` is swept. A hardcoded 0.05 here would disagree with a run."""
    loose = StatsParams(fdr_alpha=0.9)
    V.validate(cell(q_value=0.8492, survives_fdr=True), loose)
    with pytest.raises(ResponseInvalid):
        V.validate(cell(q_value=0.8492, survives_fdr=False), loose)


# ---------------------------------------------------------------------------
# Rule: a suppressed cell states nothing
# ---------------------------------------------------------------------------


def test_a_suppressed_cell_passes_and_carries_its_counts():
    s = Suppressed(
        cell_id="x",
        reason="n_eff 14.0 below min_n_eff 30",
        n_events=19,
        n_eff=14,
        min_n_eff=30,
        meta=META,
    )
    V.validate(s, SP)
    assert s.n_eff == 14


def test_a_suppressed_type_that_grew_a_rate_raises():
    """Only reachable if someone adds a probability field to `Suppressed`.

    That is exactly when it should fire, so the check is written against a
    stand-in rather than skipped as unreachable.
    """

    @dataclass(frozen=True)
    class LeakySuppressed(Suppressed):
        p_hit: float | None = 0.51

    leaky = LeakySuppressed(
        cell_id="x", reason="too few", n_events=19, n_eff=14, min_n_eff=30, meta=META
    )
    with pytest.raises(ResponseInvalid, match="suppressed and still states"):
        V.validate(leaky, SP)


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


def _screen(stats) -> ScreenResult:
    row = ScreenRow(
        ticker="TSM",
        signal_date=date(2026, 8, 14),
        signal_type="confluence_low",
        signal_types_all=("confluence_low",),
        signal_strength=3,
        side="long",
        sector="Technology",
        bb_pctb=0.02,
        k_full=18.0,
        k_fast=15.0,
        k_cross_up=True,
        dd_52w=-0.14,
        dd_bucket="10-20",
        above_sma200=False,
        cofire_count=6,
        cell_id="x",
        stats=stats,
    )
    return ScreenResult(rows=(row,), total_matched=1, limit=50, with_stats=True, meta=META)


def test_the_validator_reaches_a_cell_nested_two_levels_down():
    """A `ScreenResult` never carries a probability itself.

    Validating only the outer object would check the one shape in this layer
    that cannot violate the rule, and miss every shape that can.
    """
    with pytest.raises(ResponseInvalid, match="no n_eff"):
        V.validate(_screen(cell(n_eff=None)), SP)


def test_a_screen_result_with_no_statistics_passes():
    V.validate(_screen(None), SP)


def test_the_walk_terminates_on_a_repeated_object():
    """One `CellStats` attached to many rows is the normal case.

    Fifty screener rows in one drawdown bucket share a cell, and the walk
    must not re-validate it fifty times or follow itself into a loop.
    """
    shared = cell()
    rows = tuple(replace(_screen(shared).rows[0], ticker=f"T{i}") for i in range(50))
    V.validate(ScreenResult(rows=rows, total_matched=50, limit=50, with_stats=True, meta=META), SP)


# ---------------------------------------------------------------------------
# The escape hatch, and the shape of the API
# ---------------------------------------------------------------------------


def test_the_escape_hatch_is_off():
    """Session 15.3 permits a module-level debug flag and requires this test.

    Module-level rather than a per-call `validate=False`, because a keyword
    ends up in one call site, then in a copied one, and the guarantee is
    gone with nothing to point at. Flipping this constant is a source edit
    that shows up in a diff and fails here.
    """
    assert V._DISABLED is False


def test_validation_cannot_be_disabled_per_call():
    import inspect

    for fn in (V.validate, V.validated):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"validate", "skip", "check", "enabled"}, (
            f"{fn.__name__} accepts a per-call bypass"
        )


def test_validated_returns_the_object_it_checked():
    """The return-through shape is what makes the call visible at a glance."""
    c = cell()
    assert V.validated(c, SP) is c


def test_the_validator_refuses_rather_than_repairing():
    """A failing response is raised, never returned with a filled interval.

    A response that silently acquires a plausible interval is a response
    that ships. A raise is a defect report.
    """
    broken = cell(ci_low=None, ci_high=None)
    with pytest.raises(ResponseInvalid):
        V.validated(broken, SP)
    assert broken.ci_low is None and broken.ci_high is None


# ---------------------------------------------------------------------------
# flagged_cells
# ---------------------------------------------------------------------------


def test_flagged_cells_names_every_cell_that_did_not_survive():
    result = _screen(cell(cell_id="cl|long|0-10", q_value=0.8492, survives_fdr=False))
    assert V.flagged_cells(result, SP) == ("cl|long|0-10",)


def test_flagged_cells_ignores_a_cell_that_states_nothing():
    """A cell with no rate has nothing to caveat."""
    quiet = cell(
        p_hit=None,
        baseline=None,
        edge=None,
        earnings_frac=None,
        n_eff=None,
        ci_low=None,
        ci_high=None,
        q_value=None,
        survives_fdr=False,
    )
    assert V.flagged_cells(_screen(quiet), SP) == ()


def test_flagged_cells_is_not_a_refusal():
    """It reports; it never raises. The distinction is the whole design."""
    result = _screen(cell(survives_fdr=False))
    V.validate(result, SP)
    assert V.flagged_cells(result, SP)
