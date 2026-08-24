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
from statistics import median

import pandas as pd

from capitalscan.core.config import (
    McapPlausibility,
    SharesPlausibility,
    UniverseParams,
)
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


_DEPOSITARY_PHRASE = "american depositary"


def is_depositary_listing(name: str | None) -> bool:
    """True when this listing's price is per depositary receipt.

    The share count SEC reports for such a filer is the issuer's
    **ordinary** shares, while `bars.close` is per **ADR**, so pricing one
    against the other overstates market cap by the ADR ratio. NTES carried
    a $1,666.9B peak against a real ~$100B before this.

    **Matched on the phrase, not on `ADR` or `ADS`.** A first attempt used
    `%ADR%` and flagged Bro*adr*idge, a US company with ordinary shares.
    Those three letters occur inside ordinary English words; "american
    depositary" does not.

    **Name is the only signal available.** There is no `is_adr` column and
    SEC exposes none, so the listing name is what there is. A missing name
    is therefore `False` rather than unknown-so-assume-ADR: 317 of the
    Nasdaq additions arrived with a NULL name, and treating those as
    depositary listings would swap a correct SEC count for a Yahoo one
    across hundreds of ordinary listings.

    No ratio is computed here or anywhere downstream of it. The caller
    switches *source* rather than scaling a number, because the ratio is
    not reliably derivable: VOD measures 11.54 against a real 10:1, and LI
    and ONC land nowhere near an integer, since SEC's latest filing and
    Yahoo's current count are months apart. Inferring a scale factor from
    the data's own shape is what `jobs.ingest._implausible_shares_reason`
    warns produces "a plausible-looking wrong number".
    """
    if not name:
        return False
    return _DEPOSITARY_PHRASE in " ".join(name.lower().split())


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


def scale_error_indices(shares: Sequence[float], bounds: SharesPlausibility) -> list[int]:
    """Positions of filings corrupted by a x1,000 tagging error.

    `_implausible_shares_reason` tests one value against an absolute band
    and cannot see this class -- `SharesPlausibility` says so explicitly
    and enumerates the 26 filings across 12 tickers that slip through. A
    share count in the tens of millions, multiplied by 1,000, lands in the
    tens of billions, which is exactly where a genuine mega-cap lives.
    No bound drawn on one number separates those two.

    **The series does.** A real share count moves by tens of percent
    between quarterly filings, including across splits; the largest real
    step in the tracked universe is NVDA's 10:1, and it *persists*. A
    tagging error spikes ~1,000x and returns three or four filings later.

    **This is not the relative test `SharesPlausibility` rejects**, and the
    distinction is the whole design. That argument is against comparing a
    filing to its ticker's **global** median, and it is right: PSKY's
    global median *is* the corruption, so a global test flags its one good
    filing. Two things make a local window survive it:

    - **The minimum-filings gate.** PSKY has three filings. Rather than
      out-voting it, the guard declines to rule at all -- silence, not a
      verdict from two data points.
    - **The window itself.** WULF is the case a global test gets backwards:
      TeraWulf diluted from a tiny base, so 16 **genuine** consecutive
      filings sit up to 247x its global median and a global rule would
      reject every one. Locally each is ~1.0-1.3x its neighbours, so no
      window ever looks at it twice.

    **One-sided, deliberately.** A clean filing bracketed by corrupt ones
    sits at ~0.001x its local median -- AAP has two such rows, on
    2011-06-01 and 2012-08-20, on either side of a run of four. They are
    correct and must survive, so distance alone is never the test.

    **Naming the factor is a precondition, not a step.** A filing is
    flagged only when dividing by 1,000 puts it back on top of its
    neighbours. An anomaly that a x1,000 scale does not explain is left
    alone, because removing it would mean inferring some other factor from
    the data's own shape -- the thing `_implausible_shares_reason` refuses
    to do, for the reason it gives. The caller **rejects** these rows and
    logs them (invariant 4); nothing here divides anything.

    Returns positions rather than a filtered series so the caller keeps the
    identity of what it dropped for `bar_rejects`.
    """
    n = len(shares)
    if n < bounds.min_filings_for_scale_check:
        return []

    flagged: list[int] = []
    low = 1.0 / bounds.scale_recovery_tolerance
    for i, value in enumerate(shares):
        if value is None or value <= 0 or _isnan(value):
            continue
        neighbours = [
            other
            for j, other in enumerate(shares)
            if j != i
            and abs(j - i) <= bounds.scale_window
            and other is not None
            and other > 0
            and not _isnan(other)
        ]
        if not neighbours:
            continue
        local = median(neighbours)
        if local <= 0 or value / local <= bounds.scale_anomaly_ratio:
            continue
        recovered = (value / bounds.scale_factor) / local
        if low <= recovered <= bounds.scale_recovery_tolerance:
            flagged.append(i)
    return flagged


