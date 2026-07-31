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


@dataclass(frozen=True)
class StatsParams:
    min_n_eff: int = 30  # below this a cell is suppressed
    fdr_alpha: float = 0.05
    n_replications_default: int = 200
    n_replications_sweep: int = 50
    reach_targets: tuple = (0.02, 0.03, 0.05, 0.10)
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
