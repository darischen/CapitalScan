"""Session 13's gate, checked against what was actually written.

`test_arms.py` and `test_benchmarks_arms.py` prove the arithmetic and the
windowing on synthetic inputs. This file proves the *stored* result: the
session gate is written in terms of rows in `benchmarks`, and reading them
back is the only version of the check that catches a writer dropping a
replication or a percentile computed off an in-memory list.

**Read-only against the live database, plus one `--no-write` re-run.** No
`INSERT`, no `DELETE`, no `TRUNCATE`, safe beside a live poller. The
determinism check runs `run_benchmarks(write=False)` twice on the smaller
`validate` split, which touches nothing.

    uv run pytest capitalscan/tests/integration/test_benchmarks_measured.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core import arms as core_arms
from capitalscan.core.config import BenchmarkParams, Config
from capitalscan.jobs import db_io
from capitalscan.research import benchmarks

LIVE_CONFIG_HASH = "1835688bf7d760ba"
GATE_SPLIT = "train"
BM = BenchmarkParams()

# Every arm DESIGN §6.10 names. Eight, and the gate counts them.
EXPECTED_ARMS = {
    core_arms.ARM_BUY_HOLD,
    core_arms.ARM_RANDOM,
    core_arms.ARM_SIGNAL,
    core_arms.ARM_TRIM,
    *core_arms.DCA_ARMS,
}

# The determinism re-run's replication count. Small on purpose: determinism
# is a property of the seeding, not of the sample size, and 5 replications
# prove it in seconds where 200 would take half an hour.
DETERMINISM_REPLICATIONS = 5


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
def live_data() -> None:
    """Skip unless this database holds bars and events for the live config.

    **CI's slow tier runs this file against a freshly-migrated, empty
    Postgres service container.** `_db_reachable` is true there, so every
    test in this module is collected and run. The row-reading tests skip via
    `gate_run`, but `run_benchmarks` reads `bars` directly and raises
    `ValueError: no daily bars ...` on an empty database — a hard failure in
    CI for a missing fixture rather than a defect.
    """
    engine = db_io.get_engine()
    with engine.connect() as conn:
        has_bars = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM bars WHERE interval = '1d')")
        ).scalar()
        has_events = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM events WHERE config_hash = :c)"),
            {"c": LIVE_CONFIG_HASH},
        ).scalar()
    if not has_bars or not has_events:
        pytest.skip(
            f"this database holds no bars/events for config_hash={LIVE_CONFIG_HASH}. "
            "Expected on a fresh CI container; the arms need an ingested universe."
        )


@pytest.fixture(scope="module")
def gate_run() -> str:
    """The most recent `benchmarks` run for the live config's train split.

    Skips with an actionable message rather than failing when nothing has
    been written — the same pattern `test_cell_grid_measured.py`'s fixture
    uses, and for the same reason: a missing prerequisite is not a defect in
    the code under test.
    """
    engine = db_io.get_engine()
    with engine.connect() as conn:
        run_id = conn.execute(
            text(
                "SELECT run_id FROM benchmarks WHERE config_hash = :c AND split_key = :s "
                "ORDER BY computed_at DESC LIMIT 1"
            ),
            {"c": LIVE_CONFIG_HASH, "s": GATE_SPLIT},
        ).scalar()
    if run_id is None:
        pytest.skip(
            f"no benchmarks rows for config_hash={LIVE_CONFIG_HASH} split={GATE_SPLIT}. "
            f"Populate with:\n    uv run cscan stats benchmarks "
            f"--config-hash {LIVE_CONFIG_HASH} --split-key {GATE_SPLIT}"
        )
    return str(run_id)


@pytest.fixture(scope="module")
def rows(gate_run: str) -> pd.DataFrame:
    engine = db_io.get_engine()
    with engine.connect() as conn:
        frame = pd.read_sql(
            text("SELECT * FROM benchmarks WHERE run_id = :r"), conn, params={"r": gate_run}
        )
    for column in (
        "total_ret",
        "annualized_ret",
        "sharpe",
        "max_drawdown",
        "frac_deployed",
        "capital_efficiency",
        "win_rate",
        "terminal_value",
        "irr",
        "avg_cost_basis",
        "cash_drag",
        "capital_undeployed",
        "avg_days_in_cash",
        "pre_tax_ret",
        "post_tax_ret",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _pooled(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.loc[rows["era"] == benchmarks.POOLED_ERA]


def _subset(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.loc[rows["era"] == benchmarks.HIGH_BREADTH_ERA]


# --------------------------------------------------------------------------
# Gate item 1: all eight arms, identical universe and dates
# --------------------------------------------------------------------------


def test_all_eight_arms_are_written(rows):
    assert set(_pooled(rows)["arm"]) == EXPECTED_ARMS


def test_every_row_shares_one_config_hash_and_split(rows):
    """ADR 012's identical-universe-and-dates rule, asserted rather than
    assumed. Every arm in one run is one window by construction, and this is
    the assertion that says so."""
    assert set(rows["config_hash"]) == {LIVE_CONFIG_HASH}
    assert set(rows["split_key"]) == {GATE_SPLIT}


def test_every_row_carries_run_id_and_git_sha(rows):
    """Invariant 6."""
    assert rows["run_id"].notna().all()
    assert rows["git_sha"].notna().all()
    assert (rows["git_sha"].str.len() > 0).all()


# --------------------------------------------------------------------------
# Gate item 2: exactly 200 rows, replication 1 through 200
# --------------------------------------------------------------------------


def test_the_null_holds_exactly_the_configured_replications(rows):
    """No gaps, no duplicates. A dropped replication changes the tail, which
    is exactly where the 97.5th percentile lives."""
    null = _pooled(rows).loc[rows["arm"] == core_arms.ARM_RANDOM]
    expected = Config().stats.n_replications_default
    assert sorted(null["replication"].astype(int)) == list(range(1, expected + 1))


def test_replication_is_null_on_every_arm_but_the_random_one(rows):
    """DESIGN §6.10. A populated `replication` elsewhere would pull rows
    that are not part of the null into the distribution query."""
    others = rows.loc[rows["arm"] != core_arms.ARM_RANDOM]
    assert others["replication"].isna().all()


def test_the_null_actually_has_a_distribution(rows):
    """200 identical replications would have a 97.5th percentile equal to
    their median, and the significance test would be meaningless."""
    null = _pooled(rows).loc[rows["arm"] == core_arms.ARM_RANDOM, "total_ret"]
    assert null.nunique() > 100
    assert float(null.std()) > 0


# --------------------------------------------------------------------------
# Gate item 4: the percentile, read off the stored rows
# --------------------------------------------------------------------------


def test_the_percentile_is_computed_from_the_stored_rows(gate_run, rows):
    """13.2 acceptance. `load_null_distribution` queries the table; this
    asserts it returns the same 200 values the table holds."""
    engine = db_io.get_engine()
    loaded = benchmarks.load_null_distribution(engine, gate_run, LIVE_CONFIG_HASH, GATE_SPLIT)
    stored = _pooled(rows).loc[rows["arm"] == core_arms.ARM_RANDOM].sort_values("replication")
    assert loaded == pytest.approx(stored["total_ret"].tolist())


def test_the_signal_arms_position_against_the_null_is_computable(gate_run, rows):
    """The gate is that the number is computed and recorded, **not** that
    the signal wins. A gate requiring a favorable result is not a gate, so
    this asserts only that the comparison resolves to a real boolean."""
    engine = db_io.get_engine()
    null = benchmarks.load_null_distribution(engine, gate_run, LIVE_CONFIG_HASH, GATE_SPLIT)
    signal = float(_pooled(rows).loc[rows["arm"] == core_arms.ARM_SIGNAL, "total_ret"].iloc[0])
    threshold = core_arms.null_percentile(null, BM.null_percentile)
    assert threshold is not None
    assert core_arms.exceeds_null(signal, null, BM.null_percentile) in (True, False)


def test_the_null_is_built_on_the_same_footing_as_the_signal_arm(rows):
    """13.2 acceptance: "a null wildly off is a construction bug, and this is
    the cheapest way to see it."

    **The brief's version of this check does not apply here, and the test
    below is the replacement.** The brief asks that the null's median land
    near buy-and-hold scaled by `frac_deployed`. Measured on the train split
    that predicts 3.71 against an actual 0.84 — but it predicts the same
    3.71 for the **signal arm**, which returned 1.08 on the identical exit
    machinery. The heuristic assumes an entry arm captures the market's
    return while deployed, and a 4% target with a 5-day maximum hold
    truncates every winner, so no entry arm can compound like buy-and-hold
    regardless of when it enters. The heuristic measures the exit rules, not
    the null's construction, and it fails for both arms together.

    What actually discriminates a broken null is whether it matches the
    signal arm on everything except entry timing — which is what the null
    isolates. A wrong firing rate, entries drawn outside the trade universe,
    or a different exit path all move at least one of these.
    """
    pooled = _pooled(rows)
    signal = pooled.loc[pooled["arm"] == core_arms.ARM_SIGNAL].iloc[0]
    null = pooled.loc[pooled["arm"] == core_arms.ARM_RANDOM]

    # Same firing rate: every replication opens exactly as many positions as
    # the signal arm did. This is the sharp one.
    assert set(null["n_trades"].astype(int)) == {int(signal["n_trades"])}

    # Same exposure and roughly the same hit rate, since only entry timing
    # differs.
    assert float(null["frac_deployed"].median()) == pytest.approx(
        float(signal["frac_deployed"]), abs=0.10
    )
    assert float(null["win_rate"].median()) == pytest.approx(float(signal["win_rate"]), abs=0.10)

    # And the same order of magnitude of return. A null off by 10x is the
    # construction bug this check exists to catch.
    median = float(null["total_ret"].median())
    assert 0.2 < (1.0 + median) / (1.0 + float(signal["total_ret"])) < 5.0


def test_the_brief_heuristic_fails_for_the_signal_arm_too(rows):
    """The control for the test above. If buy-and-hold scaled by deployment
    ever *does* predict the signal arm's return, the exit-truncation
    explanation is wrong and the replacement check needs revisiting."""
    pooled = _pooled(rows)
    hold = float(pooled.loc[pooled["arm"] == core_arms.ARM_BUY_HOLD, "total_ret"].iloc[0])
    signal = pooled.loc[pooled["arm"] == core_arms.ARM_SIGNAL].iloc[0]
    predicted = hold * float(signal["frac_deployed"])
    assert float(signal["total_ret"]) < 0.5 * predicted


# --------------------------------------------------------------------------
# Gate item 5: deployment and capital efficiency
# --------------------------------------------------------------------------


def test_buy_and_hold_is_deployed_the_whole_window(rows):
    for _, row in rows.loc[rows["arm"] == core_arms.ARM_BUY_HOLD].iterrows():
        assert float(row["frac_deployed"]) == pytest.approx(1.0)


def test_capital_efficiency_is_finite_for_every_arm(rows):
    values = rows["capital_efficiency"].to_numpy(dtype="float64")
    assert np.isfinite(values).all()


def test_capital_efficiency_equals_total_return_over_deployment(rows):
    """ADR 012's definition, checked against the stored pair rather than
    recomputed from the same code that wrote it."""
    frame = rows.loc[rows["frac_deployed"] > 0]
    expected = frame["total_ret"] / frame["frac_deployed"]
    assert frame["capital_efficiency"].to_numpy() == pytest.approx(expected.to_numpy())


# --------------------------------------------------------------------------
# Gate item 6: DCA deploys exactly C
# --------------------------------------------------------------------------


def test_every_dca_variant_reports_its_undeployed_capital(rows):
    dca = rows.loc[rows["arm"].isin(core_arms.DCA_ARMS)]
    assert len(dca) == len(core_arms.DCA_ARMS)
    assert dca["capital_undeployed"].notna().all()


def test_lump_sum_leaves_nothing_undeployed(rows):
    lump = rows.loc[rows["arm"] == core_arms.ARM_DCA_LUMP]
    assert float(lump["capital_undeployed"].iloc[0]) == pytest.approx(0.0, abs=0.01)


def test_lump_sum_and_buy_and_hold_agree_on_terminal_value(rows):
    """13.4's cross-arm consistency check on the real basket. Lump sum is
    buy-and-hold with a dollar amount attached, so a disagreement means one
    of the two simulators is wrong."""
    pooled = _pooled(rows)
    lump = float(pooled.loc[pooled["arm"] == core_arms.ARM_DCA_LUMP, "terminal_value"].iloc[0])
    hold = float(pooled.loc[pooled["arm"] == core_arms.ARM_BUY_HOLD, "terminal_value"].iloc[0])
    assert lump == pytest.approx(hold, rel=1e-9)


def test_every_dca_variant_reports_an_irr(rows):
    dca = rows.loc[rows["arm"].isin(core_arms.DCA_ARMS)]
    assert dca["irr"].notna().all()


def test_cash_drag_is_zero_for_lump_sum(rows):
    lump = rows.loc[rows["arm"] == core_arms.ARM_DCA_LUMP]
    assert float(lump["cash_drag"].iloc[0]) == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------
# Gate item 7 (13.5): tax
# --------------------------------------------------------------------------


def test_post_tax_never_exceeds_pre_tax(rows):
    assert (rows["post_tax_ret"] <= rows["pre_tax_ret"] + 1e-12).all()


def test_the_wash_sale_flag_is_populated_on_every_row(rows):
    """A null flag is neither "flagged" nor "not flagged" and would read as
    whichever the consumer defaults to."""
    assert rows["wash_sale_flagged"].notna().all()


# --------------------------------------------------------------------------
# Gate item 8 (13.6): the high-breadth subset
# --------------------------------------------------------------------------


def test_the_subset_runs_all_three_arms(rows):
    """ADR 099 requires the three-arm comparison on the high-breadth subset
    alone, reported **alongside** the pooled one, never instead of it."""
    subset = _subset(rows)
    assert set(subset["arm"]) == {
        core_arms.ARM_BUY_HOLD,
        core_arms.ARM_SIGNAL,
        core_arms.ARM_RANDOM,
    }
    assert not _pooled(rows).empty


def test_the_subset_null_holds_the_same_replication_count(rows):
    subset_null = _subset(rows).loc[rows["arm"] == core_arms.ARM_RANDOM]
    expected = Config().stats.n_replications_default
    assert sorted(subset_null["replication"].astype(int)) == list(range(1, expected + 1))


def test_the_subset_shares_the_pooled_split_key(rows):
    """The subset narrows the entry population, not the dates. Overloading
    `split_key` would make the firewall query ambiguous."""
    assert set(_subset(rows)["split_key"]) == {GATE_SPLIT}


def test_the_subset_buy_and_hold_matches_the_pooled_one(rows):
    """ "Buy-and-hold restricted to the same dates" (13.6 rule). Same dates
    and same universe means the same number, and a different one would mean
    the subset silently moved the window."""
    pooled = _pooled(rows)
    subset = _subset(rows)
    a = float(pooled.loc[pooled["arm"] == core_arms.ARM_BUY_HOLD, "total_ret"].iloc[0])
    b = float(subset.loc[subset["arm"] == core_arms.ARM_BUY_HOLD, "total_ret"].iloc[0])
    assert a == pytest.approx(b)


def test_the_subset_event_count_matches_the_top_breadth_tercile(live_data, rows):
    """13.6 acceptance. Recomputed from `events` through the same
    `assign_breadth_tercile` Session 12 used, so a change to the tercile cut
    shows up here rather than silently changing which days the subset
    measures."""
    engine = db_io.get_engine()
    from capitalscan.core.config import ReportingParams
    from capitalscan.core.types import Side

    window = benchmarks.load_window(engine, Config(), GATE_SPLIT)
    events = benchmarks.restrict_to_universe(
        benchmarks.load_signal_events(engine, LIVE_CONFIG_HASH, GATE_SPLIT, Side.LONG), window
    )
    labelled = benchmarks.assign_breadth_tercile(events, ReportingParams())
    n_high = int((labelled["breadth_tercile"] == benchmarks.HIGH_BREADTH_TERCILE).sum())

    subset_signal = _subset(rows).loc[rows["arm"] == core_arms.ARM_SIGNAL]
    assert n_high > 0
    assert int(subset_signal["n_trades"].iloc[0]) <= n_high


# --------------------------------------------------------------------------
# Gate item 9: determinism
# --------------------------------------------------------------------------


def test_two_runs_produce_identical_output_ignoring_run_identifiers(live_data):
    """Gate item 9, on the smaller `validate` split with `write=False`.

    Determinism is a property of the seeding and the simulator, not of the
    replication count, so five replications prove it. Nothing is written.
    """
    engine = db_io.get_engine()
    identity = ["run_id", "computed_at", "git_sha"]
    first, _ = benchmarks.run_benchmarks(
        engine,
        LIVE_CONFIG_HASH,
        "validate",
        replications=DETERMINISM_REPLICATIONS,
        run_id="determinism-a",
        git_sha="a" * 40,
        write=False,
    )
    second, _ = benchmarks.run_benchmarks(
        engine,
        LIVE_CONFIG_HASH,
        "validate",
        replications=DETERMINISM_REPLICATIONS,
        run_id="determinism-b",
        git_sha="b" * 40,
        write=False,
    )
    pd.testing.assert_frame_equal(first.drop(columns=identity), second.drop(columns=identity))
