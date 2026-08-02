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


@dataclass(frozen=True)
class SignalParams:
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    require_fast_agreement: bool = False  # ADR 044
    fast_agreement_tol: float = 5.0
    price_tolerance: float = 0.0  # 0.0 == "at or beyond", exact


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
    exit_on_mid_band: bool = False  # ADR 046


@dataclass(frozen=True)
class CostParams:
    slippage_bps: float = 3.0
    commission_per_share: float = 0.0
    borrow_bps_annual: float = 40.0
    short_enabled: bool = True


@dataclass(frozen=True)
class UniverseParams:
    min_mcap_usd: float = 200e9
    min_price: float = 1.0
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

    **`max_shares`.** Set from the highest genuine share count on file:
    Citigroup pre-2011-reverse-split, ~29.2 billion (2011-05-05 filing).
    32 billion gives that roughly 10% headroom. The next value up on file
    is a confirmed-bad one — Alaska Air's three 2011 filings at ~35.8-36
    billion, which are its real ~35.8 million shares with three extra
    zeros — so 32 billion also sits comfortably (~12%) below the lowest
    confirmed-bad value found.

    **Known limit, stated plainly.** These two numbers are close together
    (32B sits between 29.2B genuine and 35.8B corrupted) because a x1,000
    filer error on a company whose real share count is in the tens of
    millions lands in the same absolute neighborhood as a genuine mega-cap's
    real count in the billions — the corruption factor and the market's own
    genuine range overlap at that boundary. A hypothetical x1,000 error on
    a ~15-20 million-share company (15-20 billion after corruption) would
    NOT be caught by `max_shares` alone; nothing in the 73k-row scan found
    an instance of that shape, but the guard cannot rule one out structurally.
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
    max_shares: int = 32_000_000_000


DEFAULT_SHARES_PLAUSIBILITY = SharesPlausibility()