def implausible_mcap_reason(mcap: float | None, bounds: McapPlausibility) -> str | None:
    """`None` when `mcap` is usable; otherwise the `bar_rejects.rule`.

    Reject, never correct — the same rule `_implausible_shares_reason`
    follows. Dividing by an inferred scale factor would be guessing at the
    factor from the data's own shape, and a wrong guess is a
    plausible-looking wrong number rather than an obvious one.

    `None` in means `None` out: "no shares on file" was never a measurement
    and is not a rejection.
    """
    if mcap is None:
        return None
    if mcap <= 0:
        # Not a small company, a bad computation. `crit_mcap` would fail it
        # either way; naming it puts the cause in `bar_rejects` instead of
        # leaving it to look like a genuine miss.
        return "mcap_not_positive"
    if mcap > bounds.max_mcap_usd:
        return "mcap_above_plausible_ceiling"
    return None


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


# `universe.watch_reason` values. Stored on the row rather than derived at
# read time, because the two populations are admitted by different arguments
# and will behave differently -- collapsing them into one undifferentiated
# flag would make it impossible to measure whether one works and the other
# does not.
WATCH_HISTORY = "history"
WATCH_PULLBACK = "pullback"


def watch_reason(
    criteria: dict[str, bool | None],
    bars: int,
    stale: bool,
    min_bars_for_rel_return: int = 757,
) -> str | None:
    """Why this row belongs to the watch universe, or `None`.

    `in_watch` is a **sibling** of `in_trade`, not a relaxation of it. The
    two are disjoint by construction: this returns `None` for anything that
    already qualifies to trade, so a name graduates from watch to trade and
    never holds both.

    Two admission reasons, and the distinction between them is the whole
    argument:

    - `history` -- every judgeable criterion passes and `crit_rel_return` is
      `None` **because the ticker is too new to have 757 daily bars**. GE
      Vernova was spun out of GE in April 2024 with 603 bars: its trailing
      three-year return is undefined, not bad.
    - `pullback` -- `crit_sma200_slope` holds while `crit_above_sma200` does
      not, so price sits below a **rising** 200-day average. That is a dip
      inside an intact uptrend, which for a mean-reversion study is the
      canonical setup rather than a risk.

    **Why not "any three of four".** Measured 2026-08-24: three passing with
    one `None` is 6 tickers and $1.18T; three passing with one `False` is
    247 tickers and $14.85T. The second admits names *because* they failed
    the test that would have excluded them. The pullback carve-out is not
    that: it is one specific failure conditioned on the trend gate still
    passing, and TSLA demonstrates the discrimination in both directions --
    `above_sma F, slope F` in 2024 is a real downtrend and stays out, while
    `above_sma F, slope T` in 2026 is a $1.4T dip and is admitted.

    **`crit_mcap` and `crit_sma200_slope` are never waived.** The first is a
    designed floor and is what already excludes the genuinely dangerous
    names -- CHGG has no `universe` row at all, because $20B filters it
    before any criterion runs. The second is the trend gate that separates a
    pullback from a collapse.

    **`stale` is never waived either.** A sibling universe inherits every
    safeguard, and ADR 135 exists because Aetna passed all four criteria at
    2026-06-30 on an indicator row frozen in November 2018 -- 31 consecutive
    quarters `in_trade` with no bars behind any of them. Without this the
    watchlist fills with delisted companies that look like discoveries.

    `bars` is the ticker's daily bar count: one row in `bars` at
    `interval='1d'`, i.e. one session's candle.
    """
    if stale:
        return None
    if criteria.get("crit_mcap") is not True:
        return None
    if criteria.get("crit_sma200_slope") is not True:
        return None

    rel = criteria.get("crit_rel_return")
    above = criteria.get("crit_above_sma200")

    if above is True and rel is None and bars < min_bars_for_rel_return:
        return WATCH_HISTORY
    if above is False and rel is True:
        return WATCH_PULLBACK
    return None


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
