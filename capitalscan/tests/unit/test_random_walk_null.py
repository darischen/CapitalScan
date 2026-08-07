"""Statistical self-validation: the random-walk null and recovery tests
(DESIGN §6.13, ADR 087, TESTS.md §10 Phase 1).

The premise: run the **real** measurement code over data that provably
contains no signal, and confirm it reports none. A random walk that
produces an edge means the measurement layer has a bug, and every real
result computed by that layer is suspect. This is the one test that can
invalidate everything else, which is why ADR 087 makes it the primary
statistical guard rather than one check among many.

**What this file covers and what it does not.** DESIGN §6.13 states the
null test at the *cell* level — "the fraction of cells at `q < 0.05` must
not exceed 5%". That form needs `cell_stats`, BH correction, and q-values,
none of which exist yet (Phase 4 builds them; `research/` has no
statistics module as of 2026-08-06). What exists today is the layer
underneath: `core.returns.path_for_event` computes the forward path, and
`research.path_queries` derives reachability and terminal returns from it.
Those are the inputs every cell statistic will be built from, so a bug
there propagates into every cell — and they are testable now.

So this file runs the null and recovery tests against the label layer, and
Phase 4 extends the same generator to the cell layer. The tests here are
not a substitute for the cell-level check; they are the half that can run
before it exists. `docs/BUILD.md`'s Phase 4 section carries the remaining
half as an explicit task.

**Why symmetry is the sharpest assertion here.** Daily bars sample the
path at discrete points, so a discretely-monitored walk touches a barrier
strictly less often than the continuous reflection-principle value — the
barrier can be crossed and recrossed between two closes. That makes
"measured == analytical" the wrong assertion for reachability. But
discretization is direction-blind: it suppresses up-touches and
down-touches by the same factor. Symmetry therefore survives it exactly,
while a look-ahead leak, a `t` vs `t−1` error, or a sign bug in the
favorable/adverse split breaks it immediately. The continuous value is
still used, as a one-sided upper bound.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from capitalscan.core.returns import path_for_event
from capitalscan.core.types import Side
from capitalscan.research.path_queries import Direction, touched_by
from capitalscan.research.synthetic import (
    TRADING_DAYS_PER_YEAR,
    analytical_touch_probability,
    gbm_bars,
    synthetic_ticker_names,
)

# DESIGN §6.13's panel: 50 synthetic tickers, 2,500 days.
N_TICKERS = 50
N_DAYS = 2500
SIGMA_ANNUAL = 0.30
HORIZON = 10
TARGET = 0.05

# Entries are sampled every `STRIDE` days rather than every day. This is a
# statistical improvement, not only a speed one: consecutive entries share
# 9 of their 10 forward bars, so daily sampling inflates the nominal count
# roughly tenfold over the independent-sample count while adding no
# information. A stride at or above the horizon yields non-overlapping
# windows, so the nominal count *is* the effective count and the standard
# errors quoted in each test are the real ones.
STRIDE = 20

# `entry_offset = 0` is `EntryKind.TOUCH` (`core.returns.entry_offset_for`),
# and `path_for_event`'s `day_offset` is 1-based from the first forward
# bar. The two line up: `_window` asks for `[1, horizon]` and that is
# exactly what the path carries.
ENTRY_OFFSET = 0


def log_symmetric_down_target(up_target: float) -> float:
    """The down-move that is the mirror of `up_target` **in log space**.

    This function exists because the obvious version of the null test is
    wrong, and wrong in a way that passes under a loose tolerance.

    A driftless random walk is symmetric in log-price, not in percent. The
    barriers `+5%` and `-5%` are not mirror images: `log(1.05) = 0.04879`
    while `|log(0.95)| = 0.05129`, so the down barrier sits 5% farther
    away and a correct, unbiased measurement touches `+5%` *more often*
    than `-5%`. Measured on this panel: 32.40% vs 30.61%, a 1.79pp gap
    that is real arithmetic, not a defect.

    Asserting naive `P(+x) == P(-x)` therefore either fails on correct
    code or, with a tolerance wide enough to accommodate 1.79pp, stops
    being able to detect a genuine 1.5pp directional bug. The mirror of
    `+x` is `1 - 1/(1+x)`: at `x = 0.05` that is `0.047619`, the move that
    takes price to `S0 / 1.05`.
    """
    return 1.0 - 1.0 / (1.0 + up_target)


def _reach_counts(
    bars: pd.DataFrame,
    up_target: float = TARGET,
    down_target: float | None = None,
    horizon: int = HORIZON,
    stride: int = STRIDE,
) -> tuple[int, int, int]:
    """`(n_windows, n_touched_up, n_touched_down)` measured through the
    real path and reachability code, never a reimplementation.

    `down_target` defaults to `up_target`, i.e. the percent-symmetric
    pair. Pass `log_symmetric_down_target(up_target)` for the log-symmetric
    pair the null test needs.

    Every entry uses `Side.LONG`, so `favorable` is the up-move and
    `adverse` is the down-move, and the up/down asymmetry the null test
    looks for cannot be manufactured by the side argument itself.
    """
    if down_target is None:
        down_target = up_target
    n_windows = n_up = n_down = 0
    for ticker in synthetic_ticker_names(bars["ticker"].nunique()):
        one = bars.loc[bars["ticker"] == ticker].reset_index(drop=True)
        closes = one["close"].to_numpy()
        for i in range(0, len(one) - horizon - 1, stride):
            fwd = one.iloc[i + 1 : i + 1 + horizon]
            path = path_for_event(entry_price=float(closes[i]), side=Side.LONG, fwd_bars=fwd)
            up = touched_by(path, up_target, horizon, Direction.FAVORABLE, ENTRY_OFFSET)
            down = touched_by(path, down_target, horizon, Direction.ADVERSE, ENTRY_OFFSET)
            n_windows += 1
            n_up += bool(up)
            n_down += bool(down)
    return n_windows, n_up, n_down


def _terminal_returns(
    bars: pd.DataFrame, horizon: int = HORIZON, stride: int = STRIDE
) -> np.ndarray:
    """Terminal return at `horizon`, read off the real path frame."""
    out: list[float] = []
    for ticker in synthetic_ticker_names(bars["ticker"].nunique()):
        one = bars.loc[bars["ticker"] == ticker].reset_index(drop=True)
        closes = one["close"].to_numpy()
        for i in range(0, len(one) - horizon - 1, stride):
            fwd = one.iloc[i + 1 : i + 1 + horizon]
            path = path_for_event(entry_price=float(closes[i]), side=Side.LONG, fwd_bars=fwd)
            terminal = path.loc[path["day_offset"] == horizon, "terminal"]
            if not terminal.empty:
                out.append(float(terminal.iloc[0]))
    return np.asarray(out)


@pytest.fixture(scope="module")
def null_bars() -> pd.DataFrame:
    """The null panel: driftless in log space, no intraday range."""
    return gbm_bars(
        n_tickers=N_TICKERS,
        n_days=N_DAYS,
        sigma_annual=SIGMA_ANNUAL,
        mu_annual=0.0,
    )


@pytest.fixture(scope="module")
def null_reach(null_bars) -> tuple[int, int, int]:
    """One sweep at log-symmetric barriers, shared by the tests that need
    the unbiased comparison.

    Module-scoped because the sweep is ~10s and several tests assert
    different properties of the same counts — recomputing would pay the
    cost again to prove the same numbers twice.
    """
    return _reach_counts(null_bars, down_target=log_symmetric_down_target(TARGET))


@pytest.fixture(scope="module")
def null_reach_percent(null_bars) -> tuple[int, int, int]:
    """One sweep at the percent-symmetric pair (+5% / -5%), which is the
    naive and slightly biased comparison. Used only to pin that the bias
    is the size theory says it is."""
    return _reach_counts(null_bars)


# ---------------------------------------------------------------------------
# The generator itself must be beyond suspicion before anything is
# concluded from what the measurement code says about its output.
# ---------------------------------------------------------------------------


def test_generator_is_deterministic():
    """ADR 060: identical arguments, identical frame."""
    a = gbm_bars(n_tickers=3, n_days=50)
    b = gbm_bars(n_tickers=3, n_days=50)
    pd.testing.assert_frame_equal(a, b)


def test_generator_ticker_path_does_not_depend_on_panel_size():
    """Ticker `i` is seeded from `seed + i`, so widening the panel must not
    reshuffle the tickers already in it. Without this, every measurement
    below would silently change whenever `N_TICKERS` moved, and a
    regression would look like a statistical fluctuation."""
    narrow = gbm_bars(n_tickers=2, n_days=50)
    wide = gbm_bars(n_tickers=8, n_days=50)
    for ticker in ("SYN000", "SYN001"):
        pd.testing.assert_frame_equal(
            narrow.loc[narrow["ticker"] == ticker].reset_index(drop=True),
            wide.loc[wide["ticker"] == ticker].reset_index(drop=True),
        )


def test_generator_is_driftless_in_log_space(null_bars):
    """Mean log-return must sit inside its own sampling error of zero.

    A generator with an accidental drift would make the null test measure
    a real effect and then correctly report it, which reads as a
    measurement bug when it is a fixture bug.
    """
    log_returns = np.concatenate(
        [
            np.diff(np.log(null_bars.loc[null_bars["ticker"] == t, "close"].to_numpy()))
            for t in synthetic_ticker_names(N_TICKERS)
        ]
    )
    sigma_daily = SIGMA_ANNUAL / np.sqrt(TRADING_DAYS_PER_YEAR)
    standard_error = sigma_daily / np.sqrt(len(log_returns))
    assert abs(log_returns.mean()) < 4 * standard_error


def test_generator_realized_volatility_matches_the_requested_sigma(null_bars):
    log_returns = np.concatenate(
        [
            np.diff(np.log(null_bars.loc[null_bars["ticker"] == t, "close"].to_numpy()))
            for t in synthetic_ticker_names(N_TICKERS)
        ]
    )
    realized_annual = log_returns.std(ddof=0) * np.sqrt(TRADING_DAYS_PER_YEAR)
    assert realized_annual == pytest.approx(SIGMA_ANNUAL, rel=0.02)


# ---------------------------------------------------------------------------
# Null test (DESIGN §6.13)
# ---------------------------------------------------------------------------


def test_null_reachability_is_symmetric_in_log_space(null_reach):
    """**The null test.** On a driftless walk, touching a log-barrier `+a`
    and touching `-a` are the same event mirrored, so the real
    path/reachability code must report them at the same rate.

    Any look-ahead leak, any `t` vs `t−1` slip, any sign error in the
    favorable/adverse split shows up here as an asymmetry, and none of
    those is hidden by discrete monitoring — which suppresses both
    directions by the same factor. That direction-blindness is what makes
    symmetry a sharper assertion than any absolute rate.

    The barriers are `+5%` and `log_symmetric_down_target(0.05)` =
    `4.7619%`, not `±5%`. See that function for why the percent-symmetric
    pair is biased by a real 1.79pp on correct code.

    Tolerance: ~6,250 non-overlapping windows at p near 0.32 gives a
    standard error near 0.6pp on each proportion. The two are measured on
    the same paths, so the difference's error is below the 0.83pp
    independent value. 1.5pp is roughly 2.5 sigma of that — half the
    tolerance the naive test needed, and now every bit of it is budget for
    real bugs rather than for a known arithmetic effect.
    """
    n, n_up, n_down = null_reach
    p_up, p_down = n_up / n, n_down / n
    assert abs(p_up - p_down) < 0.015, (
        f"driftless walk is directionally biased at log-symmetric barriers: "
        f"P(up)={p_up:.4f} vs P(down)={p_down:.4f} over {n} windows"
    )


def test_percent_symmetric_barriers_are_biased_by_the_amount_theory_predicts(
    null_reach_percent,
):
    """Pins the trap that `log_symmetric_down_target` exists to avoid.

    At `±5%` the up-touch rate must exceed the down-touch rate, because
    `log(1.05) < |log(0.95)|` puts the down barrier farther away. The
    reflection principle gives the continuous gap:

        erfc(log(1.05) / (s*sqrt(2))) - erfc(|log(0.95)| / (s*sqrt(2)))

    Discrete monitoring shrinks both rates, so the measured gap lands
    below the continuous one while keeping its sign. Asserting the sign
    and the ordering — rather than a fitted number — keeps this a
    statement about arithmetic rather than about this particular seed.

    If this test ever fails, the naive `P(+x) == P(-x)` intuition has
    become true, which would mean the reachability code stopped
    distinguishing the two barriers.
    """
    n, n_up, n_down = null_reach_percent
    p_up, p_down = n_up / n, n_down / n
    # The `-5%` barrier sits `|log(0.95)|` away in log space. The up-target
    # with that same log distance is `1/(1 - 0.05) - 1`, which is what
    # `analytical_touch_probability` has to be evaluated at to price the
    # down side on the same footing.
    down_equivalent_up_target = 1.0 / (1.0 - TARGET) - 1.0
    continuous_gap = analytical_touch_probability(
        TARGET, SIGMA_ANNUAL, HORIZON
    ) - analytical_touch_probability(down_equivalent_up_target, SIGMA_ANNUAL, HORIZON)
    measured_gap = p_up - p_down
    assert continuous_gap > 0
    assert 0 < measured_gap < continuous_gap


def test_null_reachability_does_not_exceed_the_continuous_bound(null_reach):
    """Discrete daily monitoring can only touch a barrier *less* often than
    continuous monitoring of the same walk. A measured rate above the
    reflection-principle value is therefore impossible for correct code,
    and is the signature of a path that peeks past its window.

    One-sided on purpose: the gap below the bound is a real discretization
    effect whose size is not worth pinning, but the bound itself cannot be
    crossed from below by any legitimate mechanism.
    """
    analytical = analytical_touch_probability(TARGET, SIGMA_ANNUAL, HORIZON)
    n, n_up, n_down = null_reach
    assert n_up / n <= analytical
    assert n_down / n <= analytical


def test_null_terminal_direction_is_a_coin_flip(null_bars):
    """The parametric baseline on a driftless walk is exactly 0.50.

    This is the null half of the recovery test below, and it isolates a
    different failure than reachability does: `terminal` is a single mark
    rather than a running extreme, so an off-by-one in `day_offset` moves
    this without necessarily moving the touch rates.
    """
    terminal = _terminal_returns(null_bars)
    measured = float((terminal > 0).mean())
    assert measured == pytest.approx(0.50, abs=0.01)


# ---------------------------------------------------------------------------
# Recovery test (DESIGN §6.13): known drift in, analytical value out,
# within 1 percentage point.
# ---------------------------------------------------------------------------


def test_recovery_known_drift_matches_the_analytical_baseline():
    """A null test alone cannot distinguish "correctly finds nothing" from
    "finds nothing, ever". This is the other side: inject a drift whose
    effect is known in closed form and confirm the same code recovers it.

    With iid log-returns `N(mu_d, sigma_d^2)`, the terminal log-return over
    `h` days is `N(mu_d*h, sigma_d^2*h)`, so

        P(S_h > S_0) = Phi( mu_d * sqrt(h) / sigma_d )

    DESIGN §6.13 sets the tolerance at 1 percentage point.
    """
    mu_annual = 0.30
    drifted = gbm_bars(
        n_tickers=N_TICKERS,
        n_days=N_DAYS,
        sigma_annual=SIGMA_ANNUAL,
        mu_annual=mu_annual,
        seed=20260807,
    )

    sigma_daily = SIGMA_ANNUAL / np.sqrt(TRADING_DAYS_PER_YEAR)
    mu_daily = mu_annual / TRADING_DAYS_PER_YEAR
    analytical = float(norm.cdf(mu_daily * np.sqrt(HORIZON) / sigma_daily))

    terminal = _terminal_returns(drifted)
    measured = float((terminal > 0).mean())

    # The drift has to be large enough that recovering it is a real
    # result rather than noise around 0.50.
    assert analytical > 0.55
    assert measured == pytest.approx(analytical, abs=0.01)
