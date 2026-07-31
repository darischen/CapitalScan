import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib as tomli  # type: ignore
except ImportError:
    import tomli  # type: ignore


@dataclass(frozen=True)
class IndicatorParams:
    bb_window: int = 20
    bb_std: float = 2.0
    bb_ddof: int = 0
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
    require_fast_agreement: bool = False
    fast_agreement_tol: float = 5.0
    price_tolerance: float = 0.0


@dataclass(frozen=True)
class ExitParams:
    max_hold_days: int = 5
    target_pct: float = 0.04
    stop_mode: str = "atr"
    stop_atr_k: float = 1.5
    stop_fixed_pct: float = 0.03
    exit_on_upper_band: bool = True
    exit_on_stoch_80: bool = True
    exit_on_mid_band: bool = False


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
    rel_return_lookback_days: int = 756
    rebalance_freq: str = "Q"


@dataclass(frozen=True)
class StatsParams:
    min_n_eff: int = 30
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


def resolve_config(
    cli_overrides: Optional[dict] = None,
    env_file: Optional[str] = None,
    config_file: Optional[str] = None,
) -> Config:
    cli_overrides = cli_overrides or {}
    config_file = config_file or "config.toml"

    result = {}

    if Path(config_file).exists():
        with open(config_file, "rb") as f:
            file_config = tomli.load(f)
            result.update(file_config)

    env_config = {}
    if env_file:
        import dotenv
        dotenv.load_dotenv(env_file)

    for key in ["indicators", "signals", "exits", "costs", "universe", "stats", "splits"]:
        env_key = f"CAPSCAN_{key.upper()}"
        env_val = os.getenv(env_key)
        if env_val:
            try:
                env_config[key] = json.loads(env_val)
            except json.JSONDecodeError:
                pass

    result.update(env_config)
    result.update(cli_overrides)

    return Config(
        indicators=IndicatorParams(**result.get("indicators", {})),
        signals=SignalParams(**result.get("signals", {})),
        exits=ExitParams(**result.get("exits", {})),
        costs=CostParams(**result.get("costs", {})),
        universe=UniverseParams(**result.get("universe", {})),
        stats=StatsParams(**result.get("stats", {})),
        splits=SplitParams(**result.get("splits", {})),
    )


DEFAULT_CONFIG = Config()
