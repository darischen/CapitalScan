"""Head enumeration and the fold plumbing around the fit (ADR 113).

No LightGBM call here — fitting needs a database and minutes, and lives in
the integration tier. What is checked is the part that decides *what* gets
fitted and *on which rows*, which is where a silent mistake would live: a
head fitted on the wrong label, or a fold whose purge never ran, produces a
number that looks like a result.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from capitalscan.research import train

# ---------------------------------------------------------------------------
# The twenty heads
# ---------------------------------------------------------------------------


def test_there_are_exactly_twenty_heads():
    """ADR 113 cut ~56 to 20. Five τ × two horizons × two families."""
    assert len(train.all_heads()) == 20


def test_the_head_set_matches_adr_113():
    families = {f for f, _, _ in train.all_heads()}
    horizons = {h for _, h, _ in train.all_heads()}
    taus = {t for _, _, t in train.all_heads()}
    assert families == {"terminal", "peak"}
    assert horizons == {5, 10}
    assert taus == {0.05, 0.25, 0.50, 0.75, 0.95}


def test_the_retired_horizons_are_absent():
    """1, 2 and 3 are dropped: the shortest windows carry the least
    movement relative to noise."""
    assert not {h for _, h, _ in train.all_heads()} & {1, 2, 3}


def test_the_head_order_is_stable():
    """Two runs should be comparable line by line. A set-derived order would
    make a diff of two reports meaningless."""
    assert train.all_heads() == train.all_heads()


def test_head_names_are_unique_and_readable():
    names = [train.head_name(f, h, t) for f, h, t in train.all_heads()]
    assert len(set(names)) == 20
    assert "terminal_h5_q50" in names
    assert "peak_h10_q95" in names


@pytest.mark.parametrize(
    "family,horizon,expected",
    [
        ("terminal", 5, "fwd_ret_5d"),
        ("terminal", 10, "fwd_ret_10d"),
        ("peak", 5, "peak_ret_5d"),
        ("peak", 10, "peak_ret_10d"),
    ],
)
def test_each_family_maps_to_its_label_column(family, horizon, expected):
    """`terminal` is R_h and `peak` is M_h. Swapping them fits every head on
    the wrong target and still returns plausible losses, because both
    columns are returns on the same scale."""
    assert train.label_for(family, horizon) == expected


def test_an_unknown_family_raises():
    with pytest.raises(ValueError, match="unknown family"):
        train.label_for("reachability", 5)


# ---------------------------------------------------------------------------
# Hyperparameters
# ---------------------------------------------------------------------------


def test_the_hyperparameters_are_designs():
    """DESIGN §7.5, chosen against an effective sample near 8,000. If these
    drift, the conservatism argument stops holding."""
    for key, value in {
        "num_leaves": 15,
        "max_depth": 4,
        "min_child_samples": 100,
        "learning_rate": 0.03,
        "lambda_l2": 5.0,
    }.items():
        assert train.LGBM_PARAMS[key] == value


def test_early_stopping_is_on():
    """Without it, `MAX_ROUNDS` trees are fitted regardless and every head
    overfits its fold."""
    assert train.EARLY_STOPPING_ROUNDS == 50


# ---------------------------------------------------------------------------
# Fold masks — where a silent leak would live
# ---------------------------------------------------------------------------


def _frame(n: int = 900) -> pd.DataFrame:
    """Spans 2010 to ~2017 at 3-day spacing.

    Sized deliberately: at 400 rows the frame stopped in 2013, so a fold
    validating on 2015 selected nothing and every assertion below compared
    zero against zero and passed. A fixture too small to reach the thing
    under test is the quietest way to write a test that checks nothing.
    """
    start = date(2010, 1, 4)
    dates = [start + timedelta(days=3 * i) for i in range(n)]
    return pd.DataFrame(
        {
            "signal_date": dates,
            "cluster_id": [None] * n,
            "fwd_ret_5d": np.linspace(-0.1, 0.1, n),
        }
    )


def _calendar() -> list[date]:
    start = date(2010, 1, 1)
    return [start + timedelta(days=i) for i in range(365 * 12)]


def test_train_and_validate_masks_never_overlap():
    """The property that makes a fold a fold. If it fails, the model is
    scored on rows it trained on and every loss is meaningless."""
    frame = _frame()
    from capitalscan.core.folds import Fold

    tr, va = train._fold_masks(frame, Fold(2010, 2014, 2015), calendar=_calendar(), horizon_days=10)
    assert not (tr & va).any()


def test_validation_rows_come_only_from_the_fold_year():
    frame = _frame()
    from capitalscan.core.folds import Fold

    _, va = train._fold_masks(frame, Fold(2010, 2014, 2015), calendar=_calendar(), horizon_days=10)
    years = pd.to_datetime(frame["signal_date"]).dt.year[va]
    assert set(years) == {2015}


def test_training_rows_stop_before_the_fold_year():
    frame = _frame()
    from capitalscan.core.folds import Fold

    tr, _ = train._fold_masks(frame, Fold(2010, 2014, 2015), calendar=_calendar(), horizon_days=10)
    years = pd.to_datetime(frame["signal_date"]).dt.year[tr]
    assert years.max() <= 2014


def test_the_purge_actually_removes_rows():
    """A mask that purges nothing is indistinguishable from a correct one in
    every downstream number, which is why this is asserted rather than
    assumed."""
    frame = _frame()
    from capitalscan.core.folds import Fold

    tr_purged, _ = train._fold_masks(
        frame, Fold(2010, 2014, 2015), calendar=_calendar(), horizon_days=10
    )
    tr_unpurged, _ = train._fold_masks(
        frame, Fold(2010, 2014, 2015), calendar=_calendar(), horizon_days=0
    )
    assert tr_purged.sum() < tr_unpurged.sum()


def test_the_embargo_actually_removes_rows():
    frame = _frame()
    from capitalscan.core.folds import Fold

    _, va_embargoed = train._fold_masks(
        frame, Fold(2010, 2014, 2015), calendar=_calendar(), horizon_days=10, embargo_days=30
    )
    _, va_none = train._fold_masks(
        frame, Fold(2010, 2014, 2015), calendar=_calendar(), horizon_days=10, embargo_days=0
    )
    assert va_embargoed.sum() < va_none.sum()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _result(head: str, year: int, model: float, base: float) -> train.HeadResult:
    return train.HeadResult(
        head=head,
        fold_validate_year=year,
        n_train=100,
        n_validate=20,
        model_loss=model,
        baseline_loss=base,
        best_iteration=42,
    )


def test_beating_the_baseline_requires_every_fold():
    """Not an average. A head winning four folds and losing three has shown
    nothing a fold ordering could not produce, and averaging is how that
    gets reported as a win."""
    report = train.FitReport(
        results=[
            _result("terminal_h5_q50", 2015, 0.010, 0.012),
            _result("terminal_h5_q50", 2016, 0.013, 0.012),
        ]
    )
    assert report.heads_beating_baseline() == {"terminal_h5_q50": False}


def test_a_head_winning_every_fold_is_reported_as_beating():
    report = train.FitReport(
        results=[
            _result("peak_h10_q05", 2015, 0.010, 0.012),
            _result("peak_h10_q05", 2016, 0.011, 0.012),
        ]
    )
    assert report.heads_beating_baseline() == {"peak_h10_q05": True}


def test_improvement_is_signed():
    """Negative when the model is worse. A magnitude-only figure would
    report a failing head as a small improvement."""
    assert _result("h", 2015, 0.009, 0.010).improvement == pytest.approx(0.1)
    assert _result("h", 2015, 0.011, 0.010).improvement == pytest.approx(-0.1)


# ---------------------------------------------------------------------------
# Quantile crossing
# ---------------------------------------------------------------------------


def test_crossing_quantiles_are_sorted():
    """Independent heads have no monotonicity constraint, so a fitted
    Q(0.25) can exceed Q(0.50)."""
    got = train.sort_quantiles(
        {0.25: np.array([0.9]), 0.50: np.array([0.2]), 0.75: np.array([0.5])}
    )
    assert got[0.25][0] == pytest.approx(0.2)
    assert got[0.50][0] == pytest.approx(0.5)
    assert got[0.75][0] == pytest.approx(0.9)


def test_already_monotone_quantiles_are_unchanged():
    got = train.sort_quantiles(
        {0.25: np.array([0.1]), 0.50: np.array([0.2]), 0.75: np.array([0.3])}
    )
    assert [got[t][0] for t in (0.25, 0.50, 0.75)] == pytest.approx([0.1, 0.2, 0.3])


def test_sorting_is_per_row():
    """Each event's fan is repaired independently; sorting the whole column
    would reorder events against each other."""
    got = train.sort_quantiles({0.25: np.array([0.9, 0.1]), 0.75: np.array([0.1, 0.9])})
    assert list(got[0.25]) == pytest.approx([0.1, 0.1])
    assert list(got[0.75]) == pytest.approx([0.9, 0.9])
