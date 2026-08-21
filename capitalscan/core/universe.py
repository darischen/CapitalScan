"""Trade-universe health filter. Pure functions, no IO (DESIGN §3.1).

The five ADR 014 criteria are returned **separately** rather than reduced to
one boolean, and `is_tradeable` accepts a subset of required criteria. That
pair is what makes the ablation study a config change rather than a code
change (DESIGN §3.10).

Three-valued by design. A criterion is `None` when the data needed to judge
it is absent — fewer than four reported quarters for revenue growth, a
sector with too few members for a median, a name inside its SMA warmup.
`is_tradeable` treats `None` as failing, but the two stay distinguishable so
the audit log can separate "failed the filter" from "could not be judged"
(DESIGN §4.6).

Every criterion is evaluated on information available at t-1. This module
never sees a clock; the caller supplies the as-of row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd

from capitalscan.core.config import UniverseParams
from capitalscan.core.signals import _isnan

# The five ADR 014 criteria, matching the `universe` table's crit_* columns
# exactly (DESIGN §2.5). No translation layer.
CRITERIA: tuple[str, ...] = (
    "crit_mcap",
    "crit_above_sma200",
    "crit_sma200_slope",
    "crit_rel_return",
    "crit_rev_growth",
)


def _cmp(left: object, right: object, strict: bool = True) -> bool | None:
    """`left > right`, or None when either side is missing."""
    if _isnan(left) or _isnan(right):
        return None
    lhs, rhs = float(left), float(right)  # type: ignore[arg-type]
    return lhs > rhs if strict else lhs >= rhs


def adr_adjusted_shares(ticker: str, shares: float | None, up: UniverseParams) -> float | None:
    """Ordinary share count converted to ADR-equivalent, for pricing.

    An ADR's Form 20-F reports the issuer's **ordinary** shares while the
    bar price is per **ADR**, so multiplying the two directly overstates
    market cap by the ADR ratio. TSM filed 25,932,524,521 ordinary shares
    against 5,186,474,013 ADRs — exactly 5:1 — which priced it at $10.5T
    against an actual ~$2.1T.

    `crit_mcap` survived that unharmed, since TSM clears the $200B
    threshold at either figure. `mcap_usd` and `mcap_rank` did not: both
    are stored on every event as context tags, so anything conditioning on
    company size inherited a 5x error on that ticker.

    A ticker absent from the map is 1:1 — that covers every ordinary US
    listing and the 1:1 ADRs alike, so being an ADR is not itself a
    correction. `None` passes through unchanged rather than becoming 0.0,
    because "no filing yet" is not "no shares" (invariant 4).
    """
    if shares is None:
        return None
    ratio = dict(up.adr_ordinary_per_adr).get(ticker.upper(), 1.0)
    return shares / ratio


def split_adjusted_shares(shares: float | None, ratios: Sequence[float]) -> float | None:
    """As-filed share count restated onto the price series' split basis.

    `bars.close` is split-adjusted, and Yahoo re-adjusts the **whole**
    history whenever a new split lands, so every close is expressed in
    today's share basis. `shares_outstanding` holds the count as filed. The
    two agree only while no split has occurred since the filing, and
    multiplying them regardless understates market cap by exactly the
    cumulative factor.

    Measured 2026-08-21, AAPL at `as_of` 2011-06-30: $11.1B against a real
    ~$310B, a ratio of 28 = 7 (2014-06-09) x 4 (2020-08-31). The error
    falls to 4x by 2016 and vanishes by 2021 as those splits are absorbed
    into the filed count. 446 of ~929 tickers carry at least one split.

    This is `adr_adjusted_shares`'s defect one layer deeper -- a share
    count on a different basis than the price it multiplies -- and it did
    the damage that one did not: `crit_mcap` decides `in_trade`, so the
    historical trade universe was undersized and biased toward names that
    never split.

    **`ratios` must be every split with `ex_date > filed_on`, including
    splits after `as_of`.** That reads like look-ahead and is not. Market
    cap is split-invariant, so the factor cancels; the only requirement is
    that price and shares share one basis, and the price side has already
    absorbed those later splits. Filtering to `ex_date <= as_of` would
    leave the two mismatched, which is the bug being fixed.

    `None` passes through unchanged rather than becoming 0.0: "no filing
    yet" is not "no shares" (invariant 4).
    """
    if shares is None:
        return None
    factor = 1.0
    for ratio in ratios:
        factor *= float(ratio)
    return shares * factor


def evaluate_criteria(
    ind_row: pd.Series,
    mcap: float | None,
    sector_median_return: float | None,
    rev_growth_positive: bool | None,
    up: UniverseParams,
) -> dict[str, bool | None]:
    """The five ADR 014 criteria, judged independently.

    `ind_row` supplies `close`, `sma_200`, and `sma200_slope_60` from the
    indicators table, plus `rel_return_756d` — the trailing 3-year total
    return, which the universe job computes and attaches because it is a
    cross-sectional quantity rather than a per-bar indicator.

    `mcap` is point-in-time: the caller resolves it from the latest filing
    with `filed_on < as_of` (DESIGN §2.4). A ticker with no filing yet gets
    `None`, not a backfilled current share count.
    """
    return {
        "crit_mcap": _cmp(mcap, up.min_mcap_usd, strict=False),
        "crit_above_sma200": _cmp(ind_row.get("close"), ind_row.get("sma_200")),
        "crit_sma200_slope": _cmp(ind_row.get("sma200_slope_60"), 0.0),
        "crit_rel_return": _cmp(ind_row.get("rel_return_756d"), sector_median_return),
        "crit_rev_growth": rev_growth_positive,
    }


# How long a rebalance period lasts, in calendar days, per `UniverseParams.
# rebalance_freq`. Calendar arithmetic, not a tunable threshold: a quarter
# is ~92 days because a quarter is ~92 days. Invariant 9 governs numbers
# that could be swept and change a result; these change only if the
# definition of a month changes.
#
# `rebalance_freq` had no consumer at all until 2026-08-20 — declared in
# `core/config.py` and read nowhere.
_REBALANCE_DAYS: dict[str, int] = {"D": 1, "W": 7, "M": 31, "Q": 92, "A": 366, "Y": 366}


def evaluation_max_age_days(rebalance_freq: str) -> int:
    """How stale an indicator row may be and still evaluate a quarter.

    **One rebalance period, so an evaluation must rest on data from inside
    the period it describes.** A ticker that did not trade at all during
    the quarter has not been shown to pass anything, and the criteria are
    a claim that it did.

    Without this floor `jobs.compute._latest_indicator_row` filtered
    `ts <= as_of` with no lower bound, so a ticker that stopped trading
    kept returning its final row forever. AET (Aetna, acquired by CVS
    2018-11-29) passed all four criteria at 2026-06-30 on data frozen in
    November 2018 — **31 consecutive quarters `in_trade` with no bars
    behind any of them**, found 2026-08-20.

    No event is affected, and the reason is structural rather than lucky:
    an event needs a bar and staleness means no bars, so the two sets
    cannot intersect. Measured at 0 rows before the change. What *is*
    affected is `research/arms.py`, which walks membership forward per day
    and held AET at a frozen 2018 price — rebalancing into it every quarter
    for seven years, because `last_price` persists once seen.

    Raises on an unknown frequency rather than defaulting: a silent fallback
    here would restore exactly the unbounded behaviour this closes.
    """
    key = rebalance_freq.strip().upper()
    if key not in _REBALANCE_DAYS:
        raise ValueError(
            f"rebalance_freq={rebalance_freq!r} has no known period. "
            f"Known: {', '.join(sorted(_REBALANCE_DAYS))}"
        )
    return _REBALANCE_DAYS[key]


def in_trade(universe_flags: pd.DataFrame, ticker: str, signal_date: date) -> bool:
    """Whether `ticker` is in the trade universe as of `signal_date`.

    **False when no universe evaluation exists** for that ticker on or
    before `signal_date` (ADR 129). Otherwise the most recent evaluation on
    or before `signal_date` decides.

    This fails **closed**. It failed open until 2026-08-19 — a v1
    simplification so `jobs.compute.run_events` worked before
    `run_universe` had ever run for a name — and the cost of that was
    18,805 training events on 566 tickers admitted to the trade population
    without ever being evaluated for it, 11.9% of the split. The check is
    per *ticker*, so a name that entered the universe late failed open
    across all of its earlier history, not merely the pre-2010 window where
    it was first assumed to live.

    Membership is a claim that a name passed four criteria. Absent evidence
    is not that claim, and defaulting to True made "we never looked" and
    "it passed" the same value in the same column.

    This is the single home for what used to be two identical copies —
    `jobs/compute.py:_in_trade` and `research/candidates.py:_in_trade`
    (Session 9 Task 3 duplicated it on purpose and flagged it for a ruling;
    Session 9 Task 9a Ruling C4-adjacent cleanup consolidates it here).
    It lives in `core/` rather than either caller's module because both
    `jobs/` and `research/` need it and neither may import a private from
    the other; `core/` performing no IO (invariant 1) is not violated here
    because `universe_flags` arrives as an already-loaded DataFrame — this
    function reads no database, file, or clock itself, same as the rest of
    this module.
    """
    rows = universe_flags.loc[
        (universe_flags["ticker"] == ticker) & (universe_flags["as_of"] <= signal_date)
    ]
    if rows.empty:
        return False
    return bool(rows.sort_values("as_of").iloc[-1]["in_trade"])


def is_tradeable(
    criteria: dict[str, bool | None],
    required: set[str] | None = None,
) -> bool:
    """Whether a name is in the trade universe, given the criteria that count.

    `required` defaults to all five. Passing a subset is the ablation arm:
    dropping `crit_rev_growth` measures what that criterion is worth without
    touching this function. An empty set admits everything, which is the
    no-filter control.

    An unknown criterion name raises rather than being ignored, so a typo in
    an ablation config fails loudly instead of silently widening the universe.
    """
    names = set(CRITERIA) if required is None else required
    unknown = names - set(CRITERIA)
    if unknown:
        raise KeyError(f"unknown criteria: {sorted(unknown)}")
    # `is True` rather than truthiness: None must fail, not raise or pass.
    return all(criteria.get(name) is True for name in names)
