"""The headline grid measured against real events (Session 12.2).

This is the load-bearing test of Session 12. It is the only thing
connecting the code to the measurement the grid design rests on: if
`n_eff` here disagrees with ADR 102's table, either the writer is wrong or
the grid design rests on a miscalculation, and both need resolving before
Session 13.

**Read-only.** `SELECT` only, no writes of any kind, safe to run beside a
live poller.

    uv run pytest capitalscan/tests/integration/test_cell_grid_measured.py

**The population is not stated in ADR 102 and was recovered by
measurement** on 2026-08-11: `is_cluster_head AND entry_kind = 'next_open'`
with a closed forward window. Without the cluster-head filter every `n` is
roughly 4x too large; without the entry-kind filter, 4x again. Those two
filters are `GRID_CLUSTER_HEADS_ONLY` and `GRID_ENTRY_KIND`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core.cells import headline_grid
from capitalscan.core.config import Config
from capitalscan.jobs import db_io
from capitalscan.research.cell_stats import (
    cell_n_eff,
    load_grid_events,
    load_rho_era,
    min_fwd_window_for,
    select_grid_events,
)

LIVE_CONFIG_HASH = "1835688bf7d760ba"

# ADR 102's published table: (side, signal_type, dd_bucket) -> (n, k_bar, n_eff)
ADR_102 = {
    ("short", "bb_upper_touch", "0-10"): (4116, 12.57, 717),
    ("long", "bb_lower_touch", "0-10"): (3593, 22.40, 369),
    ("short", "stoch_overbought", "0-10"): (5266, 38.37, 322),
    ("short", "confluence_high", "0-10"): (2986, 32.12, 215),
    ("long", "stoch_oversold", "0-10"): (1444, 22.67, 147),
    ("long", "bb_lower_touch", "10-20"): (775, 23.91, 74),
    ("long", "confluence_low", "0-10"): (725, 30.34, 56),
    ("long", "stoch_oversold", "10-20"): (900, 42.06, 51),
    ("short", "bb_upper_touch", "10-20"): (432, 20.02, 48),
    ("long", "confluence_low", "10-20"): (620, 36.56, 40),
    ("short", "stoch_overbought", "10-20"): (371, 60.53, 14),
    ("short", "confluence_high", "10-20"): (139, 64.61, 5),
}

# ADR 102's `n_eff` column was computed with rounded intermediates: its
# `k_bar` and `rho` are printed to 2 and 3 decimals and were reused at that
# precision. Recomputing from full precision moves three cells by exactly
# one unit (74->73, 51->50, 40->39). The tolerance covers that and nothing
# larger; it is deliberately too tight to absorb a wrong filter, which
# would move `n` by a factor of four.
N_EFF_TOLERANCE = 2

SUPPRESSED_CELLS = {
    ("short", "stoch_overbought", "10-20"),
    ("short", "confluence_high", "10-20"),
}


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture(scope="module")
def measured():
    """`(side, signal_type, dd_bucket) -> (n, k_bar, n_eff)` from the live
    config's train split."""
    engine = db_io.get_engine()
    cfg = Config()
    events = load_grid_events(engine, LIVE_CONFIG_HASH, "train")
    rho_era = load_rho_era(engine, LIVE_CONFIG_HASH)
    selected, report = select_grid_events(events, cfg.stats, min_fwd_window=min_fwd_window_for(cfg))

    out = {}
    for spec in headline_grid(cfg.stats):
        cell = selected.loc[
            (selected["side"] == spec.side)
            & (selected["signal_type"] == spec.signal_type)
            & (selected["dd_bucket"] == spec.dd_bucket)
        ]
        k_bar, rho_bar, n_eff = cell_n_eff(cell, rho_era)
        out[(spec.side, spec.signal_type, spec.dd_bucket)] = (len(cell), k_bar, n_eff, rho_bar)
    return out, report


def test_rho_era_prerequisite_is_populated(measured):
    """The session brief lists four `rho_era` rows under the live config as
    a prerequisite. `pooled_rho` raises without them, so this failing first
    is a clearer signal than twelve cells failing together."""
    engine = db_io.get_engine()
    rho_era = load_rho_era(engine, LIVE_CONFIG_HASH)
    assert set(rho_era["era"]) >= {"2010-2014", "2015-2019", "2020-2023"}


def test_exactly_twelve_cells_are_measured(measured):
    out, _ = measured
    assert len(out) == 12


@pytest.mark.parametrize("key", sorted(ADR_102))
def test_n_matches_adr_102_exactly(measured, key):
    """`n` is an exact count, so it gets no tolerance. A mismatch here
    means the population filter changed."""
    out, _ = measured
    assert out[key][0] == ADR_102[key][0]


@pytest.mark.parametrize("key", sorted(ADR_102))
def test_k_bar_matches_adr_102(measured, key):
    out, _ = measured
    assert out[key][1] == pytest.approx(ADR_102[key][1], abs=0.05)


@pytest.mark.parametrize("key", sorted(ADR_102))
def test_n_eff_reproduces_adr_102_within_tolerance(measured, key):
    out, _ = measured
    assert out[key][2] == pytest.approx(ADR_102[key][2], abs=N_EFF_TOLERANCE)


def test_exactly_two_cells_suppress(measured):
    """ADR 102 predicts two. A third suppressing, or either of these
    rendering, means something changed and needs explaining before Session
    13 (session brief 12.2)."""
    out, _ = measured
    cfg = Config()
    suppressed = {key for key, value in out.items() if value[2] < cfg.stats.min_n_eff}
    assert suppressed == SUPPRESSED_CELLS


def test_the_two_suppressed_cells_are_nowhere_near_the_floor(measured):
    """They suppress at roughly 14 and 5 against a floor of 30. Recording
    the distance is what makes 'lower the floor' visibly a bad trade rather
    than a near miss."""
    out, _ = measured
    assert out[("short", "stoch_overbought", "10-20")][2] < 20
    assert out[("short", "confluence_high", "10-20")][2] < 10


def test_pooled_rho_lands_in_the_measured_band(measured):
    """ADR 102's `rho` column runs 0.406 to 0.423 across the twelve. A
    pooled value outside that band means the event-count weighting broke,
    which moves `n_eff` without moving `n`."""
    out, _ = measured
    for key, value in out.items():
        assert 0.35 <= value[3] <= 0.50, f"{key} pooled rho {value[3]}"


def test_exclusions_are_counted_and_non_trivial(measured):
    """Null `dd_bucket` events exist in the live data — roughly 90 on the
    short side per ADR 104 — so a zero here means the filter is not
    running rather than that the data is clean."""
    _, report = measured
    assert report.excluded_null_dd > 0
    assert report.excluded_deep_dd > 0
    assert "dd_bucket" in report.summary()
