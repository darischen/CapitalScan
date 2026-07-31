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
    touch_level: float
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
