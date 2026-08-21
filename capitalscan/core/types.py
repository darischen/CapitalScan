from dataclasses import dataclass
from datetime import date
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class Bound(str, Enum):
    LOWER = "lower"
    MID = "mid"
    UPPER = "upper"


class SignalType(str, Enum):
    BB_LOWER_TOUCH = "bb_lower_touch"
    BB_UPPER_TOUCH = "bb_upper_touch"
    STOCH_OVERSOLD = "stoch_oversold"
    STOCH_OVERBOUGHT = "stoch_overbought"
    CONFLUENCE_LOW = "confluence_low"
    CONFLUENCE_HIGH = "confluence_high"
    # ADR 108. The only close-confirmed type: every other member is an
    # intraday touch (ADR 005). `breach_live` never returns this one — the
    # live path has no close to confirm against.
    BEAR_CLOSE_ABOVE_UPPER = "bear_close_above_upper"
    # ADR 144, the long-side mirror (user's request, 2026-08-21):
    # `close > open AND close <= bb_lower[t]`. Same close-confirmed nature,
    # same absence from `breach_live`.
    #
    # **Defined but not enabled.** `SignalParams.enabled_signal_types` does
    # not list it, so `enabled_types` never resolves it and `_types_fired`
    # filters it out. Adding an enum member does not move `config_hash` --
    # the hash is over `dataclasses.asdict(Config)` and a member is not a
    # field, which is the exact trap ADR 108 documents. That is the property
    # relied on here: the type can exist, be tested, and be reviewed while a
    # backtest runs against the current hash, and turning it on later is a
    # deliberate one-line config change that moves the hash on purpose.
    BULL_CLOSE_BELOW_LOWER = "bull_close_below_lower"


class ExitReason(str, Enum):
    TIMEOUT = "timeout"
    TARGET = "target"
    STOP = "stop"
    UPPER_BAND = "upper_band"
    MID_BAND = "mid_band"
    STOCH_80 = "stoch_80"


class EntryKind(str, Enum):
    TOUCH = "touch"
    TOUCH_5M = "touch_5m"
    TOUCH_30M = "touch_30m"
    NEXT_OPEN = "next_open"


@dataclass(frozen=True)
class Bands:
    bb_lower: float
    bb_mid: float
    bb_upper: float
    k_full: float
    d_full: float
    k_fast: float
    atr_14: float


@dataclass(frozen=True)
class SignalHit:
    ticker: str
    ts: date
    signal_type: SignalType
    signal_types_all: tuple
    signal_strength: int
    side: Side
    # None when no band was touched — a stochastic-only signal. Never NaN:
    # `nan != nan` makes two identical hits compare unequal while hashing the
    # same (frozen dataclasses derive __hash__ from fields, and hash(nan) is
    # stable), so a set would silently keep both. DESIGN §3.4, §4.7.
    touch_level: float | None
    pctb: float
    k_full: float


@dataclass(frozen=True)
class ExitResult:
    exit_idx: int
    exit_date: date
    exit_price: float
    reason: ExitReason
    holding_days: int
    mfe: float
    mae: float
    time_to_mfe: int
    ambiguous: bool
