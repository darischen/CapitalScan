"""Configuration dataclasses. Frozen values only, no IO (ADR 091).

**The sole import is `dataclasses`.** No `open()`, no `os.getenv()`, no
`Path.exists()`, no `dotenv`. Resolution — the CLI / env / TOML / default
precedence chain — lives in `jobs/config.py`, which owns all IO. `core/`
receives config objects as arguments and never fetches them.

This is the module every other module imports, so a single environment read
here would break purity for the whole library: the backtest and the poller
would stop being callable through identical code paths, and `core/` would
stop being testable without fixtures on disk (DESIGN §3.1, invariant 1).

Every constant in the system lives here. No magic numbers anywhere else
(invariant 9). Each backtest serializes its full config into `runs.params`,
so reproducing a result means reading one JSON blob.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IndicatorParams:
    bb_window: int = 20
    bb_std: float = 2.0
    bb_ddof: int = 0  # population std (ADR 004)
    stoch_window: int = 14
    stoch_smooth_k: int = 3
    stoch_smooth_d: int = 3
    sma_long: int = 200
    sma_slope_window: int = 60
    atr_window: int = 14
    rv_window: int = 20
    rv_pct_window: int = 252
    vol_z_window: int = 20
    dd_window: int = 252
    # How many bars back the close-confirmed band is read from (ADR 109).
    #
    # 0 means bar t's own band, which is what every charting platform draws
    # and what "closed above the upper band" universally means. 1 restores
    # ADR 108's original shifted rule.
    #
    # **This field exists so the band choice is part of `config_hash`.** ADR
    # 109 changed a formula in `core/indicators.py`, and `config_hash` hashes
    # `dataclasses.asdict(Config)` — a formula is not a `Config` field, so the
    # hash did not move and a backtest would have silently overwritten the
    # measurements taken under the superseded rule. That is the same defect
    # `SignalParams.enabled_signal_types` was added to close one day earlier,
    # reached by a different route.
    #
    # Naming it also makes the superseded rule *reproducible* rather than
    # merely abandoned: `bear_close_band_lag=1` reconstructs ADR 108's
    # population, which is what lets the 55%-fewer-firings comparison be
    # re-derived from a config instead of only from a database snapshot.
    # DESIGN §3.10 wants an ablated rule to be a config change, not a code
    # change.
    bear_close_band_lag: int = 0


@dataclass(frozen=True)
class SignalParams:
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    # ADR 044. Flipped to True 2026-08-16 (user's decision), together with
    # `stoch_source = "k_fast"` below. The pair states one rule: the raw %K
    # is the trigger, and the smoothed %K must sit within
    # `fast_agreement_tol` of it for a confluence to fire.
    #
    # `core.signals._fast_agrees` compares `|k_fast - k_full|`, which is
    # symmetric, so "full within 5 of fast" and "fast within 5 of full" are
    # the same predicate. No code change was needed to express it either
    # way round.
    #
    # Scoped to the confluence types only — bare `stoch_overbought` /
    # `stoch_oversold` never consult it — so this narrows `confluence_high`
    # and `confluence_low` while leaving the single-condition types alone.
    require_fast_agreement: bool = True
    fast_agreement_tol: float = 5.0
    # ADR 142 (user's decision, 2026-08-20). Widens agreement: the two %K
    # columns also agree when **both** sit beyond the same threshold, however
    # far apart they are.
    #
    # `fast_agreement_tol` measures numeric proximity, which is not the
    # question the rule is asking. %K_fast 99 with %K_full 89 is the
    # strongest joint statement the two columns can make and the +/-5 band
    # refused it; %K_fast 81 with %K_full 77 is inside the band while the
    # smoothed column says the stochastic is not extreme at all. Measured on
    # 2026-08-20: 216,298 daily bars had both columns overbought and were
    # rejected, against 14,728 that fired while %K_full was not overbought.
    #
    # Additive, not a replacement. The tolerance limb is untouched, so no
    # event that fired before this stops firing -- the user's requirement.
    # 10,172 short and 6,606 long events are relabelled from a bare band
    # touch to a confluence, which is a 59% and 71% increase in the two
    # confluence populations and no change at all in the row count.
    #
    # **Set it to False to reconstruct every event measured before ADR 142.**
    # That is the same property `enabled_signal_types` (ADR 108) and
    # `bear_close_band_lag` (ADR 109) exist for: a superseded rule stays
    # reachable from a config rather than only from a database snapshot.
    #
    # **This field is also what moves `config_hash`.** ADR 142 changes a
    # comparison inside `core/signals.py`, and `jobs.config.config_hash`
    # hashes `dataclasses.asdict(Config)` -- a formula is not a field, so the
    # hash would not have moved and a backtest would have overwritten
    # 139,253 measured rows in place. ADR 108 and 109 were both reached by
    # this exact route.
    fast_agreement_both_extreme: bool = True
    price_tolerance: float = 0.0  # 0.0 == "at or beyond", exact
    # Which %K column decides oversold/overbought (2026-08-05, user's
    # decision — an A/B test, not a permanent swap). `k_full` (the
    # `stoch_smooth_k`-period-smoothed %K) is the value every existing ADR
    # and golden-reference fixture assumes; `k_fast` (raw, unsmoothed) is
    # more sensitive but untested. Kept as a named, sweepable field rather
    # than hardcoded in `core.signals._types_fired` so a k_fast run and a
    # k_full run are two comparable configs (different `config_hash`,
    # ADR 060) instead of one silently replacing the other with no way to
    # tell which produced which events.
    #
    # To run a comparison backtest with k_fast (PowerShell, this session
    # only — `jobs.config.resolve_config`'s env-var layer, ADR 091):
    #   $env:CAPSCAN_SIGNALS = '{"stoch_source": "k_fast"}'
    #   uv run cscan backtest --workers 8
    # No `config.toml` needed/created — the env var is read directly and
    # leaves no repo state behind once the shell session ends.
    #
    # Flipped to `k_fast` 2026-08-16 (user's decision), superseding the
    # 2026-08-05 note above that called this "an A/B test, not a permanent
    # swap". The raw %K is now the trigger and `require_fast_agreement`
    # keeps the smoothed %K within tolerance of it.
    #
    # `k_full` stays one env var away, so the pre-flip population is
    # reconstructible from a config rather than only from a database
    # snapshot — the same property `enabled_signal_types` (ADR 108) and
    # `bear_close_band_lag` (ADR 109) exist for.
    #
    # The flip was blocked for a day because every test in
    # `test_signals.py` set its extreme on `k_full` and read this default
    # for the source, so 21 tests depended on the default rather than on
    # the column under test. Those tests now route through `_ind(k=...)`
    # and follow whatever this says, and they pass under both values.
    stoch_source: str = "k_fast"  # "k_full" | "k_fast"
    # Which `SignalType` values may fire (ADR 108, added 2026-08-13).
    #
    # **This field exists to make the signal set part of `config_hash`.**
    # ADR 108 states that adding `BEAR_CLOSE_ABOVE_UPPER` forces a new hash,
    # because `signal_strength` counts concurrent types and therefore shifts
    # on every day it fires. That does not happen on its own:
    # `jobs.config.config_hash` hashes `dataclasses.asdict(Config)`, and an
    # enum member is not a `Config` field. Adding the type alone left the
    # hash at `1835688bf7d760ba` — the identity Sessions 12 and 13 published
    # against — so a backtest would have overwritten 626,977 event rows in
    # place, silently, and those results would have stopped reproducing.
    #
    # Naming the set here fixes that at the root: the enabled signals are
    # genuinely a config dimension, the same way ADR 060 treats a universe
    # threshold as one. It also gives DESIGN §3.10 what it asks for — an
    # ablated criterion becomes a config change rather than a code change —
    # and makes the pre-ADR-108 hash *reconstructible* rather than merely
    # abandoned, which is what keeps every published Session 12/13 number
    # derivable from a config instead of only from a database snapshot.
    #
    # Strings rather than `SignalType` members because this dataclass is
    # frozen and its sole import is `dataclasses` (invariant 10).
    # `core.signals.enabled_types` resolves and validates them; an unknown
    # name raises rather than silently disabling a real signal and minting a
    # hash for a config nobody intended.
    enabled_signal_types: tuple[str, ...] = (
        "bb_lower_touch",
        "bb_upper_touch",
        "stoch_oversold",
        "stoch_overbought",
        "confluence_low",
        "confluence_high",
        "bear_close_above_upper",
    )


@dataclass(frozen=True)
class ExitParams:
    max_hold_days: int = 5
    target_pct: float = 0.04  # swept 0.03-0.05 (ADR 009)
    stop_mode: str = "atr"  # "atr" | "fixed" | "none"
    stop_atr_k: float = 1.5  # swept 1.0-2.5 (ADR 008)
    stop_fixed_pct: float = 0.03
    exit_on_upper_band: bool = True
    exit_on_stoch_80: bool = True
    # Exit policy, independent of SignalParams.stoch_overbought even though
    # the long side defaults to the same value (ADR 092). One is signal
    # detection, the other is exit policy, and an exit-rule sweep must not
    # require a signal-config change.
    #
    # Two fields, not one derived from the other. Deriving the short as
    # `100 - long` would force both to move together in every sweep cell,
    # making combinations like (long 70, short 20) unreachable — and it
    # would hardcode exactly the long/short symmetry ADR 016 rejects, biasing
    # the Phase 4 asymmetry comparison before it is ever run.
    exit_stoch_threshold: float = 80.0
    exit_stoch_threshold_short: float = 20.0
    # Which %K column the close-based stochastic exit reads (2026-08-15,
    # user's decision). `core/exits.py` hardcoded `"k_full"` until now,
    # which was invisible while `SignalParams.stoch_source` was also
    # `k_full`. Flipping the entry to `k_fast` made the two disagree inside
    # one backtest, with no field naming the disagreement — invariant 9's
    # failure mode, where the output looks fine either way.
    #
    # Deliberately defaults to `"k_full"` rather than tracking
    # `SignalParams.stoch_source`. ADR 092 holds that exit policy is
    # independent of signal detection, and an exit-rule sweep must not
    # require a signal-config change; deriving this from the signal side
    # would reintroduce exactly the coupling that ADR rejects. The default
    # also preserves every exit measured to date, so this field's
    # introduction changes `config_hash` without changing behaviour.
    #
    # Set it to `"k_fast"` to make exits follow the entry.
    exit_stoch_source: str = "k_full"  # "k_full" | "k_fast"
    exit_on_mid_band: bool = False  # ADR 046
    # `capture_ratio = r_exit / mfe` (DESIGN §5.6) is unbounded as mfe -> 0+:
    # a signal whose favorable excursion barely cleared zero before exit
    # divides a normal-sized r_exit by a near-zero denominator. A real run
    # (2026-08-05) produced a ratio in the tens of thousands and a second
    # event exceeded events.capture_ratio's numeric(12,6) column limit
    # (10^6), aborting that ticker's whole insert batch. Clamped to
    # [-capture_ratio_cap, capture_ratio_cap] rather than nulled below some
    # mfe floor (the `mfe <= 0` case already nulls it) — a huge ratio still
    # means "the exit gave almost everything back," which a null would
    # discard; 100 keeps that signal readable while staying far inside the
    # column's range.
    capture_ratio_cap: float = 100.0


@dataclass(frozen=True)
class CostParams:
    slippage_bps: float = 3.0
    commission_per_share: float = 0.0
    borrow_bps_annual: float = 40.0
    short_enabled: bool = True


@dataclass(frozen=True)
class UniverseParams:
    # ADR 014 pins $200B nominal as the mega-cap threshold. Session 9
    # config-thresholds task (user's decision, 2026-08-02) lowers this to
    # $100B nominal — see the dated note appended to ADR 014 in
    # `docs/DECISIONS.md`. Session 10 (user's decision, 2026-08-03)
    # further lowers to $30B nominal to expand candidate pool beyond
    # mega-cap constraint, allowing large-cap inclusion.
    #
    # This value changes `config_hash` for every `Config` (ADR 060: a
    # different universe threshold is genuinely a different config). Report
    # the new hash prominently — a Postgres GUC is set from it.
    # 30e9 -> 20e9 on 2026-08-21 (the user's decision), with the Nasdaq
    # expansion. Measured that day against the live listing: 190 Nasdaq
    # names clear $20B against 142 at $30B, and `jobs/fetch/nasdaq.py`
    # ingests down to $5B so a name below the floor today still has the
    # bars to be measured in the quarters when it was above it.
    min_mcap_usd: float = 20e9
    # `min_price` was removed here on 2026-08-20, bundled into ADR 142's
    # rebuild because deleting it moves `config_hash` and was not worth a
    # five-hour run of its own (`BACKLOG.md`, the user's decision).
    #
    # It was declared in Session 9 and read nowhere in the tree for eleven
    # months: no criterion consulted it and `is_tradeable` never saw it. A
    # config field nothing reads is a claim the system makes and does not
    # honour -- someone tuning it would move the hash, rebuild, and get an
    # identical universe.
    #
    # Market cap subsumes it. SBNY trades at $0.64 with a $0.03B cap and
    # fails `crit_mcap` by three orders of magnitude, so there is no ticker a
    # price floor would exclude that `min_mcap_usd` does not already.
    rel_return_lookback_days: int = 756  # 3 years
    rebalance_freq: str = "Q"
    # ADR 014 names five criteria; this is the honest subset actually
    # enforced. `crit_rev_growth` is excluded because the SEC fetcher
    # (jobs/sec.py, called from jobs/ingest.run_shares) currently pulls only
    # `EntityCommonStockSharesOutstanding`, never a revenue tag, so
    # `jobs/compute._revenue_growth_positive` returns `None` for every
    # ticker with no exception. `core.universe.is_tradeable` correctly
    # treats `None` as failing, which is right for a single ticker missing
    # four quarters of filings but wrong applied uniformly: it silently
    # zeroes the entire trade universe every quarter, forever, because the
    # data that criterion needs was never ingested (DESIGN §4.6).
    # `is_tradeable`'s `required` parameter exists precisely so an
    # unimplemented or ablated criterion is a config change, not a code
    # change (DESIGN §3.10) — using it here, instead of quietly relaxing
    # `is_tradeable`, keeps the `crit_rev_growth` column honestly `None` in
    # the audit log while still letting the other four decide `in_trade`.
    # Restore `"crit_rev_growth"` here the day the fetcher pulls
    # `us-gaap:Revenues` and `_revenue_growth_positive` stops stubbing.
    required_criteria: tuple[str, ...] = (
        "crit_mcap",
        "crit_above_sma200",
        "crit_sma200_slope",
        "crit_rel_return",
    )
    # Ordinary shares backing one ADR. An ADR's Form 20-F reports the
    # issuer's *ordinary* share count while the bar price is per *ADR*, so
    # market cap needs the ordinary count divided by this ratio first.
    #
    # Only tickers with a ratio other than 1:1 belong here; everything else
    # (including 1:1 ADRs) falls back to 1.0. Measured 2026-08-01 against
    # yfinance's ADR-equivalent counts: TSM 5.00, ASML 1.00, SAP 1.06,
    # NVO 1.32. Only TSM is a real ratio — TSMC's ADR is 1:5. SAP's 6% is
    # filing-date drift, and NVO's 1.32 is Novo's A+B share classes, where
    # yfinance's own `marketCap` agrees with the A+B total, so that number
    # is already correct and the ADR itself is 1:1.
    #
    # A tuple of pairs rather than a dict because this dataclass is frozen
    # and its sole import is `dataclasses` (invariant 10).
    #
    # ADR ratios do change — an issuer can re-ratio, and that silently
    # rescales every historical market cap computed here. If a ratio is
    # ever revised, the honest fix is a dated mapping, not editing this
    # number in place.
    adr_ordinary_per_adr: tuple[tuple[str, float], ...] = (("TSM", 5.0),)


@dataclass(frozen=True)
class StatsParams:
    min_n_eff: int = 30  # below this a cell is suppressed
    fdr_alpha: float = 0.05
    n_replications_default: int = 200
    n_replications_sweep: int = 50
    reach_targets: tuple = (0.02, 0.03, 0.05, 0.10)
    # Horizons for the `events.fwd_ret_*d` columns (DESIGN §5.6, §5.7). Not
    # bounded by `ExitParams.max_hold_days` (5) — 10 exceeds it on purpose,
    # since these are unconditional baseline returns on the raw price series,
    # not a statement about how long any position was held. Session 9 Task 7
    # added this field: `path_metrics`'s pinned interface took only
    # `fwd_bars` (bounded at `max_hold_days`), which cannot supply a 10-day
    # horizon, so the horizons live here rather than as a literal in
    # `research/enrich.py` (invariant 9).
    fwd_ret_horizons: tuple = (1, 2, 3, 5, 10)
    adverse_targets: tuple = (0.03, 0.05)
    dd_buckets: tuple = (0.10, 0.20, 0.35)
    era_bounds: tuple = ("2014-12-31", "2019-12-31", "2023-12-31")


@dataclass(frozen=True)
class SplitParams:
    ingest_start: str = "2009-01-01"
    event_start: str = "2010-01-01"
    train_end: str = "2021-12-31"
    validate_end: str = "2023-12-31"


@dataclass(frozen=True)
class Config:
    indicators: IndicatorParams = IndicatorParams()
    signals: SignalParams = SignalParams()
    exits: ExitParams = ExitParams()
    costs: CostParams = CostParams()
    universe: UniverseParams = UniverseParams()
    stats: StatsParams = StatsParams()
    splits: SplitParams = SplitParams()


DEFAULT_CONFIG = Config()


@dataclass(frozen=True)
class SweepParams:
    """DESIGN §5.9's exit-parameter grid (Session 9 Task 12). Deliberately
    **not** a field of `Config`: `jobs.config.config_hash` hashes
    `dataclasses.asdict(config)`, and every value a sweep cell actually
    varies (`stop_mode`, `stop_atr_k`, `target_pct`) already lives in
    `ExitParams`. Adding `SweepParams` here as a *standalone* dataclass — one
    `research.backtest.sweep_configs` reads to build 18 `ExitParams`
    variants — keeps the grid values out of `research/backtest.py` as bare
    literals (invariant 9) without adding a field to `Config` and changing
    `config_hash` for every existing config, swept or not (see
    `task-12-report.md` for the exact hash this session is already tracking
    for the Postgres GUC).

    `stop_atr_k` only has behavioral meaning under `stop_mode="atr"` —
    `"fixed"` uses `stop_fixed_pct` instead, `"none"` places no stop at all
    — so this dataclass does not enumerate `stop_mode` x `stop_atr_k` as an
    independent cross product; `sweep_configs` collapses that pairing to 6
    stop variants (4 atr k-values + 1 fixed + 1 none) itself.
    """

    stop_atr_ks: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5)
    target_pcts: tuple[float, ...] = (0.03, 0.04, 0.05)


DEFAULT_SWEEP = SweepParams()


@dataclass(frozen=True)
class ServingParams:
    """What the cloud serving store carries (ADR 053, ADR 137).

    **Standalone, not a `Config` field**, for the same reason as
    `BaselineParams` below: nothing here varies a backtest result, and
    `jobs.config.config_hash` hashes `dataclasses.asdict(config)`. Folding
    this in would move `config_hash` for every config already written to
    `events` in order to name a deployment constant the backtest never
    reads. Invariant 9 is still satisfied — the number lives here and
    `jobs/sync.py` holds no literals.

    `history_years` is how far back the served subset reaches. Three,
    measured against Neon's 512MB free tier on 2026-08-20:

        1 year   139 MB     3 years  393 MB     full   2,149 MB
        2 years  266 MB     5 years  638 MB

    Three fits with 23% headroom, covers every chart range through 1y and
    most of 2y, and spans the validate split (2022-2023) so `/research` is
    fully backed. The 5y range degrades by stopping early, which the chart
    already handles — it pages until the server returns nothing.

    **This is a serving cut, never a statistical one.** No measured number
    is computed from the subset; the arms, the cells and the benchmarks are
    all produced locally against full history and shipped as results. A
    reader of the deployed site sees fewer *dates*, never a different
    *answer*.
    """

    history_years: int = 3


@dataclass(frozen=True)
class BaselineParams:
    """DESIGN §6.2 / ADR 062's dual-baseline geometry (Session 11.2).

    Standalone for the same reason as `SweepParams` above: nothing here
    varies a backtest result, and `jobs.config.config_hash` hashes
    `dataclasses.asdict(config)`, so folding these into `Config` would move
    `config_hash` for every config already written to `events` in order to
    name constants the backtest never reads. Invariant 9 is still satisfied
    — the numbers live in `core/config.py` and `core/baselines.py` holds no
    literals.

    `trailing_window_days` is the strictly-prior window the parametric
    baseline reads. A window that includes the day being predicted is
    lookahead, and it produces a baseline that looks excellent.

    `horizon_days` and `trading_days_per_year` are what turn an annual drift
    and vol into the horizon values DESIGN §6.2 spells as `mu_ann / 50.4`
    and `sigma_ann * sqrt(5/252)`: 50.4 is `252 / 5`, derived here rather
    than written down, so a horizon change cannot leave a stale divisor
    behind.

    `disagreement_pct` is the gap, in absolute probability, above which the
    empirical and parametric baselines are called disagreeing. Ten points on
    a baseline that typically sits near 0.36-0.40 is a quarter of the
    quantity, far outside what estimation noise on ~250 overlapping
    observations produces, and it is the diagnostic ADR 062 asks for: a flag
    that the ticker-year's return distribution is not normal, never an input
    to any reported number.
    """

    trailing_window_days: int = 252
    horizon_days: int = 5
    trading_days_per_year: int = 252
    disagreement_pct: float = 0.10


@dataclass(frozen=True)
class ReportingParams:
    """DESIGN §6.11's descriptive splits (Session 12.4, ADR 099, ADR 103).

    Standalone for the same reason as `BaselineParams` above, and the
    reason matters more here than anywhere else: `StatsParams` *is* a
    `Config` field, so adding `max_ticker_share` there would move
    `config_hash` for every config already written to `events` — including
    the live poller's — and the poller's same-day duplicate check keys on
    that hash. A reporting threshold is not a backtest parameter and must
    not behave like one.

    `max_ticker_share` is the pooled-reporting cap: above it, the cell is
    reported with and without its largest contributor. NVDA over 2020-2024
    is the obvious risk, and 2015-2019 holds 140,288 event rows across only
    196 distinct tickers, so the cap is expected to bind.

    `breadth_quantiles` is the number of breadth buckets, cut on the
    empirical distribution **per era** because firing rates differ across
    regimes. Three is the tercile split DESIGN §6.11 describes; the field
    exists so the split is named rather than written into a `qcut` call.
    """

    max_ticker_share: float = 0.15
    breadth_quantiles: int = 3


DEFAULT_BASELINE = BaselineParams()


@dataclass(frozen=True)
class RhoParams:
    """ADR 098's `rho_bar` measurement settings (Session 11.3).

    Standalone, same rationale as `BaselineParams` above: `rho_bar` is a
    measurement stored in `rho_era`, deliberately kept out of `config_hash`
    (ADR 098 part 3), so folding these into `Config` would do the one thing
    that ADR exists to prevent.

    `min_pair_overlap` is how many shared observations a ticker pair needs
    before its correlation counts toward the weighted mean. Below it the
    estimate carries almost no information: a pair with three overlapping
    days can return exactly 1.0 by coincidence, and there are far more thin
    pairs than dense ones. Such pairs drop out rather than being counted as
    perfectly correlated. 30 is the same floor `factor_betas` applies before
    it will fit a beta, so a pair either informs both estimates or neither,
    which is what keeps `rho_gap` a statement about the two estimators
    rather than about which pairs each happened to see.

    The 5-day horizon is **not** here. It lives in `BaselineParams.horizon_days`
    and is read from there: `rho_bar` and the baseline must describe the same
    quantity, and two independently editable horizons is exactly how they
    would stop doing so.
    """

    min_pair_overlap: int = 30


DEFAULT_RHO = RhoParams()


@dataclass(frozen=True)
class McapPlausibility:
    """Ceiling for a single `universe.mcap_usd`, applied in
    `jobs.compute.run_universe`.

    Deliberately **not** a field of `Config`, same rationale as
    `SweepParams` and `SharesPlausibility` above: nothing here varies a
    backtest result, and folding it in would move `config_hash` for every
    existing config.

    **Why a second guard, when `SharesPlausibility` already exists.** That
    one guards the *filing* and documents why it cannot close the x1,000
    class: an error on a company with real shares in the tens of millions
    lands inside `[min_shares, max_shares]` and is accepted. It accepts
    them deliberately, because rejecting a genuine filing freezes that
    ticker's share count forever and silently, while a bad one surfaces as
    an absurd market cap.

    This guard sits on the derived value instead, where neither property
    holds: nulling one quarter's `mcap_usd` excludes that ticker for that
    quarter only, and the next quarter recomputes from scratch.

    **It logs rather than merely nulling.** The absurd market cap *is* the
    detection mechanism — it is how the 32B-ceiling defect was found in
    Session 9 and how the PKG/GRMN/BNTX values were found on 2026-08-22. A
    guard that silently replaced a loud wrong number with a quiet missing
    one would repeat the error one layer up, so `run_universe` writes a
    `bar_rejects` row for every value it drops.

    **`max_mcap_usd` = $6T.** AAPL sat at $4.25T on 2026-06-30 in this
    database, so the ceiling has to clear the largest real company by a
    real margin or it rejects the truth. $6T catches BNTX ($65.9T), PKG
    ($18.9T), GRMN ($13.9T) and AAP ($6.5T).

    **The known gap, stated rather than implied.** This is a backstop
    against the *impossible*, not a fix for the merely wrong. ALK carried
    $2,453B — plainly wrong for Alaska Air, and comfortably inside any
    bound that also has to clear AAPL. Tightening far enough to catch it
    would start rejecting real mega-caps, which is the same trade
    `SharesPlausibility` refused at the ingest end.
    """

    max_mcap_usd: float = 6e12


@dataclass(frozen=True)
class SharesPlausibility:
    """Absolute floor/ceiling for a single `shares_outstanding.shares` value,
    ingested in `jobs.ingest.run_shares` (Session 9 shares-guard task).

    Deliberately **not** a field of `Config`: nothing here varies a backtest
    result, so folding it into `Config` would change `config_hash` for every
    existing config for a value that has no effect on signal/exit/cost
    behavior. Standalone, same rationale as `SweepParams` above.

    **Why absolute, not relative-to-the-ticker's-own-history.** The obvious
    first idea — reject a filing whose `shares` is N times its ticker's own
    median or its nearest neighboring filing — fails on a real shape found
    while deriving these bounds: PSKY's median share count is 1,000, because
    two of its three known filings are themselves bad (a placeholder-looking
    `1,000`), and the one plausible filing (1,071,666,977, its most recent)
    is the one a median- or neighbor-relative test would flag as the
    outlier and reject — exactly backwards. A criterion built from the
    data's own central tendency inherits that data's own corruption. These
    bounds are set from external facts about the US equity market instead:
    a real listed company's total share count, at any point in this
    project's tracked history (2009-present).

    **`min_shares`.** Every confirmed-genuine `shares_outstanding` row in
    the live database (73k+ rows) sits above 1,000,000; every row below
    that line inspected by hand was a placeholder-shaped filer error (`1`,
    `100`, `1000`, `13001`, ...), not a real coincidentally-small issuer.
    Berkshire Hathaway is the standing edge case for "smallest real share
    count a mega-cap can have" (its Class A count runs in the hundreds of
    thousands), which is why the floor is not set at, say, 10,000,000 even
    though that would still clear every confirmed-bad row found — 1,000,000
    keeps clearance for a legitimately low-float name without weakening the
    guard against any bad value actually observed.

    **`max_shares`.** Originally set from the highest genuine share count on
    file, Citigroup pre-2011-reverse-split at ~29.2 billion (2011-05-05
    filing), with the ceiling at 32 billion for roughly 10% headroom above
    it. Review finding (Session 9, config-thresholds task) rejected that
    number: 32B sits only ~10% above a *live, current* count — Citigroup's
    own 2026 count is still ~29.2B, TSM is ~25.9B, and NVDA is ~24.5B
    post its 2024 10:1 split. A single ordinary 2:1 split on any of those
    three real constituents produces a genuine filing above 32B, and the
    old ceiling rejected it permanently: `run_shares` never retries a
    rejected accession, so once a real filing sits outside the band, every
    later filing for that ticker is rejected too, and the ticker is frozen
    at its last-accepted share count forever, silently.
    **The ceiling is now 320 billion** — 10x the old value, chosen so a
    10:1 split on today's largest issuer (Citigroup, ~29.2B) still clears
    with room (292B, well inside 320B), not just a 2:1. `min_shares` is
    untouched: every real value on file is orders of magnitude above it and
    every bad value found sits far below it, so the floor is sound and the
    review confirmed no change is warranted there.

    **Known limit, quantified, not just described.** A x1,000 filer error
    on a company whose real share count is in the tens of millions lands in
    the same absolute neighborhood as a genuine mega-cap's real count in
    the billions, so `max_shares` alone cannot always distinguish the two.
    Moving the ceiling to 320B widens this gap: a x1,000 error on a company
    with real shares in the tens of *millions* (tens of billions after
    corruption) now lands inside `[min_shares, max_shares]` and is accepted
    undetected, whereas the old 32B ceiling only missed errors on companies
    with real shares up to ~32M.

    This is not a one-ticker gap. The controller backed up every row this
    guard has ever rejected to `shares_outstanding_rejected_20260802`
    before deleting them. Querying that table for rows the widened ceiling
    now admits (`shares BETWEEN 32e9 AND 320e9`) returns **26 filings
    across 12 tickers**: AAP (4), GRMN (5), PKG (3), ALK (3), FTNT (2),
    SWKS (2), MAA (2), and one each for AIZ, CNX, EOG, PNR, REG — every one
    a real company with real shares in the tens-to-hundreds of millions,
    x1,000-corrupted, that the old 32B ceiling caught and the new 320B
    ceiling would silently accept.

    The ceiling is not vestigial, though: the same reject-log query with
    `shares > 320e9` returns 33 rows across 23 tickers (ORCL, AMD, EXC,
    TFC, AEP, and others) that still exceed the new ceiling and would still
    be caught — real x10^5/x10^6-class filer corruption, not the x1,000
    class the widened band now misses. Re-run both queries against
    `shares_outstanding_rejected_20260802` to verify either count.

    **The asymmetry that justifies this anyway.** Rejecting good data is
    worse than admitting bad data here. A bad share count surfaces
    downstream as an absurd market cap — that is exactly how the original
    32B-ceiling defect was found (`docs/DECISIONS.md`, the ADR-mcap-outlier
    diagnosis). A rejected genuine filing is invisible: nothing downstream
    can detect a filing that was never stored, and the ticker silently
    stops updating rather than throwing a loud, greppable market-cap
    outlier. Given a choice between a rare, structurally-undetectable bad
    value slipping through and a real ticker going permanently stale with
    no error, this guard is deliberately tuned toward the former.

    `min_shares` has no equivalent gap: every real value is orders of
    magnitude above it and every bad value found sits far below it, with the
    sole exception of two BRK-B 2009-2010 filings (1,103,764 and 1,056,884)
    that are themselves implausible against BRK-B's own contemporaneous
    price but sit just above `min_shares` — a second, class-mismatch defect
    this guard does not close (see the shares-guard report for the query
    that surfaces it for the separate cleanup step).

    **Splits clear this with room.** NVDA's 2024 10:1 split took it from
    ~2.5B to ~24.5B shares — inside `[min_shares, max_shares]` before and
    after. AAPL's 2020 4:1 split (~4.3B -> ~17.1B) clears the same way. A
    single filing multiplying share count by 10x, or even the largest
    real-world stock split multiple in the tracked universe, never
    approaches the 1,000x+ jump these bounds are built to catch.
    """

    min_shares: int = 1_000_000
    max_shares: int = 320_000_000_000

    # --- The x1,000 class the absolute band above cannot see ------------
    #
    # The docstring names 26 filings across 12 tickers that sit inside
    # `[min_shares, max_shares]` and are still wrong by three orders of
    # magnitude. These four values close that gap with a *local* test,
    # which is not the relative test the docstring rejects: that argument
    # is against comparing a filing to its ticker's **global** median, and
    # it stands. See `core.universe.scale_error_indices`.
    #
    # Every number here was set from the live table on 2026-08-22, not
    # tuned to make a test pass.

    # Neighbours per side. Four is the smallest window that outvotes the
    # longest observed run of consecutive bad filings (AAP's four, from
    # 2011-08-24 to 2012-05-29) while staying inside the ~2 years either
    # side over which a share count moves by tens of percent, not orders
    # of magnitude.
    scale_window: int = 4

    # Below this, decline to rule rather than ruling from too little. This
    # is the gate that excludes PSKY (3 filings, two of them corrupt),
    # which is the counterexample that sinks a global-median test.
    min_filings_for_scale_check: int = 8

    # A filing must exceed its local median by this much to be a
    # candidate. WULF's genuine dilution peaks at 247x its *global* median
    # and never exceeds ~1.3x locally, so it is not close to this line.
    scale_anomaly_ratio: float = 50.0

    # The only factor this guard will name. Not inferred from the data.
    scale_factor: float = 1_000.0

    # ...and dividing by it must actually explain the anomaly, landing the
    # filing within this factor of its neighbours in both directions. An
    # anomaly a x1,000 scale does not explain is left alone, because
    # removing it would mean guessing at some other factor from the data's
    # own shape -- exactly what `_implausible_shares_reason` refuses to do.
    scale_recovery_tolerance: float = 5.0


DEFAULT_SHARES_PLAUSIBILITY = SharesPlausibility()


@dataclass(frozen=True)
class HourlySplitGuard:
    """Tolerance for `jobs.ingest`'s hourly/daily range-escape guard
    (Session 9 hourly-split-adjust task).

    Deliberately **not** a field of `Config`: this bounds a data-quality
    check, not a backtest input — nothing here varies a signal, exit, or
    cost, so folding it into `Config` would change `config_hash` for every
    existing config for a value with no effect on backtest output.
    Standalone, same rationale as `SweepParams` and `SharesPlausibility`
    above.

    **What it catches.** An hourly bar's day-aggregate range (max high,
    min low across that ticker's hourly bars for the day) escaping its
    matching daily bar's range by more than this fraction. A ticker that
    splits inside the hourly window and is not back-adjusted shows this as
    a sustained, multi-hundred-day escape at exactly the split ratio
    (Session 9: KLAC ~10x, CRWD ~4x, AMCR ~0.2x) — the defect this guard
    exists to catch reappearing on the next unadjusted split.

    **Where `range_escape_tolerance` comes from.** Queried against the
    live database (297,790 (ticker, day) pairs where hourly and daily
    bars both exist): ordinary tick-level noise between the two feeds has
    a median absolute gap of ~$0.005 and stays under roughly 10% relative
    deviation for the overwhelming majority of pairs; the largest
    relative deviation found among pairs with **no** corresponding split
    in `corporate_actions` was ~33% (FTV, WDC — a separate, smaller
    daily/hourly data-quality gap, not this defect). The smallest relative
    deviation among the 17 tickers with a confirmed, unadjusted split in
    the window was AMCR's reverse split (ratio 0.2, an 80% deviation);
    every other confirmed split deviates by 200% or more (CRWD 4x, KLAC
    10x, BKNG 25x). 0.50 (50%) sits with wide margin above the largest
    non-split anomaly (0.33) and well below the smallest real split
    (0.80), so it flags every known split-factor mismatch while leaving
    every known non-split anomaly alone.
    """

    range_escape_tolerance: float = 0.50


DEFAULT_HOURLY_SPLIT_GUARD = HourlySplitGuard()


@dataclass(frozen=True)
class HourlySplitDetection:
    """Decisiveness margin for `jobs.ingest._split_adjustment_factor`'s
    per-day "did the vendor already adjust this?" test (Session 9
    double-adjust-fix follow-up, 2026-08-02).

    Deliberately **not** a field of `Config`, and deliberately **not**
    reusing `HourlySplitGuard.range_escape_tolerance` — a review finding
    caught that borrowing: this dataclass answers a different question at
    a different scale.

    **Why this can't share `range_escape_tolerance` (0.50).** That value
    separates "clean bar" from "split-sized escape" *after* adjustment,
    calibrated against the single worst non-split anomaly ever observed
    (~33%) versus the smallest real split's post-adjustment deviation
    (~80%) — a wide gap with room for a threshold in the middle. This
    dataclass instead has to pick between two competing hypotheses for
    what the *raw, pre-adjustment* value should be — "already adjusted"
    (ratio to daily ~1.0) vs. "not yet adjusted" (ratio to daily ~ the
    split factor) — and for a split with a small ratio (measured live:
    `SELECT count(*) FILTER (WHERE ratio BETWEEN 0.667 AND 1.5) FROM
    corporate_actions WHERE action_type='split' AND ex_date > '2024-08-01'`
    returns 11 of 33 splits in the hourly window), the two hypotheses sit
    close enough together that 0.50 would make everything look like
    either answer — it has no discriminating power at that scale. What
    varies here is measurement noise around one of two nearby points, not
    a decision about a huge range.

    **Where `resolution_margin` (0.10) comes from.** `HourlySplitGuard`'s
    own docstring already characterizes ordinary hourly/daily tick noise
    from the live 297,790-pair measurement: it "stays under roughly 10%
    relative deviation for the large majority of mismatched pairs." That
    is the number this margin reuses — not `range_escape_tolerance`
    itself, but the underlying noise floor it was derived from, applied
    to its actual purpose (bounding ordinary noise) instead of stretched
    to also bound split-decisiveness. A day's observed ratio must sit
    closer to one hypothesis than the other by more than this margin to
    be called decisively either way; within it, the two hypotheses are
    not reliably distinguishable from noise and the day is treated as
    unresolved rather than guessed (invariant 4) — see
    `_split_adjustment_factor`'s docstring for what "unresolved" does.

    **Residual limit, stated directly.** A split whose ratio sits within
    roughly `2 * resolution_margin` of 1.0 (i.e. inside about [0.80, 1.20]
    for a forward split, symmetric for reverse) can still land in the
    unresolved band on an unlucky noisy day even though most days for
    that same split resolve cleanly, because the two hypotheses are close
    enough that ordinary noise can occasionally straddle the midpoint.
    This is inherent to the physical measurement, not a bug in the
    margin: no threshold makes two nearby points reliably separable when
    noise is large enough relative to their separation.
    """

    resolution_margin: float = 0.10


DEFAULT_HOURLY_SPLIT_DETECTION = HourlySplitDetection()


@dataclass(frozen=True)
class MonitoringThresholds:
    """Bounds for `cscan system-status` (DESIGN §9.6, ADR 080, ADR 083).

    Deliberately **not** a field of `Config`, same rationale as
    `SweepParams`, `SharesPlausibility`, and the two hourly-split guards
    above: nothing here varies a signal, an exit, or a cost, so folding it
    in would change `config_hash` for every existing config in service of a
    value that cannot affect a single event row. These describe how loudly
    to report on jobs, not how to run them.

    **`catch_up_delay_seconds`** comes straight from ADR 080: Task Scheduler
    runs a missed job at next boot, so a large gap between the intended fire
    time and the actual start means the machine was off rather than that
    anything failed. 3600 is the line that ADR draws, and `scheduled_runs`
    has been recording `delay_seconds` against it since Session 8 with no
    consumer to read it.

    **`stale_after_days`** matches the 2-day threshold ADR 070 already
    pins for the serving layer's staleness banner. One number for "this data
    is too old to trust," used by both surfaces, so the CLI and the API
    cannot disagree about what stale means.

    **`stale_running_hours`** has no prior ADR. A `runs` row sits at
    `status='running'` until its context manager closes it, so a process
    killed by a machine sleeping leaves the row open forever. There is no
    honest terminal status to rewrite it to (`runs_status_check` allows only
    `running`/`ok`/`failed`, and neither `ok` nor `failed` is true of an
    interrupted run), so `system-status` flags them by age instead of
    mutating history. 12 hours clears the longest legitimately-open job by a
    wide margin: the poller runs a single session across one market day
    (6.5 hours), and the longest measured batch job is the ~2h48m full
    backtest.
    """

    catch_up_delay_seconds: int = 3600
    stale_after_days: int = 2
    stale_running_hours: int = 12


DEFAULT_MONITORING = MonitoringThresholds()


@dataclass(frozen=True)
class BenchmarkParams:
    """Session 13's benchmark arms (DESIGN §6.4-§6.6, ADRs 012, 017, 032, 061).

    Standalone, same rationale as `BaselineParams` and `ReportingParams`
    above: `jobs.config.config_hash` hashes `dataclasses.asdict(config)`, and
    nothing here varies a signal, an exit, or a cost. Folding these into
    `Config` would move `config_hash` for every config already written to
    `events` — including the live poller's, whose same-day duplicate check
    keys on that hash — in order to name constants that only ever describe
    how capital is simulated *after* the events exist.

    Invariant 9 still binds: `core/arms.py` and `research/benchmarks.py`
    hold no literals, and the 13.3 brief calls out that ADR 092's pattern
    "has already escaped once into `v_positions`."

    **`initial_capital`.** A scale, not a claim. Every reported quantity is
    a ratio (`total_ret`, `sharpe`, `max_drawdown`, `capital_efficiency`)
    except `terminal_value` and the tax numbers, and those are linear in it,
    so the value only fixes the units the `benchmarks` rows are read in.

    **`risk_free_annual`.** A flat 2%, the rate `sharpe` subtracts and the
    rate idle cash accrues at between a trim and its redeploy (DESIGN §6.5).
    It is deliberately a constant rather than a series: the arms are read
    against each other, and threading a daily T-bill series through eight
    simulations adds an ingestion dependency the session does not have.
    2% is roughly the 2010-2023 average 3-month T-bill yield, which spent
    most of that window near zero and finished above 5%. **The limit is
    real and one-directional:** a flat rate understates the cash return in
    2023 and overstates it in 2015, so the trim arm's idle-cash leg is
    mis-stated in both eras, in opposite directions. It is sweepable
    precisely so the sensitivity is measurable rather than assumed.

    **`trim_fraction` and `trim_stoch_threshold`.** DESIGN §6.5's "sell 20%
    on `CONFLUENCE_HIGH` or `%K_full >= 80`." The threshold is *not*
    `ExitParams.exit_stoch_threshold` even though both default to 80: that
    one is exit policy for an open sleeve position, this one decides whether
    to trim a held core position, and an exit-rule sweep must not silently
    move the trim rule with it. Same reasoning ADR 092 applies to the
    signal/exit pair.

    **`dca_tranches`.** DESIGN §6.6's "buy C/12 on the first trading day of
    each month." Named rather than written as 12 so the fixed-schedule arm's
    ramp length is visible. Note the consequence, which is a property of the
    specified rule rather than of this implementation: over a window longer
    than `dca_tranches` months, `dca_fixed` exhausts C early and converges
    toward `dca_lump`.

    **The tax fields.** ADR 032, which is Provisional and stays Provisional.
    37% is the top US federal ordinary bracket and 20% the top long-term
    capital gains bracket; no state tax, no NIIT, and no bracket
    progression. Setting `long_term_tax_rate = 0.238` adds the 3.8% net
    investment income tax, which is the one-field change if you want it.

    `long_term_holding_days` is 365 and the comparison is **strictly
    greater than**, because the US rule is "more than one year." A position
    held for exactly a year is short-term, and an off-by-one here silently
    reclassifies every one-year hold.

    **Why a long-term rate exists at all** (added 2026-08-13, amending
    ADR 032): the ADR named only a short-term rate, because it describes
    the *sleeve*. Session 13 also taxes the benchmark arms, whose holding
    stints average roughly four and a half years. Taxing those at 37%
    overstated buy-and-hold's tax by about 66 points of return on the train
    split and flattered every high-turnover arm measured against it. The
    signal arm's own rate is unaffected: its positions are held five days
    or fewer and are genuinely short-term.

    30 days is the statutory wash-sale window, and it is **calendar** days
    on both sides of the disposal.

    **`irr_*`.** The bisection bracket and tolerance for the DCA arms' XIRR.
    `irr_days_per_year` is 365, not 252: IRR discounts calendar time, so a
    trading-day divisor would inflate every rate by roughly 45%.
    """

    initial_capital: float = 1_000_000.0
    risk_free_annual: float = 0.02
    trading_days_per_year: int = 252

    trim_fraction: float = 0.20
    trim_stoch_threshold: float = 80.0

    dca_tranches: int = 12

    short_term_tax_rate: float = 0.37
    long_term_tax_rate: float = 0.20
    long_term_holding_days: int = 365
    wash_sale_window_days: int = 30

    irr_days_per_year: float = 365.0
    irr_max_rate: float = 100.0
    irr_tolerance: float = 1e-10
    irr_max_iterations: int = 500

    # ADR 061's significance criterion. A field rather than a literal so a
    # one-sided 95% read and this one are two named settings instead of one
    # edited number.
    null_percentile: float = 97.5


DEFAULT_BENCHMARK = BenchmarkParams()
