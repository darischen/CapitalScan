# DESIGN.md

System design for `CapitalScan`.

This document describes **what the system is**. `DECISIONS.md` records **why each choice was made** and holds 87 numbered ADRs. Where this document states a rule, the ADR reference is given. If the two ever disagree, `DECISIONS.md` wins and this file is stale.

Last updated: 2026-07-30

---

## 1. System overview

A Bollinger Band and Stochastic Oscillator event-study engine for US mega-cap equities. It detects indicator events, measures what historically followed, and notifies the user. It is advisory only: no execution path exists (ADR 043).

### 1.1 Four planes

| Plane | Latency budget | Runs on | Owns |
|---|---|---|---|
| Ingest and compute | Minutes, offline | Local workstation | Bars, indicators, events |
| Research | Hours, offline | Local workstation | Backtests, statistics, model training |
| Serving | Under 200 ms | Vercel + Neon | Screener, stats lookup, prediction lookup |
| Interaction | Seconds, streaming | Vercel Edge + Anthropic | Chat, tool orchestration |

The rule holding this together: **nothing heavy runs in a request path** (ADR 021). Every number the UI or chatbot shows was computed offline and written to a table. The serving plane does indexed lookups only.

### 1.2 Component map

```
┌──────────────────────────────────────────────────────┐
│ CLIENT (Next.js on Vercel)                           │
│  /  /ticker/[sym]  /research  /forward  /positions   │
│  /chat                                               │
└────────────┬─────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────┐
│ API ROUTES (TypeScript, Vercel)                      │
│  SELECT from Postgres views. No logic. (ADR 070/076) │
└────────────┬─────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────┐
│ SERVING STORE (Neon, free tier, ~150 MB)             │
│  tickers universe events indicators(subset)          │
│  cell_stats predictions outcomes positions           │
└────────────▲─────────────────────────────────────────┘
             │  nightly sync, ~20 s
┌────────────┴─────────────────────────────────────────┐
│ RESEARCH STORE (local Postgres in Docker, ~1.5 GB)   │
│  bars(daily+hourly) indicators(all) events(all       │
│  configs) bar_rejects runs scheduled_runs            │
└────────────▲─────────────────────────────────────────┘
             │
┌────────────┴─────────────────────────────────────────┐
│ JOBS + RESEARCH (local, Python)                      │
│  jobs/    ingest, indicators, events, poll, sync     │
│  research/ backtest, stats, train                    │
│  handlers/ shared query layer, also serves MCP       │
└────────────▲─────────────────────────────────────────┘
             │
┌────────────┴─────────────────────────────────────────┐
│ CORE (pure Python, no IO — ADR: §3.1)                │
│  config types indicators signals exits returns       │
│  costs universe                                      │
└──────────────────────────────────────────────────────┘
```

`core/signals.py` is the load-bearing module. One function decides whether a bar or a live tick breaches a band. The poller imports it. The backtest imports it. No second implementation exists (ADR 006). Every time these drift apart in a system like this, the live path fires on events the backtest never measured and it goes unnoticed for months.

### 1.3 Phase order

| Phase | Scope | Gate |
|---|---|---|
| 1 | Ingest, indicators, event detection, `scan()`, CLI | `ie scan` returns correct events |
| 2 | Live poller, notifications, positions, live call overlay | Breach detected and delivered within one interval |
| 3 | Exit engine, MFE/MAE, full event table | Exit invariants hold on 10k generated cases |
| 4 | Statistics, baselines, benchmarks, FDR | Three-arm comparison renders; every cell reports n_eff/CI/q |
| 5 | Frontend, chat, tools, MCP | Tools schema-valid; validator rejects naked probability |
| 6 | Quantile model, calibration, forward log | Model beats cell lookup on Brier, or lookup ships alone |
| 7 | Options: synthetic then Polygon-validated | Synthetic pricing error published |

---

## 2. Data contracts

### 2.1 Sources

| Need | Source | Auth | Rate limit | Refresh |
|---|---|---|---|---|
| Daily OHLCV | yfinance | none | ~0.5 req/s, batch 50 | Nightly, 5-day overlap |
| Hourly OHLCV | yfinance | none | ~0.5 req/s | Nightly, 2-day overlap |
| Live quotes | yfinance batch | none | batch 50-100 | Every 5 min, market hours |
| VIX, SPX | yfinance `^VIX`, `^GSPC` | none | — | Nightly |
| Index membership | Wikipedia scrape | none | — | Once, frozen (ADR 055) |
| Shares outstanding | SEC XBRL company facts | User-Agent | 8 req/s | Weekly, new filings |
| Historical earnings | SEC EDGAR 8-K filings | User-Agent | 8 req/s | Once, frozen (ADR 036) |
| Forward earnings | Finnhub calendar | API key | 0.8 req/s | Weekly, 90-day window |
| Option chains (Phase 2) | yfinance | none | on demand | Live only, never stored historically |

`SEC_USER_AGENT` must contain a real email address. SEC blocks requests without it, and the block is IP-level and persistent.

**Removed 2026-08-01: Stooq cross-check.** This table used to carry a `Cross-check | Stooq` row (2 req/s, 20-ticker sample, integration test). As of 2026-08-01 Stooq serves a JavaScript proof-of-work challenge to `requests` on every endpoint tried (`stooq.com` and `stooq.pl`, with and without a browser User-Agent), verified against the vendor blocking automated access rather than a network quirk. The owner decided to remove the check and accept single-source (Yahoo) data rather than keep a permanently-failing safeguard or add a keyed vendor. See §2.3 and §4.11 for the corresponding removals.

### 2.2 Price adjustment policy

Two series, used for different purposes (ADR 041, and §1.2 of the walkthrough):

| Purpose | Series | Reason |
|---|---|---|
| Indicator computation | Split-adjusted close (`close`) | Matches the chart a trader sees and the live quote the poller compares against |
| Return measurement | Total-return adjusted (`adj_close`) | Dividends are real return and belong in the payoff |
| Live band comparison | Split-adjusted | The live quote is a raw price, so the band must live in raw price space |

Computing Bollinger bands on a dividend-adjusted series produces band levels the market never traded at. On a 2% yielder over 10 years the adjusted series sits roughly 20% below the raw one at the start, so every historical band level shifts and the signal fires at prices nobody saw.

Splits are adjusted in both. A 10-for-1 split on unadjusted history creates a fake 90% drawdown, a fake lower-band breach, and a fake %K of zero. NVDA split twice inside the window; AAPL, AMZN, and GOOGL once each.

`adj_factor = adj_close / close` is stored per bar so either series is reconstructible. Never store only a derived series.

**Exception, documented:** `realized_vol` takes total-return adjusted close because it measures return dispersion rather than price level. This is the only place the two series mix inside one module and it requires a comment in the code.

### 2.3 Validation rules

Every rule runs on ingest and writes to `bar_rejects` rather than silently dropping.

| Check | Threshold | Action |
|---|---|---|
| Missing bar on an NYSE trading day | any | Flag, refetch, then mark gap |
| Zero or null volume | any | Flag, keep bar, exclude from `vol_z` |
| `high < low` | any | Reject |
| `close` outside `[low, high]` | any | Reject |
| `open` outside `[low, high]` | any | Reject |
| Absolute daily return | > 40% | Flag, check `corporate_actions`, reject if unexplained |
| Split detected without a `corporate_actions` entry | any | Reject and alert |
| Duplicate `(ticker, ts, interval)` | any | Upsert, keep latest fetch |
| Price below $1 | any | Flag, exclude from trade universe |
| Identical close for 5+ days | any | Flag, exclude those bars from indicator windows |

**Removed 2026-08-01:** `Stooq close disagreement | > 0.5% on > 1% of bars | Flag for manual review` was here. Removed with the rest of the Stooq cross-check (see §2.1) once the vendor began blocking automated requests; the pipeline is single-source on Yahoo, not covered by an independent cross-check.

Trading calendar comes from `pandas_market_calendars` (NYSE). Do not infer trading days from the data; that hides exactly the gaps you want to find.

### 2.4 Point-in-time market cap

Shares outstanding come from the SEC XBRL company facts API (`CommonStockSharesOutstanding`), which is free, reaches back past 2009, and requires a CIK-to-ticker mapping.

At evaluation time, use the latest filing with `filed_on < as_of`. This introduces a real ~45-day lag for 10-Qs which is correct and unavoidable.

The rejected alternative was holding current shares constant backward, which is wrong in the worst direction: AAPL retired roughly 35% of its shares over 10 years, so its 2016 cap would come out ~50% too low, and cap is a filter criterion.

An ablation control exists: 20-day average dollar volume as a liquidity proxy, testing whether cap adds anything over the trend filter (ADR 014).

### 2.5 Schema

```sql
-- ============ Reference ============

CREATE TABLE trading_days (
  d date PRIMARY KEY,
  is_early_close boolean NOT NULL DEFAULT false
);

CREATE TABLE tickers (
  ticker text PRIMARY KEY,
  cik text,
  name text,
  sector text,
  industry text,
  exchange text,
  first_bar date,
  last_bar date,
  is_active boolean NOT NULL DEFAULT true,
  delisted_on date
);

CREATE TABLE corporate_actions (
  ticker text NOT NULL REFERENCES tickers(ticker),
  ex_date date NOT NULL,
  action_type text NOT NULL CHECK (action_type IN ('split','dividend')),
  ratio numeric,
  amount numeric,
  PRIMARY KEY (ticker, ex_date, action_type)
);

CREATE TABLE shares_outstanding (
  ticker text NOT NULL REFERENCES tickers(ticker),
  filed_on date NOT NULL,
  period_end date,
  shares bigint NOT NULL,
  source text NOT NULL,
  PRIMARY KEY (ticker, filed_on)
);

CREATE TABLE earnings (
  ticker text NOT NULL,
  report_date date NOT NULL,
  session text CHECK (session IN ('bmo','amc','unknown')),
  source text NOT NULL,
  confidence text,
  PRIMARY KEY (ticker, report_date)
);

-- ============ Universe ============

CREATE TABLE universe (
  ticker text NOT NULL REFERENCES tickers(ticker),
  as_of date NOT NULL,
  in_train boolean NOT NULL,
  in_trade boolean NOT NULL,
  mcap_usd numeric,
  mcap_rank int,
  adv_20d_usd numeric,
  crit_mcap boolean,
  crit_above_sma200 boolean,
  crit_sma200_slope boolean,
  crit_rel_return boolean,
  crit_rev_growth boolean,
  PRIMARY KEY (ticker, as_of)
);

-- ============ Market data ============

CREATE TABLE bars (
  ticker text NOT NULL REFERENCES tickers(ticker),
  ts timestamptz NOT NULL,
  interval text NOT NULL DEFAULT '1d',
  open numeric(12,4) NOT NULL,
  high numeric(12,4) NOT NULL,
  low numeric(12,4) NOT NULL,
  close numeric(12,4) NOT NULL,
  adj_close numeric(12,4) NOT NULL,
  volume bigint,
  adj_factor numeric(12,6) NOT NULL DEFAULT 1.0,
  is_terminal boolean NOT NULL DEFAULT false,
  source text NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  run_id text,
  PRIMARY KEY (ticker, ts, interval),
  CHECK (high >= low),
  CHECK (close BETWEEN low AND high),
  CHECK (open  BETWEEN low AND high)
);
CREATE INDEX bars_ts_idx ON bars (ts);

CREATE TABLE bar_rejects (
  id bigserial PRIMARY KEY,
  ticker text, ts timestamptz,
  rule text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('flag','reject')),
  payload jsonb,
  run_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE market_days (
  ts date PRIMARY KEY,
  spx_close numeric(12,4),
  spx_ret_1d numeric(12,6),
  vix_close numeric(12,4),
  vix_pct_252d numeric(12,6)
);

-- ============ Derived ============

CREATE TABLE indicators (
  ticker text NOT NULL,
  ts timestamptz NOT NULL,
  interval text NOT NULL DEFAULT '1d',
  bb_mid numeric(12,6), bb_upper numeric(12,6), bb_lower numeric(12,6),
  bb_pctb numeric(12,6), bb_width numeric(12,6), bb_width_pct numeric(12,6),
  k_fast numeric(12,6), d_fast numeric(12,6),
  k_full numeric(12,6), d_full numeric(12,6),
  k_cross_up boolean, k_cross_down boolean,
  sma_200 numeric(12,6), sma200_slope_60 numeric(12,6),
  atr_14 numeric(12,6),
  rv_20d numeric(12,6), rv_pct_252d numeric(12,6),
  vol_z_20d numeric(12,6),
  dd_52w numeric(12,6),
  days_to_earnings int,
  computed_at timestamptz NOT NULL DEFAULT now(),
  run_id text,
  PRIMARY KEY (ticker, ts, interval)
);
CREATE INDEX indicators_ts_idx ON indicators (ts);

-- ============ Audit ============

CREATE TABLE runs (
  run_id text PRIMARY KEY,
  job text NOT NULL,
  git_sha text NOT NULL,
  params jsonb NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  status text CHECK (status IN ('running','ok','failed')),
  rows_written bigint,
  notes text
);

CREATE TABLE scheduled_runs (
  job text NOT NULL,
  scheduled_for timestamptz NOT NULL,
  actual_start timestamptz,
  delay_seconds int,
  status text,
  run_id text,
  PRIMARY KEY (job, scheduled_for)
);

CREATE TABLE quotes_live (
  ticker text NOT NULL,
  ts timestamptz NOT NULL,
  price numeric(12,4) NOT NULL,
  breached text,                    -- null | 'lower' | 'upper'
  breach_depth_atr numeric(12,6),   -- (band − price) / ATR_14, Phase 6 feature
  event_id bigint,
  PRIMARY KEY (ticker, ts)
);

-- Immutable record of what the user was told, per ADR 029.
-- Lets the forward log compare the recommendation against the outcome.
CREATE TABLE signal_reports (
  id bigserial PRIMARY KEY,
  event_id bigint,
  ticker text NOT NULL,
  fired_at timestamptz NOT NULL,
  state_json jsonb NOT NULL,        -- full indicator state at fire time
  cell_id text,
  prediction_id bigint REFERENCES predictions(id),
  call_overlay_json jsonb,          -- live chain snapshot, Phase 2
  channels_sent text[],
  model_version text,
  git_sha text
);

CREATE TABLE poller_sessions (
  session_date date PRIMARY KEY,
  started_at timestamptz,
  ended_at timestamptz,
  ticks_completed int,
  ticks_expected int,
  coverage_pct numeric(6,3),
  notes text
);
```

Event, statistics, prediction, and position tables are defined in §5.7, §6.8, §7.7, and §8.5.

### 2.6 Idempotency

Every job is safe to rerun. Three rules:

1. All writes are `INSERT ... ON CONFLICT DO UPDATE`. No plain inserts on tables with natural keys.
2. Recompute is windowed. Indicators for date *t* need 272 trading bars of history, so a recompute expands its read range by `max(warmup) × 1.6` calendar days beyond the write range.
3. Deletes are scoped by `run_id`, never by date alone.

This is what makes ADR 080's catch-up scheduling safe: a nightly job running at 11 AM the following day produces correct results.

### 2.7 Warmup

Ingest starts 2009-01-01. Event detection starts 2010-01-01 (ADR 040).

The longest chain is `rv_pct_252d`, a 252-day percentile of a 20-day realized volatility, requiring 272 prior bars. `sma200_slope_60` requires 260. One extra year of bars removes the null boundary entirely.

**Enforcement:** a test asserts zero nulls in every required indicator column on or after 2010-01-01 for any ticker with continuous coverage.

---

## 3. Core library

### 3.1 Purity rule

`core/` performs **no IO**. No database, no HTTP, no file reads, no clock access. Every function takes data in and returns data out.

Everything follows from this. The backtest and the poller call identical functions with different data sources, which is what makes ADR 006 testable rather than merely intended. It also makes the whole library testable without fixtures on disk.

`jobs/` and `research/` own all IO.

### 3.2 Conventions

| Convention | Choice |
|---|---|
| Library | pandas |
| Numeric type in compute | float64 (ADR 041) |
| Numeric type in storage | `numeric(12,4)` prices, `numeric(12,6)` indicators |
| Index | `pd.DatetimeIndex`, tz-naive for daily, tz-aware for hourly |
| Scope per call | One ticker |
| Column names | Exactly the SQL column names, no translation layer |
| Missing values | `np.nan`, never filled |
| Price rounding | Round to 4 decimals before comparison |
| Mutation | Never in place |

### 3.3 `core/config.py`

Every constant lives here as a frozen dataclass. No magic numbers anywhere else in the repo. Each backtest run serializes its full config into `runs.params`, so reproducing a result means reading one JSON blob.

**`core/config.py` contains dataclasses only.** No `open()`, no `os.getenv()`, no `Path.exists()`, no `dotenv`. Its only import is `dataclasses`. This is §3.1 applied to itself.

Resolution lives in `jobs/config.py`, which owns the precedence chain (CLI flag, then environment variable, then `config.toml`, then dataclass default) and returns populated frozen dataclasses. `core/` receives config objects as arguments and never fetches them.

`jobs/config.py` raises on malformed input rather than falling back to defaults: parse failures, unknown keys, and type-coercion failures all raise `ConfigError`. Silent fallback is not survivable here, because `config_hash` is computed from the resolved config and stamped on every generated row. A silently-defaulted config yields a hash that describes parameters which never ran, and the record is internally consistent, so no later check can catch it.

Consequence for tests: resolution tests must pass an explicit `config_file` and clear `CAPSCAN_*` via `monkeypatch.delenv`. A resolver defaulting to ambient CWD and environment makes the suite depend on the machine it runs on, and a green CI proves only that no stray variable happened to be set.

```python
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
    exit_stoch_threshold: float = 80.0  # long exit, exit policy
    exit_stoch_threshold_short: float = 20.0  # short exit, independent (ADR 016)
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
```

### 3.4 `core/types.py`

```python
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
    signal_types_all: tuple[SignalType, ...]
    signal_strength: int
    side: Side
    touch_level: float | None  # None when no band was touched
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
```

`Bands` is what the poller reads from Postgres and what the backtest reads from a DataFrame row. Identical shape on both paths; this is the mechanism behind ADR 006.

**`touch_level` is `float | None`, never NaN.** A stochastic-only signal has no band touch, and that absence is `None`. Encoding it as NaN violates §3.11 in spirit and breaks equality: `nan != nan`, so two structurally identical hits compare unequal while hashing identically, since frozen dataclasses derive `__hash__` from fields and `hash(nan)` is stable. A set would keep both. See §4.7 for the consequence this would have had on debounce.

### 3.5 `core/indicators.py`

Registry-based (ADR 051):

```python
@register("rsi_14", deps=["close"], warmup=14, dtype="numeric(12,6)")
def rsi_14(bars: pd.DataFrame, p: IndicatorParams) -> pd.Series: ...
```

`compute_all(bars, p)` iterates the registry, one ticker in, one row per bar out, columns matching the `indicators` table.

**Pinned formulas (ADR 004).** Written here, referenced everywhere, never duplicated.

Bollinger, 20-period, 2 population standard deviations, on split-adjusted close:

```
mid   = SMA_20(C)
upper = mid + 2·σ_20
lower = mid − 2·σ_20
%B    = (C − lower) / (upper − lower)
BW    = (upper − lower) / mid
```

Fast Stochastic (14, 3):

```
%K_fast = 100 · (C − L_14) / (H_14 − L_14)
%D_fast = SMA_3(%K_fast)
```

Full Stochastic (14, 3, 3):

```
%K_full = SMA_3(%K_fast)
%D_full = SMA_3(%K_full)
```

Support: `SMA_200`, `slope_60(SMA_200)`, `ATR_14`, `RV_20d`, `DD_52w`, `vol_z_20d`.

**Three specifics that decide correctness:**

1. **Which price series.** `bollinger`, `stochastic`, `atr`, `drawdown_from_high`, and `sma` take split-adjusted OHLC. `realized_vol` takes total-return adjusted close (see §2.2 exception).
2. **Stochastic division by zero.** When `H_14 == L_14` (halted or fully flat session), return `NaN`. Not 50, not 0. A filled value creates a fake extreme.
3. **Percentile convention.** `rolling_percentile` returns the fraction of the trailing window at or below the current value, in `[0, 1]`, strict trailing window. Used by `bb_width_pct` and `rv_pct_252d`.

### 3.6 `core/signals.py`

The shared contract. Two public functions over one private primitive.

```python
def _breach(price: float, level: float, bound: Bound, tol: float = 0.0) -> bool:
    """The single comparison. Everything routes through here."""
    if np.isnan(price) or np.isnan(level):
        return False
    p, l = round(price, 4), round(level, 4)
    if bound is Bound.LOWER:
        return p <= l * (1 + tol)
    if bound is Bound.UPPER:
        return p >= l * (1 - tol)
    raise ValueError(bound)


def detect(bar: pd.Series, ind: pd.Series, sp: SignalParams) -> list[SignalHit]:
    """Backtest path. One bar plus its t-1 indicator row."""


def breach_live(price: float, bands: Bands, sp: SignalParams) -> list[SignalType]:
    """Live path. Current quote plus bands fixed at the prior close."""
```

**Where the paths differ, and why it is still one contract.** `detect` reads `bar.low` and `bar.high` for band conditions, since a daily bar's extremes are the intraday touch. `breach_live` reads the current price. Both call `_breach` with the same level and bound. The predicate is shared; only the price argument differs, which is correct because the live price is a point sample of the same intraday path.

**The look-ahead trap.** %K derives from the close, so it is only defined at session end. During the session, `breach_live` evaluates %K from the prior close as a static qualifier, exactly like the band levels. **The backtest must do the same: evaluate `k_full` at bar t−1, not bar t.** Evaluating at t uses the close to decide an intraday entry.

This is the single most likely place this project develops a silent bias. It is guarded twice: inside `core` and again at the job level (§4.7), and it has a dedicated test (`TESTS.md` §3.1).

Pinned confluence definition:

```
CONFLUENCE_LOW(t)  ⟺  low_t  ≤ bb_lower_{t−1}  ∧  %K_full_{t−1} ≤ 20
CONFLUENCE_HIGH(t) ⟺  high_t ≥ bb_upper_{t−1}  ∧  %K_full_{t−1} ≥ 80
```

`require_fast_agreement` optionally adds `|%K_fast_{t−1} − %K_full_{t−1}| ≤ 5.0` (ADR 044).

**Multi-signal (ADR 057).** Multiple signals on the same ticker on the same trading day emit **one** hit. `signal_type` carries the most specific; `signal_types_all` carries every concurrent one; `signal_strength` counts them. Specificity ranking, fixed: `CONFLUENCE_LOW` > `BB_LOWER_TOUCH` > `STOCH_OVERSOLD`, mirrored high.

**Crossover (ADR 045).** `k_cross_up` and `k_cross_down` are computed, stored, displayed, and available as model features. They never enter `detect()` and never affect a recommendation.

Debounce lives in the job layer, not `core`, because it needs cross-day state.

### 3.7 `core/exits.py`

```python
def resolve_exit(
    entry_price: float,
    entry_idx: int,
    side: Side,
    fwd_bars: pd.DataFrame,       # rows entry_idx+1 .. entry_idx+max_hold
    fwd_ind: pd.DataFrame,        # same index
    atr_at_entry: float,
    ep: ExitParams,
) -> ExitResult
```

Full evaluation order is specified in §5.5. Separated from `returns.py` so it gets its own test suite; it is the most error-prone module in the system.

### 3.8 `core/returns.py`

```python
def forward_returns(close: pd.Series, horizons: list[int]) -> pd.DataFrame
def mfe_mae(entry_price, side, fwd_bars) -> tuple[float, float, int]
def realized_return(entry_price, exit_price, side) -> float
def entry_price_for(kind, bar, next_bar, touch_level, hourly=None) -> float
```

**Coverage constraint on `entry_price_for`.** `TOUCH` and `NEXT_OPEN` compute from daily bars across 2010-2026. `TOUCH_5M` and `TOUCH_30M` need hourly data, which reaches back only ~730 days.

So the entry-timing sweep splits into two claims: touch-vs-next-open runs on 16 years; the 5-minute and 30-minute comparison runs on the hourly subset (roughly 2024 forward) and is reported with that limitation attached. The subset grows daily, which is the argument behind ADR 037.

Pre-2024, the fine-grained kinds produce nulls, never a fabricated value.

### 3.9 `core/costs.py`

```python
def apply_costs(gross_ret, side, holding_days, cp) -> float
def borrow_cost(holding_days, cp) -> float
def slippage(price, cp) -> float
```

Slippage applies on both legs. Borrow applies to shorts only at `bps_ann × d/252` — roughly 0.8 bp over a 5-day hold at 40 bps annualized. Negligible for mega-caps, and it belongs in the code so the long-vs-short comparison stays honest.

### 3.10 `core/universe.py`

```python
def evaluate_criteria(ind_row, mcap, sector_median_return,
                      rev_growth_positive, up) -> dict[str, bool]
def is_tradeable(criteria: dict[str, bool], required: set[str] | None = None) -> bool
```

`evaluate_criteria` returns the five ADR 014 criteria **separately** rather than a single boolean. `is_tradeable` accepts a subset of required criteria, which makes the ablation study a config change rather than a code change.

The five criteria, all evaluated on information available at t−1:

1. Market cap above $200B as of the prior quarter-end
2. Close above the 200-day SMA
3. 200-day SMA slope positive over the trailing 60 days
4. Trailing 3-year total return above the sector median
5. Revenue growth positive over the last four reported quarters

### 3.11 Null policy

**Never fill. Never forward-fill. Never interpolate.**

Warmup produces NaN for the first ~272 bars per ticker; ADR 040 handles this by starting ingest a year early.

Event construction requires every feature in the row to be non-null. A null in any indicator column drops the candidate and writes to `bar_rejects` with a reason. Silent drops are the failure mode, so the drop is always logged.

Delisted tickers simply end. No padding, no carry-forward. An open position on a delisting date exits at the last available close with reason `TIMEOUT` and `is_terminal` set.

### 3.12 Performance budget

| Operation | Volume | Expected on 3700X |
|---|---|---|
| `compute_all` per ticker | ~4,300 bars | 15 ms |
| All indicators, 750 tickers | 3.2M bars | 12 s single-thread, ~2 s on 14 workers |
| Event detection, full history | 3.2M bar-rows | 20 s |
| Exit resolution | ~40k events | 8 s |
| One full backtest config | end to end | under 60 s |
| Full 18-config sweep | 18 configs | ~4-5 min |

Peak memory holds one ticker's bars plus indicators, under 5 MB. Loading all 750 at once is ~1.5 GB against 32 GB.

**Implication:** no chunking, no streaming, no Dask, no distributed anything. Load a ticker, compute, write, move on, 14 at a time with `ProcessPoolExecutor`.

---

## 4. Jobs

### 4.1 Inventory

One CLI entry point, `cscan`, built on Typer. Every job is idempotent and writes a `runs` row.

| Job | Command | Cadence | Runtime |
|---|---|---|---|
| `calendar` | `cscan calendar --through 2027` | Yearly | 2 s |
| `tickers` | `cscan tickers --refresh` | Monthly | 3 min |
| `membership` | `cscan membership --backfill` | Once, frozen | 5 min |
| `bars_daily` | `cscan bars --daily --lookback 5` | Nightly | 4 min |
| `bars_hourly` | `cscan bars --hourly --lookback 2` | Nightly | 6 min |
| `actions` | `cscan actions --lookback 30` | Nightly | 2 min |
| `market` | `cscan market --lookback 5` | Nightly | 5 s |
| `shares` | `cscan shares --since-last` | Weekly | 20 min |
| `earnings_hist` | `cscan earnings --historical` | Once, frozen | 90 min |
| `earnings_fwd` | `cscan earnings --forward 90` | Weekly | 8 min |
| `indicators` | `cscan indicators --lookback 5` | Nightly | 30 s |
| `universe` | `cscan universe --quarter 2026Q3` | Quarterly | 10 s |
| `events` | `cscan events --lookback 5` | Nightly | 20 s |
| `sync` | `cscan sync --to-serving` | Nightly | 20 s |
| `poll` | `cscan poll --interval 300` | Market hours | continuous |

### 4.2 Fetcher contract

All external IO routes through one module so retry, rate limiting, and caching exist once.

```python
class Fetcher(Protocol):
    name: str
    rate_limit_per_sec: float

    def fetch(self, **kwargs) -> pd.DataFrame: ...


def with_retry(fn, *, attempts=4, base_delay=2.0, jitter=True): ...
def rate_limited(fn, *, per_sec: float): ...
def cached(fn, *, key_fn): ...
```

Retry: exponential backoff at 2/4/8/16 s with jitter. Retry on connection errors, timeouts, HTTP 429, HTTP 5xx. **Never retry on 404** — the ticker does not exist, and that belongs in `bar_rejects`.

**Disk cache.** Every raw response lands in `data/cache/{source}/{key}.parquet` before parsing. Backfill re-runs then cost zero network calls, turning a 40-minute backfill into a 90-second replay while iterating on the parser. Roughly 11,300 files totalling ~1.2 GB, dominated by 9,750 small hourly files. Gitignored, disposable, excluded from backups (ADR 081).

### 4.3 `bars_daily`

```
per batch of 50 tickers:
  1. yf.download(tickers, start, end, auto_adjust=False,
                 actions=True, group_by="ticker", threads=False)
  2. split the multi-index frame per ticker
  3. derive adj_factor = adj_close / close
  4. run §2.3 validation
  5. upsert clean rows; write flagged/rejected to bar_rejects
```

`auto_adjust=False` is **mandatory**. The default `True` overwrites OHLC with adjusted values and destroys the split-adjusted series that §2.2 requires for band levels.

`threads=False` because yfinance's internal threading triggers rate limiting faster than sequential batching, and the batch is already the parallelism.

**Two failure modes needing explicit handling:**

- *Partial batch failure.* yfinance returns NaN columns for failed tickers inside an otherwise successful batch rather than raising. Detect by checking for an all-NaN close series per ticker, then retry those individually.
- *Silent truncation.* Requesting 2009-2026 for a ticker that listed in 2014 returns 2014 forward with no warning. Record `first_bar` and `last_bar` on every run, and alert when `last_bar` falls more than 5 trading days behind the calendar — that is the delisting signal.

### 4.4 `bars_hourly`

Same shape, three differences. Yahoo caps hourly at 730 days total and 60 days per request, so backfill is 13 sequential windows per ticker: ~9,750 requests at 0.5 req/s ≈ 5.4 hours. Run overnight, once, checkpointed per ticker.

Timestamps are exchange-local and tz-aware. Normalize to `America/New_York` and store as `timestamptz`, `interval = '1h'`. **Regular session only (09:30-16:00).** Pre-market and after-hours bars are dropped, because band comparison in those sessions is meaningless against a prior-close band.

### 4.5 `indicators`

The only job with a lookback subtlety, and getting it wrong produces silent nulls.

```python
def run(tickers, target_start, target_end, run_id):
    read_start = target_start - timedelta(days=ceil(max_warmup() * 1.6))
    for ticker in tickers:
        bars = read_bars(ticker, read_start, target_end)
        if len(bars) < 280:
            reject(ticker, "insufficient_history")
            continue
        ind = core.indicators.compute_all(bars, PARAMS)
        ind = ind.loc[target_start:target_end]  # write window ≠ read window
        upsert(ind)
```

The read window and write window differ **by design**. That asymmetry is the whole point, and `max_warmup()` comes from the registry so adding a longer indicator adapts the job automatically (ADR 051).

Parallelism: `ProcessPoolExecutor(max_workers=14)`, leaving two threads for Postgres and the OS.

Merge step: `days_to_earnings` needs the `earnings` table; `spx_ret_1d` and VIX need `market_days`. Join after per-ticker computation in a single vectorized pass, since neither is ticker-parallel.

### 4.6 `universe`

Runs quarterly, evaluating each ticker as of the quarter-end using only prior data.

**Three data hazards:**

- *Shares outstanding lag.* Use the latest filing with `filed_on < as_of`. A filing dated after `as_of` is correctly invisible. ~45-day lag for 10-Qs is real and unavoidable.
- *Sector median return.* Computed across tickers in the same sector present at `as_of`, using only their trailing 756-day returns. Sectors with fewer than 5 members fall back to the universe median, flagged.
- *Revenue growth.* Needs four quarters from XBRL. Fewer than four available means the criterion is `null`, not `False`. `is_tradeable` treats null as failing, while the audit log distinguishes the two.

### 4.7 `events`

```
per ticker, per bar in range:
  1. read bar t and indicator row t−1
  2. skip if any required indicator field is null → bar_rejects
  3. skip if universe.in_trade is false for the enclosing quarter
  4. core.signals.detect(bar_t, ind_{t−1}, SIGNAL_PARAMS)
  5. debounce on the explicit key (ticker, signal_date, bound)
     -- never on the SignalHit dataclass itself
  6. cluster tagging (§5.3)
  7. write event rows
```

Step 1's t−1 read is the look-ahead guard from §3.6, enforced **again** here at the job level. Two layers, because this is the highest-risk silent failure in the system.

**Debounce keys on a tuple, not the dataclass.** The key is `(ticker, signal_date, bound)`. Deduping on the whole `SignalHit` is an implementation temptation that was never the spec, and it fails on any float field: a NaN member hashes stably but compares unequal, so a set silently keeps duplicates. Keying explicitly is immune to future field additions.

### 4.8 `poll`

```
09:15  load today's bands: read indicators at the last closed session for
       every in_trade ticker into memory (~60 rows, <1 MB)
       load the 11 model artifacts (ADR 069)
09:30  begin loop
every 300 s:
       batch quote all in_trade tickers (1-2 requests)
       core.signals.breach_live(price, bands, params) per ticker
       debounce against today's fired set (state in Postgres, not memory)
       on new breach:
         compute live features incl. breach depth
         run live inference (11 models, <1 ms)
         write quotes_live + event, emit notification, store signal report
16:00  stop; write poller_sessions row
```

Bands are read once at open and never recomputed intraday. The loop is two float comparisons per ticker per tick, so compute is not the constraint at any interval. The real constraints are the yfinance rate limit and quote staleness (up to 15 minutes on some symbols), which is why 5 minutes rather than 1 (ADR 024).

Debounce state lives in Postgres so a restart mid-session does not re-fire already-sent events.

### 4.9 Failure handling

| Failure | Behavior |
|---|---|
| Single ticker fetch fails after retries | Log to `bar_rejects`, continue batch, report at end |
| Entire source unreachable | Fail the job, `runs.status='failed'`, alert |
| Validation rejects > 1% of rows | Fail the job, do not write, alert |
| Indicator job finds insufficient history | Skip ticker, log, continue |
| Poll loop exception | Log, sleep one interval, continue; 3 consecutive failures alerts and exits |
| Postgres connection lost | Retry 3× over 30 s, then fail |

Every job returns a report and exits nonzero on failure.

### 4.10 Progress reporting

Any job over 30 seconds uses `rich.progress` showing current item, completed/total, elapsed, ETA, and running error count. Parallel jobs render a live per-worker table. `--quiet` emits one structured JSON line per 100 items for cron contexts. Jobs over ~10 minutes checkpoint to disk so an interrupt resumes (ADR 052).

### 4.11 Backfill runbook

```bash
cscan db migrate
cscan backfill --all --through-validate   # ~50 min, stops at the gate
# read the validation report, confirm
cscan backfill --all --resume             # ~2.5 hr
cscan bars --hourly --backfill            # ~5.4 hr, overnight, checkpointed
```

`cscan validate --report` is a deliberate human gate: reject counts by rule and coverage per ticker. Proceeding past bad data costs more than the pause. `--yes` skips it for unattended reruns.

(Until 2026-08-01 this gate also ran a Stooq cross-check on a 20-ticker sample; removed when the vendor began blocking automated requests — see §2.1.)

Dependency graph:

```
A trading_days ──┐
B tickers ───────┼──▶ D bars ──┬──▶ I indicators ──▶ J universe ──▶ K events
C membership ────┘             │          ▲
E corp_actions ────────────────┘          │
F market_days ────────────────────────────┤
G shares_out ─────────────────────────────┤
H earnings ───────────────────────────────┘
```

### 4.12 Refresh cadence

| Table | Cadence | Scope per run | Frozen |
|---|---|---|---|
| `trading_days` | Yearly | Next calendar year | No |
| `tickers` | Monthly | New listings, delistings | No |
| `universe` (membership history) | Never | Wikipedia history | Yes |
| `bars` daily | Nightly | Last 5 days, overlapping | No |
| `bars` hourly | Nightly | Last 2 days | No |
| `corporate_actions` | Nightly | Last 30 days | No |
| `market_days` | Nightly | Last 5 days | No |
| `shares_outstanding` | Weekly | New filings only | No |
| `earnings` historical | Never | 2010 → backfill date | Yes |
| `earnings` forward | Weekly | Next 90 days | No |
| `indicators` | Nightly | Last 5 days, expanded read window | No |
| `universe` evaluation | Quarterly | Current quarter | No |

Overlapping windows mean a missed run self-heals on the next one. Upsert makes the overlap free.

---

## 5. Backtest engine

### 5.1 Contract

A deterministic function from config to an event table (ADR 060):

```
backtest(config, bars, indicators, universe) -> events_df
```

It computes **no statistics**, selects **no parameters**, and knows nothing about baselines. Those live in §6. A sweep runs the engine 18 times; the statistics layer runs once over the union.

### 5.2 Pipeline order

```
per ticker (parallel, 14 workers):
   1. load bars[2009..], indicators[2010..]
   2. join universe membership by quarter
   3. candidate scan          → raw signal rows
   4. eligibility filter      → tradeable, non-null, in-window
   5. debounce                → one per ticker/bound/day
   6. cluster tagging         → cluster_id, seq_in_cluster
   7. entry resolution        → 4 entry prices per event
   8. exit resolution         → per entry kind, per config
   9. path metrics            → MFE, MAE, time-to-MFE, reachability
  10. cost application        → slippage, borrow
  11. context tagging         → regime, drawdown bucket, era
  12. split assignment        → train/validate/holdout by date
post-pass (single-threaded):
  13. cofire_count            → group by (signal_date, signal_type)
```

Steps 3-6 reduce. Steps 7-11 enrich. Step 12 labels. Order is fixed here because getting 5 and 6 wrong silently changes the event count.

### 5.3 Cluster tagging

Overlapping events on one ticker are **tagged, not suppressed** (ADR 056).

```sql
cluster_id      bigint,   -- shared across overlapping events on one ticker
seq_in_cluster  int,      -- 1 = first touch, 2 = second, ...
is_cluster_head boolean,  -- true only for seq 1
days_since_head int
```

| Consumer | Filter | Why |
|---|---|---|
| Primary statistics | `is_cluster_head = true` | Non-overlapping, clean standard errors |
| Secondary statistics | all events | Measures whether adds improve outcomes |
| Live advisory | all events | Fires on every touch; the user decides |
| Pyramiding variant | all events, sized | A separate strategy, post-Phase 4 |

This buys a question the original blocking design could not ask: does the second touch outperform the first? A higher hit rate on `seq_in_cluster = 2` validates averaging down with a number; a lower one identifies the falling-knife case.

Note that filtering to cluster heads makes events strictly non-overlapping, which is why no Newey-West adjustment is needed (ADR 007).

### 5.4 Entry resolution

| Kind | Price | Data | Coverage |
|---|---|---|---|
| `TOUCH` | band level from t−1, or `open` if gapped through | Daily | 2010-2026 |
| `NEXT_OPEN` | `open` at t+1 | Daily | 2010-2026 |
| `TOUCH_5M` | interpolated from the first hourly bar after touch | Hourly | 2024-2026 |
| `TOUCH_30M` | hourly bar close after touch | Hourly | 2024-2026 |

Gap rule for `TOUCH` (long):

```
P_entry = open_t   if open_t ≤ L
        = L        otherwise
```

A bar opening below the band never traded at the band, so filling at `L` invents a better price than was available.

Slippage applies on top: `P_entry × (1 + bps/10⁴)` for a long.

### 5.5 Exit resolver

Given entry at `P₀` on bar t, long side, `ATR_14` at entry:

```
T = P₀ · (1 + target)
S = P₀ − k · ATR_14
```

For each forward bar `i ∈ {t+1 … t+5}`, in this order:

```
1. GAP CHECK
   if open_i ≤ S:  exit(open_i, STOP);   break
   if open_i ≥ T:  exit(open_i, TARGET); break

2. INTRADAY, priority order
   if low_i  ≤ S:  exit(S, STOP)
                   ambiguous = (high_i ≥ T);  break
   if high_i ≥ T:  exit(T, TARGET);          break
   if mid_band_enabled and high_i ≥ bb_mid_{i−1}:
                   exit(bb_mid_{i−1}, MID_BAND);   break
   if high_i ≥ bb_upper_{i−1}:
                   exit(bb_upper_{i−1}, UPPER_BAND); break

3. CLOSE-BASED
   if k_full_i ≥ 80:  exit(close_i, STOCH_80);  break

4. TERMINAL
   if i == t+5:       exit(close_i, TIMEOUT)
```

**Four details that decide correctness:**

1. **Gap checks run first.** An open past the level means the fill happened at the open, not at the level. Skipping this books a 3% loss on a stop that gapped 8% through — and mega-cap earnings gaps of that size are routine, with earnings inside a 5-day window roughly 8% of the time.
2. **Exit band levels come from bar i−1, not bar i.** Using i means using i's close to decide an intraday exit on bar i. Same trap as the entry side, same test.
3. **`ambiguous` is set only when stop and target both breach on the same bar.** Stop wins by rule (ADR 010). The flag makes the frequency measurable; above 10% triggers hourly escalation.
4. **The stochastic exit is close-based and evaluated last,** because a close-triggered exit cannot preempt an intraday fill that already happened.

**Long and short thresholds are independent fields, never derived from each other.** The long exit reads `ep.exit_stoch_threshold`; the short exit reads `ep.exit_stoch_threshold_short`. A `100 - threshold` derivation reaches only the diagonal of the sweep space (4 of 16 combinations on a 4x4 grid) and assumes exactly the symmetry ADR 016 was written to reject.

**No bare literals in the exit path.** The threshold comes from `ep.exit_stoch_threshold`, never from `80.0` inline. `SignalParams.stoch_overbought` is sweepable; a hardcoded exit threshold would let entry and exit disagree about what "overbought" means inside a single backtest, and every resulting number would look reasonable. The two parameters are independent by design — one is signal detection, the other exit policy — but they default to the same value.

Fill price summary:

| Condition | Normal fill | Gap fill |
|---|---|---|
| Stop, long | stop level | `open` if already past |
| Target, long | target level | `open` if already past |
| Mid / upper band | that band level from i−1 | `open` if already past |
| Stoch 80 | that bar's `close` | n/a |
| Timeout | final bar's `close` | n/a |

### 5.6 Path metrics

MFE and MAE are computed over `[t+1, exit_idx]`. Reachability is computed over the **full** `[t+1, t+5]` regardless of when the exit fired — because "would a limit at +5% have filled" is independent of where the actual exit landed.

```
MFE = max_i (high_i − P₀) / P₀
MAE = min_i (low_i  − P₀) / P₀

touched_2pct … touched_10pct         (bool, full window)
day_touched_2pct … day_touched_10pct (int 1-5, null if never)
```

Capture ratio per event: `η_i = R_exit,i / MFE_i`, stored null when MFE ≤ 0.

### 5.7 Event table

```sql
CREATE TABLE events (
  id bigserial PRIMARY KEY,
  run_id text NOT NULL REFERENCES runs(run_id),
  config_hash text NOT NULL,

  -- identity
  ticker text NOT NULL,
  signal_date date NOT NULL,
  signal_type text NOT NULL,
  signal_types_all text[],
  signal_strength int,
  side text NOT NULL CHECK (side IN ('long','short')),

  -- clustering (§5.3)
  cluster_id bigint,
  seq_in_cluster int,
  is_cluster_head boolean,
  days_since_head int,

  -- state at signal, all from t−1
  touch_level numeric(12,4),
  bb_pctb numeric(12,6),
  bb_width_pct numeric(12,6),
  k_full numeric(12,6), d_full numeric(12,6), k_fast numeric(12,6),
  k_cross_up boolean, k_cross_down boolean,
  atr_14 numeric(12,6),
  rv_pct_252d numeric(12,6),
  dd_52w numeric(12,6),
  sma200_slope_60 numeric(12,6),
  above_sma200 boolean,
  vol_z_20d numeric(12,6),
  days_to_earnings int,
  vix_close numeric(12,4),
  spx_ret_1d numeric(12,6),

  -- context tags
  dd_bucket text, bw_regime text, era text,
  cofire_count int, mcap_usd numeric, sector text,

  -- entry (one row per entry_kind)
  entry_kind text NOT NULL,
  entry_date date, entry_price numeric(12,4), entry_gapped boolean,

  -- exit
  exit_date date, exit_price numeric(12,4), exit_reason text,
  holding_days int, ambiguous boolean NOT NULL DEFAULT false,

  -- outcome
  gross_ret numeric(12,6), net_ret numeric(12,6),
  mfe numeric(12,6), mae numeric(12,6),
  time_to_mfe int, capture_ratio numeric(12,6),

  -- reachability, full 5-bar window
  touched_2pct boolean,  day_touched_2pct int,
  touched_3pct boolean,  day_touched_3pct int,
  touched_5pct boolean,  day_touched_5pct int,
  touched_10pct boolean, day_touched_10pct int,

  -- unconditional forward returns, for baseline comparison
  fwd_ret_1d numeric(12,6), fwd_ret_2d numeric(12,6),
  fwd_ret_3d numeric(12,6), fwd_ret_5d numeric(12,6),
  fwd_ret_10d numeric(12,6),

  -- flags
  earnings_in_window boolean,
  is_terminal boolean,
  split_key text NOT NULL CHECK (split_key IN ('train','validate','holdout')),

  UNIQUE (config_hash, ticker, signal_date, signal_type, entry_kind)
);
CREATE INDEX events_lookup ON events (signal_type, split_key, dd_bucket);
CREATE INDEX events_ticker_date ON events (ticker, signal_date);
CREATE INDEX events_cluster ON events (cluster_id, seq_in_cluster);
```

**Grain:** one row per `(config, ticker, signal_date, signal_type, entry_kind)`. A single touch under an 18-config sweep with 4 entry kinds produces up to 72 rows. At ~40k base events that is ~2.9M rows worst case, which is why this table lives in the research store only (ADR 053).

`config_hash` is a deterministic hash of the serialized config. It lets sweep results coexist in one table and makes "which config produced this" answerable.

`split_key` is assigned at creation by date, never computed at query time — leakage becomes a schema violation rather than a discipline problem (ADR 019).

### 5.7b Path table

Per session10.md §0 and §1 (forward path store), the `path` table holds per-day forward price paths for every event:

```sql
CREATE TABLE path (
  event_id bigint NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  day_offset int NOT NULL,
  favorable numeric(12,6) NOT NULL,
  adverse numeric(12,6) NOT NULL,
  terminal numeric(12,6) NOT NULL,
  PRIMARY KEY (event_id, day_offset)
);
CREATE INDEX path_event_id ON path (event_id);
```

**Grain:** one row per event per forward day. The window spans eleven trading days — the maximum `StatsParams.fwd_ret_horizons` (10) plus the largest entry-to-signal offset across `EntryKind` (`entry_offset_for`, 1 day for `NEXT_OPEN`), so that even a `NEXT_OPEN` entry (which fills one trading day after the signal) still has a full `fwd_ret_10d`-equivalent horizon available from its own entry date, not just from the signal date. `day_offset` counts from `signal_date`, not from the entry date — see `core.returns.entry_offset_for` for the translation any entry-anchored consumer (Session 9's own MFE/MAE, reachability, and forward-return windows) must apply. Events near the end of available price history may have partial windows.

**Fields:**
- `event_id`: foreign key to events(id), cascade delete ensures orphans never exist.
- `day_offset`: trading day offset from signal date (1-11 for complete windows, 1-N for partial). Null windows have no rows.
- `favorable`: the best (highest for long, lowest for short) intraday extreme over that day, expressed as return pct from entry price. Direction-neutral.
- `adverse`: the worst intraday extreme, also direction-neutral.
- `terminal`: the closing price on that day, expressed as return pct from entry price.

The path table replaces label materialization. Rather than adding columns for every (threshold, horizon) pair (which would explode to ~80+ columns as thresholds and horizons expand), labels are derived as queries against the path table. Session 9's existing `touched_*pct`, `day_touched_*pct`, and `fwd_ret_*d` columns on `events` remain as a precomputed cache for frequently accessed labels and for the serving views; new thresholds and new horizons are computed on demand.

**Events table extension:** the `fwd_window_days` column (nullable int, 1-11) tracks how many trading days of the forward window exist for each event. Null means no forward data; 11 means the full window is available; 1-10 means a partial window near the end of price history.

### 5.8 Parallelism and determinism

Per-ticker workers, no shared state. `cofire_count` requires a post-pass since it groups across tickers.

Determinism requirements (ADR 060):

- Sort tickers before dispatch; sort the collected frame by `(ticker, signal_date, entry_kind)` before writing. `as_completed` returns nondeterministically.
- No wall-clock reads inside the engine. `run_id` and timestamps are injected by the caller.
- Randomized benchmark arms seed from `config_hash`.
- Nothing reduces across workers, so float associativity is not a concern.

### 5.9 Sweep mechanics

```
stop_mode:   atr, fixed, none
stop_atr_k:  1.0, 1.5, 2.0, 2.5
target_pct:  0.03, 0.04, 0.05
entry_kind:  all 4, computed in one pass
```

`(4 + 1 + 1) × 3 = 18` exit configs.

Stochastic exit thresholds are **not** in this grid. They are configuration, held at defaults across the sweep. Adding them is a §5.9 change that multiplies the config count. **Entry prices compute once per event and are reused across exit configs,** since entry does not depend on exit parameters. That single optimization takes the sweep from 18 full passes to one candidate pass plus 18 exit passes: ~4 minutes total.

Ordering rule (ADR 059): the first backtest uses one default config (ATR stop k=1.5, target 4%, `NEXT_OPEN`), passes the full validation harness, and ~20 events get inspected by hand against charts **before** any sweep. Sweeping over a buggy engine produces 18 confidently wrong answers. The full 18-config sweep is mandatory before any result is published.

### 5.10 Validation harness

| Check | Method | Passes when |
|---|---|---|
| No look-ahead | Shift all indicators forward one bar, rerun | Event set changes materially |
| Entry sanity | Every `entry_price` within its bar's `[low, high]` | 100% |
| Exit sanity | Every `exit_price` within its bar's `[low, high]` | 100% |
| Return identity | `gross_ret` recomputed from prices | Matches to 1e-9 |
| Non-overlap | No two cluster-head events with overlapping windows | Zero violations |

The shift test is the important one. If shifting indicators forward a day barely changes the event set, either the signal is not reading the indicators or it is already using future data.

---

## 6. Statistics layer

### 6.1 Job

Reads `events`, writes `cell_stats` and `benchmarks`. Never touches bars. ~30 s over 40k cluster-head events.

**Contract:** every number reported to a user carries `n_eff`, a confidence interval, and the baseline it is measured against. Nothing renders as a bare percentage.

### 6.2 Baselines

Two, both stored (ADR 062).

**Empirical.** For ticker *i* in year *y*: every trading day, 5-day forward return, fraction hitting the target. ~250 overlapping observations per ticker-year.

**Parametric.** From trailing 252-day drift and volatility:

```
P_base = 1 − Φ( (X − μ_5d) / σ_5d )
μ_5d = μ_ann / 50.4
σ_5d = σ_ann · √(5/252)
```

Edge is reported against the **empirical** baseline; parametric is a diagnostic, and large disagreement flags a non-normal return distribution for that ticker-year.

A cell's baseline is the **event-weighted mean** of its constituent events' per-ticker-year baselines, never a pooled rate over all days.

Why per ticker-year matters: selecting on growth hands you free baseline points. At 30% annualized drift and 40% vol, `μ_5d ≈ 0.60%` and `σ_5d ≈ 5.6%`, giving `P(R_5d ≥ 2%) ≈ 40.1%` versus 36.1% at zero drift. Four points arrive before any indicator fires.

### 6.3 Effective sample size

```
n_eff = n / (1 + (k̄ − 1)·ρ̄)
```

`k̄` is the mean `cofire_count` across the cell's events. `ρ̄` is the mean pairwise correlation of 5-day returns among co-firing tickers, computed once per era and stored as a per-era constant.

All standard errors use `n_eff`, and both are displayed:

```
SE = √( p(1−p) / n_eff )
```

Confidence intervals use **Wilson**, not the normal approximation, since p near 0 or 1 with modest n breaks the normal interval.

Power reference at p = 0.4, 80% power, α = 0.05:

| Edge | Events needed |
|---|---|
| 10 pts | ~190 |
| 5 pts | ~750 |
| 3 pts | ~2,100 |

### 6.4 Three benchmark arms

Identical universe and dates (ADR 012).

1. **Buy and hold.** Equal-weight the trade universe, rebalance quarterly on universe changes, hold throughout. The number to beat.
2. **Random entry.** Sample entry dates matching the signal's empirical firing rate per ticker-year, identical exit rules, seeded from `config_hash`. **200 replications**, reported as a distribution. Signal performance above the 97.5th percentile of the null is the significance criterion (ADR 061).
3. **Signal entry.** The actual strategy.

Replication counts: 200 on default and validation configs, 50 during sweeps where ranking rather than significance is the goal, escalating to 200 on any anomaly.

The randomization null beats a parametric test here because it absorbs clustering, drift, and exit-rule path dependence automatically.

Reported per arm: total return, annualized return, Sharpe, max drawdown, fraction of time deployed, capital efficiency, win rate.

```
Capital efficiency = total return / fraction of time deployed
```

Arm 1 is hard to beat by construction: a signal firing on 4% of days with a 5-day hold sits in the market ~20% of the time, needing roughly 5× the annualized rate while deployed to match a name compounding at 30%.

### 6.5 Trim-and-redeploy variant

ADR 017 makes trimming, not shorting, the primary use of the upper-band signal. It is measured as a strategy variant against buy-and-hold on the same names.

| Rule | Action |
|---|---|
| Baseline | Buy and hold the trade universe |
| Trim | Sell 20% of the position on `CONFLUENCE_HIGH` or `%K_full >= 80` |
| Redeploy | Buy back on the next `CONFLUENCE_LOW` in the same name |
| Cash idle | Held at the risk-free rate between trim and redeploy |

Reported: terminal value, Sharpe, max drawdown, number of round trips, average days in cash, and terminal value versus never trimming.

Carries no borrow cost, no gap risk, no wash-sale interaction, and no undefined loss, which is why it is expected to beat the regime-filtered short on risk-adjusted terms (ADR 016, 017).

**Expected ranking:** trim-and-redeploy > regime-filtered short > naive always-short, with the last likely showing negative edge from band walking.

### 6.6 DCA comparison

Separate from the arms, and closer to actual use.

| Variant | Rule |
|---|---|
| Fixed schedule | Buy C/12 on the first trading day of each month |
| Signal-triggered | Hold cash, deploy C/N on each signal |
| Hybrid | Monthly base, double the tranche on a signal day |
| Lump sum | Deploy C on day one |

Same total capital, same window, no exits, positions accumulate and hold. Reported: terminal value, IRR, average cost basis, cash drag. `N` comes from the historical firing rate; leftover capital deploys on the final day, and underfiring is reported explicitly since idle capital has a real cost.

### 6.7 Headline grid

Twelve cells maximum (ADR 011). Fixed composition:

| Dimension | Levels |
|---|---|
| Signal | `CONFLUENCE_LOW`, `BB_LOWER_TOUCH` |
| Drawdown bucket | 0-10%, 10-20%, 20-35%, >35% |

8 cells for the long side, plus 4 from a secondary cut on `signal_strength` (1 vs 2) within `CONFLUENCE_LOW`.

Everything else stays continuous and feeds the model. Bandwidth regime, trend, VIX percentile, and days-to-earnings are features, not cells.

Why not the full grid: `3 × 3 × 2 × 4 × 2 = 144` cells against ~2,000 effective events gives an average of 14 per cell, and clustering means ~20 cells hold 90% of events while the rest hold 0-3. A hit rate on 3 events is noise wearing a percentage sign.

**Suppression:** `n_eff < 30` returns `suppressed` with a reason. No number renders.

### 6.8 Multiple testing

Benjamini-Hochberg across all tested cells within a run (ADR 020). Both `p_value` and `q_value` stored.

Procedure: sort *m* p-values ascending, find the largest *k* with `p₍ₖ₎ ≤ (k/m)·α`, reject 1..k. The q-value is `min_{j≥i}( (m/j)·p₍ⱼ₎ )`, read as the FDR accepted by calling that result significant.

The test family is defined explicitly: all headline-grid cells across all ladder targets, for one config. **The sweep does not multiply the family**, because sweeping is parameter selection on the training split, not hypothesis testing. Validation gets its own family; holdout gets a single test.

### 6.9 Output contract

```sql
CREATE TABLE cell_stats (
  cell_id text NOT NULL,
  run_id text NOT NULL, config_hash text NOT NULL,
  PRIMARY KEY (cell_id, config_hash),

  signal_type text, dd_bucket text, signal_strength int,
  side text, entry_kind text, split_key text, era text,
  horizon_days int, target_pct numeric,

  n_events int, n_eff int, n_tickers int, mean_cofire numeric,

  p_hit numeric,
  baseline_empirical numeric, baseline_parametric numeric,
  edge numeric, ci_low numeric, ci_high numeric,
  p_value_randomization numeric, p_value_parametric numeric, q_value numeric,

  mean_ret numeric, median_ret numeric, ret_p25 numeric, ret_p75 numeric,
  mean_mfe numeric, mean_mae numeric, median_time_to_mfe numeric,
  capture_ratio numeric,

  p_touch_2pct numeric, p_touch_3pct numeric,
  p_touch_5pct numeric, p_touch_10pct numeric,
  median_day_touch_5pct numeric,

  exit_mix jsonb,
  earnings_frac numeric,
  suppressed boolean, suppress_reason text,

  computed_at timestamptz, git_sha text
);
```

The primary key is `(cell_id, config_hash)`, amended 2026-08-06 by ADR 096.
It was `cell_id` alone, which held one statistics snapshot at a time and made
each Phase 4 run overwrite the previous. Session 9's sweep wrote 18 distinct
`config_hash` values into `events`, so the composite key is what lets those 18
be compared without re-running Phase 4 once per config. `cell_key()` takes
`config_hash` as an input; `cell_id` itself is unchanged and still derived from
its component columns only.

`exit_mix` (fraction by exit reason) is the diagnostic that separates "a few large winners carrying many small losses" from "consistently positive." A cell where 70% of exits are stops tells a different story from one where 70% are targets, even at identical mean return.

### 6.10 Benchmarks table

```sql
CREATE TABLE benchmarks (
  id bigserial PRIMARY KEY,
  run_id text NOT NULL, config_hash text NOT NULL,
  arm text NOT NULL,          -- buy_hold | random | signal | trim
                              -- | dca_fixed | dca_signal | dca_hybrid | dca_lump
  replication int,            -- null except for the random-entry arm (1..200)
  split_key text, era text,

  total_ret numeric, annualized_ret numeric,
  sharpe numeric, max_drawdown numeric,
  frac_deployed numeric, capital_efficiency numeric,
  win_rate numeric, n_trades int,

  -- DCA-specific (§6.6)
  terminal_value numeric, irr numeric,
  avg_cost_basis numeric, cash_drag numeric,
  capital_undeployed numeric,

  -- trim-specific (§6.5)
  n_round_trips int, avg_days_in_cash numeric,

  -- ADR 032
  pre_tax_ret numeric, post_tax_ret numeric, wash_sale_flagged boolean,

  computed_at timestamptz, git_sha text
);
CREATE INDEX benchmarks_lookup ON benchmarks (run_id, arm, split_key);
```

The `replication` column is what makes the 200-replication randomization null a queryable distribution rather than a summary statistic. The significance test in §6.4 reads it directly.

### 6.11 Era and per-ticker reporting

Every headline cell is also reported by era: 2010-14, 2015-19, 2020-23, 2024-26 (ADR 042).

**Stability criterion:** an edge appearing in one era and absent in three is a regime artifact regardless of pooled significance. Report era-level edges side by side; no formal test, since four eras with modest n will not reach individual significance.

Per-ticker contribution is capped in pooled reporting: if one ticker supplies more than 15% of a cell's events, report the cell with and without it. NVDA over 2020-2024 is the obvious risk.

### 6.12 Reachability targets

Reported at both fixed percentages (2, 3, 5, 10%) and volatility-scaled levels (0.5σ, 1.0σ, 1.5σ of `σ_5d`).

Fixed is the headline because it matches how a limit order is placed. Scaled is the diagnostic revealing whether an apparent edge is really a volatility effect.

### 6.13 Statistical self-validation

Before any real result is trusted (ADR 087):

- **Null test.** Driftless GBM, 50 synthetic tickers, 2,500 days. The fraction of cells at `q < 0.05` must not exceed 5%. If a random walk produces significant cells, the layer has a bug and every real result is suspect.
- **Recovery test.** Known drift in, computed parametric baseline must match the analytical value within 1 percentage point.

---

## 7. Model

### 7.1 Scope

Answers one question per live event: given this state, what does the 5-day path distribution look like?

```
Terminal:     Q̂_τ(R_5),  τ ∈ {0.05, 0.25, 0.50, 0.75, 0.95}
Reachability: P̂( max_{t≤5} R_t ≥ X ),  X ∈ {2,3,5,10}%
Adverse:      P̂( min_{t≤5} R_t ≤ −Y ), Y ∈ {3,5}%
```

**Eleven independent heads** on a shared feature set (ADR 064). Not a mixture of experts: no gate, no routing. Eleven different questions from identical features. Separation is required because quantile regression and binary classification need different loss functions, and the binary targets are genuinely distinct events rather than one event at shifted thresholds.

The adverse heads are the stop-loss warning: how likely a stop triggers before the target.

Not in scope: position sizing, portfolio allocation, instrument selection, or any expected-value ranking implying a recommendation. The model emits probabilities; the UI displays them next to the empirical cell rate.

### 7.2 Relationship to the statistics layer

```
engine  →  events      (what happened, ~40k resolved cases)
stats   →  cell_stats  (frequencies conditional on cell membership)
model   →  predictions (per-event resolution on unresolved cases)
```

Without the model, a live signal is answered by lookup, and that is a complete shippable answer. The model's addition is **resolution**: the lookup treats every event in a cell as identical, while the model reads continuous features (%B at −0.4 vs −0.1, VIX at 14 vs 32, earnings in 2 days vs 40) and separates cases the cell pools. It also produces the terminal quantile fan, which no lookup can.

**Phase 4 gates Phase 6.** If the lookup shows no edge, the model has nothing to find.

### 7.3 Features

Twenty-two, all available at t−1, all already on the event row.

| Group | Features |
|---|---|
| Band state | `bb_pctb`, `bb_width_pct`, distance to mid in ATR units |
| Momentum | `k_full`, `d_full`, `k_fast`, `k_minus_d`, `k_cross_up`, `k_cross_down` |
| Volatility | `atr_14/close`, `rv_pct_252d`, `vix_close`, `vix_pct_252d` |
| Trend | `above_sma200`, `sma200_slope_60`, `dd_52w` |
| Flow | `vol_z_20d`, `spx_ret_1d`, `cofire_count` |
| Event | `signal_strength`, `seq_in_cluster`, `days_to_earnings` |
| Static | `sector` (categorical), `mcap_log` |

**Deliberately excluded:**

- Raw price and raw band levels — non-stationary across tickers and eras; everything enters as a ratio or percentile.
- `era` — invites memorizing regime, and is unavailable for a future prediction anyway.
- `ticker` identity — 60 names over 40k events permits memorizing individual histories. Sector is the right granularity.
- Anything at t or later — enforced by construction, since features come from the event row.

**Phase 6 addition (ADR 069):** live breach depth, `(lower_{t−1} − P_now) / ATR_14`. A touch dipping 0.1 ATR below the band is a graze; one punching 1.5 ATR through is a different event. Added *after* the base model exists so its contribution is measurable. In backtest, daily bars approximate it from the bar low; hourly bars from 2024 give it exactly, so it enters with a documented approximation before 2024.

### 7.4 Targets

| Head | Target | Objective |
|---|---|---|
| Terminal quantiles (5) | `R_5` timeout return | Quantile, one per τ |
| Reachability (4) | `touched_Xpct` | Binary logloss |
| Adverse (2) | `touched_−Ypct` | Binary logloss |

**Timeout return, not exit-rule return.** The model describes the underlying path; exit policy is a separate layer on top. Training on exit returns would couple the model to config, forcing a retrain on every stop or target change.

Quantile crossing is fixed post-fit by sorting.

### 7.5 Training protocol

Splits per ADR 019, assigned at event creation.

**Purged walk-forward CV inside train** (ADR 065), never random K-fold — an event on 2018-03-05 and one on 2018-03-07 share overlapping forward windows, so random splits put correlated observations on both sides.

```
fold 1: train 2010-2014 → validate 2015
fold 2: train 2010-2015 → validate 2016
...
fold 7: train 2010-2020 → validate 2021
```

*Purge:* drop training events whose forward window overlaps the fold start. *Embargo:* drop validation events in the fold's first 5 trading days. Together ~10 events per fold.

*Sample weighting:* cluster members weighted `1/|cluster|`. Co-firing is **not** weighted here — the randomization null handles it at the reporting layer, and double-correcting understates confidence.

Hyperparameters, conservative given an effective sample near 8,000:

```python
num_leaves=15, max_depth=4, min_child_samples=100,
learning_rate=0.03, n_estimators=<early stopped>,
feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1,
lambda_l2=5.0
```

Early stopping on fold pinball/logloss, patience 50.

Monotonic constraints only where the sign is known a priori: deeper drawdown must not increase upside reachability. `days_to_earnings` stays unconstrained.

### 7.6 Calibration

The product *is* the probability, so calibration is the metric.

Isotonic regression fit on the **validation** split, never train, applied to every binary head. Quantile heads are checked by coverage rather than recalibrated: the fraction of realized returns below `Q̂_0.25` should be 25%.

| Metric | Purpose |
|---|---|
| Brier score | Overall probability accuracy |
| Reliability diagram, 10 bins | Where it is over/under confident |
| Expected calibration error | Single-number summary |
| Coverage per τ | Quantile head correctness |
| Pinball loss | Quantile head sharpness |

**Against a baseline of the cell rate.** If the model does not beat "just report the cell frequency" on Brier, the lookup ships alone (ADR 067).

### 7.7 Promotion gate

All four checks, on validation, against the incumbent (ADR 067):

1. ECE strictly decreased
2. Brier did not increase by more than 0.002
3. Quantile coverage within 5 points of nominal for every τ
4. No feature's relative importance shifted by more than 40%

Check 2 prevents the degenerate win where a model gains calibration by flattening toward the base rate and loses all discrimination. Check 4 catches feature-pipeline changes, which present as model changes and are otherwise invisible.

Failure leaves the incumbent serving; the candidate is logged with metrics. Success increments `model_version`.

### 7.8 Forward log

```sql
CREATE TABLE predictions (
  id bigserial PRIMARY KEY,
  ticker text, as_of date, model_version text, event_id bigint,
  q05 numeric, q25 numeric, q50 numeric, q75 numeric, q95 numeric,
  p_touch_2 numeric, p_touch_3 numeric, p_touch_5 numeric, p_touch_10 numeric,
  p_adverse_3 numeric, p_adverse_5 numeric,
  cell_id text, cell_p_hit numeric, cell_n_eff int,
  features_json jsonb NOT NULL,
  git_sha text, created_at timestamptz
);

CREATE TABLE outcomes (
  prediction_id bigint PRIMARY KEY REFERENCES predictions(id),
  realized_ret_5d numeric, realized_mfe numeric, realized_mae numeric,
  touched_2 boolean, touched_3 boolean, touched_5 boolean, touched_10 boolean,
  pinball_loss numeric, brier_3pct numeric,
  resolved_at timestamptz
);
```

Storing `cell_p_hit` alongside the model prediction means every forward observation compares **three** things: model, lookup, and reality. That answers "does the model beat the simple version out of sample" continuously.

`features_json` is non-negotiable. Recomputing features from history six weeks later silently picks up revised data.

Predictions are **immutable** (ADR 029). Resolution runs nightly at T+6. A rolling 90-day reliability diagram is the decay detector: it degrades before performance does.

### 7.9 Serving

Two paths (ADR 069):

| Path | When | Features | Purpose |
|---|---|---|---|
| Nightly batch | After close | t−1 only | Screener view for all trade-universe tickers |
| Live inference | At trigger, in the poller | t−1 + live breach depth | The actual recommendation |

The poller loads all 11 artifacts at 09:15. Inference on one row across 11 models is well under a millisecond on CPU, so it fits inside the polling tick.

**Vercel never runs a model.** Artifacts live in `models/{version}/` in LightGBM native format with isotonic calibrators pickled alongside, plus `metadata.json` (training window, feature list, hyperparameters, calibration metrics) and `features.parquet` (the training snapshot needed for gate check 4).

### 7.10 Failure modes

| Mode | Detection | Response |
|---|---|---|
| Feature drift | PSI per feature vs training distribution, nightly | Alert above 0.25, retrain |
| Label leakage | Shift test on features | Fails CI |
| Overfit to a regime | Per-era validation metrics | Reject promotion if one era dominates |
| Degenerate predictions | Variance of predicted probabilities | Alert if near-constant |
| Silent cell mismatch | Assert every prediction's `cell_id` exists in `cell_stats` | Fail the batch |

---

## 8. Serving API

### 8.1 Principles

1. Every endpoint is an indexed SELECT from a **Postgres view**. No computation, no model calls.
2. Every response carrying a probability structurally carries `n_eff`, CI, and baseline — they are columns in the view, so returning a bare probability requires deliberately dropping columns.
3. Next.js queries Neon directly through the Postgres driver. **No Python on Vercel** (ADR 070).

### 8.2 Views as the shared contract

Query logic lives in Postgres views (ADR 076). TypeScript API routes select from them. Python MCP handlers select from the same ones. Neither language contains query logic, so a shape change happens in exactly one place.

```sql
-- Canonical cell identifier. The stats job and every view use this one function.
CREATE FUNCTION cell_key(
  p_signal_type text, p_side text, p_dd_bucket text,
  p_strength int, p_entry_kind text, p_split text,
  p_era text, p_horizon int, p_target numeric
) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT concat_ws('|',
    p_signal_type, p_side,
    coalesce(p_dd_bucket, 'all'),
    coalesce(p_strength::text, 'all'),
    p_entry_kind, p_split,
    coalesce(p_era, 'pooled'),
    'h' || p_horizon,
    't' || to_char(p_target, 'FM990.999')
  );
$$;

-- 1. Screener. Joins on components with display parameters pinned.
CREATE VIEW v_screen AS
SELECT e.ticker, e.signal_date, e.signal_type, e.signal_types_all,
       e.signal_strength, e.touch_level, e.bb_pctb, e.k_full, e.k_fast,
       e.k_cross_up, e.dd_52w, e.dd_bucket, e.above_sma200,
       e.seq_in_cluster, e.cofire_count, e.sector,
       c.cell_id,
       CASE WHEN c.suppressed THEN NULL ELSE c.p_hit END              AS p_hit,
       CASE WHEN c.suppressed THEN NULL ELSE c.baseline_empirical END AS baseline,
       CASE WHEN c.suppressed THEN NULL ELSE c.edge END               AS edge,
       CASE WHEN c.suppressed THEN NULL ELSE c.ci_low END             AS ci_low,
       CASE WHEN c.suppressed THEN NULL ELSE c.ci_high END            AS ci_high,
       c.n_events, c.n_eff, c.q_value, c.suppressed, c.suppress_reason,
       p.q50, p.p_touch_3, p.p_touch_5, p.p_adverse_3, p.model_version
FROM events e
LEFT JOIN cell_stats c
       ON c.signal_type     = e.signal_type
      AND c.side            = e.side
      AND c.dd_bucket       = e.dd_bucket
      AND c.signal_strength = e.signal_strength
      AND c.entry_kind      = e.entry_kind
      AND c.split_key       = 'validate'      -- NEVER e.split_key
      AND c.era             IS NULL           -- pooled row
      AND c.horizon_days    = 5
      AND c.target_pct      = 0.03
LEFT JOIN predictions p
       ON p.ticker = e.ticker AND p.as_of = e.signal_date
WHERE e.is_cluster_head
  AND e.entry_kind = 'next_open';
```

**`cell_id` is derived, never stored on `events`.** Two reasons, both load-bearing.

*One event belongs to many cells.* `cell_stats` carries `horizon_days` and `target_pct`, which are report parameters rather than event properties and are absent from `events`. A single `cell_id` column on `events` would store one arbitrary member of a set.

*Joining on the event's own `split_key` would leak holdout.* A live event fired today carries `split_key = 'holdout'`. Inheriting it into the join would surface holdout statistics in the screener every day, silently, destroying the guarantee in ADR 019 and 033 that holdout is evaluated exactly once. The hardcoded `split_key = 'validate'` is the firewall.

**Requirement this places on the stats job:** write a pooled row per cell with `era IS NULL`, alongside the four per-era rows. `v_screen` reads the pooled row; `/research` reads the per-era ones.



```sql
-- 2. Current state rail for /ticker/[sym]. One row per ticker.
CREATE VIEW v_ticker_state AS
SELECT DISTINCT ON (i.ticker)
       i.ticker, t.name, t.sector, i.ts AS as_of,
       b.close, b.volume,
       i.bb_lower, i.bb_mid, i.bb_upper, i.bb_pctb,
       i.bb_width, i.bb_width_pct,
       i.k_full, i.d_full, i.k_fast, i.k_cross_up, i.k_cross_down,
       i.sma_200, i.sma200_slope_60, i.atr_14,
       i.rv_20d, i.rv_pct_252d, i.vol_z_20d,
       i.dd_52w, i.days_to_earnings,
       m.vix_close, m.vix_pct_252d, m.spx_ret_1d,
       u.in_trade, u.mcap_usd,
       u.crit_mcap, u.crit_above_sma200, u.crit_sma200_slope,
       u.crit_rel_return, u.crit_rev_growth,
       (b.close > i.sma_200) AS above_sma200
FROM indicators i
JOIN bars b    ON b.ticker = i.ticker AND b.ts = i.ts AND b.interval = i.interval
JOIN tickers t ON t.ticker = i.ticker
LEFT JOIN market_days m ON m.ts = i.ts::date
LEFT JOIN LATERAL (
    SELECT * FROM universe u2
    WHERE u2.ticker = i.ticker AND u2.as_of <= i.ts::date
    ORDER BY u2.as_of DESC LIMIT 1
) u ON true
WHERE i.interval = '1d'
ORDER BY i.ticker, i.ts DESC;

-- 3. Chart series: bars + indicators + event markers, one row per bar.
CREATE VIEW v_chart AS
SELECT b.ticker, b.ts, b.open, b.high, b.low, b.close, b.volume,
       i.bb_lower, i.bb_mid, i.bb_upper,
       i.k_full, i.d_full, i.k_fast,
       i.sma_200, i.bb_width_pct, i.dd_52w,
       e.id AS event_id, e.signal_type, e.signal_strength,
       e.exit_date, e.exit_reason, e.net_ret
FROM bars b
LEFT JOIN indicators i
       ON i.ticker = b.ticker AND i.ts = b.ts AND i.interval = b.interval
LEFT JOIN events e
       ON e.ticker = b.ticker AND e.signal_date = b.ts::date
      AND e.is_cluster_head AND e.entry_kind = 'next_open'
WHERE b.interval = '1d';

-- 4. Cell statistics with suppression applied IN SQL.
CREATE VIEW v_stats AS
SELECT cell_id, run_id, config_hash,
       signal_type, dd_bucket, signal_strength, side,
       entry_kind, split_key, era, horizon_days, target_pct,
       n_events, n_eff, n_tickers, mean_cofire,
       CASE WHEN suppressed THEN NULL ELSE p_hit END              AS p_hit,
       CASE WHEN suppressed THEN NULL ELSE baseline_empirical END AS baseline,
       CASE WHEN suppressed THEN NULL ELSE edge END               AS edge,
       CASE WHEN suppressed THEN NULL ELSE ci_low END             AS ci_low,
       CASE WHEN suppressed THEN NULL ELSE ci_high END            AS ci_high,
       q_value, p_value_randomization,
       mean_ret, median_ret, ret_p25, ret_p75,
       mean_mfe, mean_mae, median_time_to_mfe, capture_ratio,
       p_touch_2pct, p_touch_3pct, p_touch_5pct, p_touch_10pct,
       median_day_touch_5pct,
       exit_mix, earnings_frac,
       suppressed, suppress_reason
FROM cell_stats;

-- 5. Event history with outcomes. Default config only.
CREATE VIEW v_events AS
SELECT e.id, e.ticker, t.sector, e.signal_date, e.signal_type,
       e.signal_types_all, e.signal_strength,
       e.cluster_id, e.seq_in_cluster, e.is_cluster_head,
       e.bb_pctb, e.k_full, e.k_fast, e.dd_52w, e.dd_bucket,
       e.above_sma200, e.vix_close, e.days_to_earnings,
       e.entry_kind, e.entry_date, e.entry_price, e.entry_gapped,
       e.exit_date, e.exit_price, e.exit_reason, e.holding_days,
       e.ambiguous, e.gross_ret, e.net_ret,
       e.mfe, e.mae, e.time_to_mfe, e.capture_ratio,
       e.touched_2pct, e.touched_3pct, e.touched_5pct, e.touched_10pct,
       e.day_touched_5pct, e.earnings_in_window, e.era, e.split_key
FROM events e
JOIN tickers t ON t.ticker = e.ticker
WHERE e.config_hash = current_setting('capitalscan.default_config_hash', true);

-- 6. Forward log: model vs lookup vs reality.
CREATE VIEW v_forward AS
SELECT p.id, p.ticker, p.as_of, p.model_version, p.event_id,
       p.q05, p.q25, p.q50, p.q75, p.q95,
       p.p_touch_2, p.p_touch_3, p.p_touch_5, p.p_touch_10,
       p.p_adverse_3, p.p_adverse_5,
       p.cell_id, p.cell_p_hit, p.cell_n_eff,
       o.realized_ret_5d, o.realized_mfe, o.realized_mae,
       o.touched_2, o.touched_3, o.touched_5, o.touched_10,
       o.pinball_loss, o.brier_3pct, o.resolved_at,
       (o.prediction_id IS NOT NULL) AS resolved,
       CASE WHEN o.prediction_id IS NULL THEN NULL
            ELSE abs(p.p_touch_3 - o.touched_3::int::numeric) END AS abs_err_3pct
FROM predictions p
LEFT JOIN outcomes o ON o.prediction_id = p.id;

-- 7. Universe membership with per-criterion pass/fail.
CREATE VIEW v_universe AS
SELECT DISTINCT ON (u.ticker)
       u.ticker, t.name, t.sector, t.industry,
       u.as_of, u.in_train, u.in_trade,
       u.mcap_usd, u.mcap_rank, u.adv_20d_usd,
       u.crit_mcap, u.crit_above_sma200, u.crit_sma200_slope,
       u.crit_rel_return, u.crit_rev_growth,
       t.is_active, t.delisted_on
FROM universe u
JOIN tickers t ON t.ticker = u.ticker
ORDER BY u.ticker, u.as_of DESC;

-- 8. Positions with live exit signals computed, not stored.
CREATE VIEW v_positions AS
SELECT p.*,
       s.close AS current_price,
       CASE WHEN p.status = 'open'
            THEN (s.close - p.entry_price) / p.entry_price
            ELSE p.realized_ret END              AS unrealized_or_realized_ret,
       (CURRENT_DATE - p.entry_date)             AS days_held,
       (s.k_full >= 80)                          AS exit_signal_stoch,
       (s.close >= s.bb_upper)                   AS exit_signal_upper_band,
       (s.close >= s.bb_mid)                     AS exit_signal_mid_band,
       ((CURRENT_DATE - p.entry_date) >= 5)      AS exit_signal_timeout
FROM positions p
LEFT JOIN v_ticker_state s ON s.ticker = p.ticker;
```

**Three design decisions embedded above.**

*Suppression is enforced in SQL, not in application code.* `v_stats` returns `NULL` for every rate on a suppressed cell. No code path in TypeScript or Python can render a number for a thin cell, because the number never leaves the database. This makes ADR 026 structural rather than conventional.

*`v_events` filters to the default config via a session setting.* `current_setting('capitalscan.default_config_hash', true)` reads a Postgres parameter set at connection time. This keeps sweep rows off the serving surface without hardcoding a hash into the view. Set it in the sync job and in the serving connection string.

*`v_positions` computes exit signals live rather than storing them.* A stored exit flag goes stale between nightly runs. Computing from `v_ticker_state` keeps the position page current relative to the last closed session.

**Eight views, not seven.** The ticker page needs `v_ticker_state` (one row, the state rail) and `v_chart` (one row per bar) as separate views. Merging them would force the chart query to re-read every state column on every bar.

### 8.3 Endpoints

```
GET  /api/screen        ?date&universe&signal_types&dd_bucket&limit
GET  /api/ticker/:sym   ?start&end
GET  /api/stats         ?signal_type&dd_bucket&target_pct&entry_kind&split
GET  /api/predict/:sym  ?as_of
GET  /api/events        ?ticker&start&end&cluster_head_only&limit
GET  /api/forward       ?window
GET  /api/universe      ?as_of
GET  /api/health
GET  /api/positions
POST /api/positions
```

`ScreenRow`, consumed by both the UI and the chat tool:

```ts
{
  ticker, sector, signal_type, signal_types_all, signal_strength,
  touch_level, close, bb_pctb, k_full, k_fast, k_cross_up,
  dd_52w, dd_bucket, above_sma200, vix_close,
  seq_in_cluster, cofire_count,
  cell: { cell_id, p_hit, baseline, edge, n_events, n_eff,
          ci_low, ci_high, suppressed },
  prediction: { q50, p_touch_3, p_touch_5, p_adverse_3, model_version } | null
}
```

Cell and prediction are **nested objects**, not flattened columns, so absence is structurally obvious.

### 8.4 Caching

| Endpoint | Strategy | TTL |
|---|---|---|
| `/api/screen` | ISR | 300 s market hours, 86400 s after close |
| `/api/ticker/:sym` | ISR per symbol | 3600 s, current day invalidated |
| `/api/stats` | Static, tagged | Until `cell_stats.run_id` changes |
| `/api/predict/:sym` | ISR | Until next batch |
| `/api/events` | ISR | 3600 s |
| `/api/forward` | ISR | 86400 s |
| `/api/universe` | Static | Until quarterly job |
| `/api/health`, `/api/positions` | No cache | — |

Cache tags come from `run_id` rather than time, since closed-session data never changes. Nightly jobs call `revalidateTag()` after writing. Indicator state (all band levels, %K, %D) caches per ticker-day with effectively infinite TTL.

### 8.5 Positions

```sql
CREATE TABLE positions (
  id bigserial PRIMARY KEY,
  user_id text,                     -- reserved, unused (ADR 071)
  ticker text NOT NULL,
  side text NOT NULL,
  entry_date date NOT NULL,
  entry_price numeric(12,4) NOT NULL,
  quantity numeric,
  source text NOT NULL DEFAULT 'user_declared',   -- ADR 048
  status text NOT NULL DEFAULT 'open',
  exit_date date, exit_price numeric(12,4), exit_reason text,
  realized_ret numeric(12,6),
  idempotency_key text UNIQUE,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE order_intents (
  id bigserial PRIMARY KEY,
  event_id bigint, ticker text, side text,
  quantity_basis text, limit_level numeric(12,4),
  stop_level numeric(12,4), time_in_force text,
  idempotency_key text UNIQUE NOT NULL,
  emitted_at timestamptz DEFAULT now(),
  run_id text, git_sha text
);
```

`order_intents` renders as a notification today and serializes to a broker adapter later (ADR 048). **No broker client, no credentials, no code path capable of placing an order exists.** The absence is the safety property.

Closed positions with realized returns form a personal trade log alongside the model's forward log (ADR 073).

### 8.6 Error contract

```ts
{ error: { code, message, detail? } }
```

| Code | HTTP | When |
|---|---|---|
| `INVALID_PARAM` | 400 | Enum miss, malformed date, out-of-range limit |
| `OUT_OF_RANGE` | 400 | Date outside the ingested window |
| `NOT_FOUND` | 404 | Unknown ticker, no prediction for that date |
| `SUPPRESSED` | 200 | Cell below the n_eff threshold, with reason |
| `STALE_DATA` | 200 | Served, with a `staleness_days` warning in meta |
| `UPSTREAM` | 503 | Database unreachable |

`SUPPRESSED` returns 200 deliberately: insufficient data is a valid answer, not a failure. The UI renders it as a message; the chat tool passes it through so the model reports insufficient data rather than inventing a number.

Every response carries `meta.as_of` and `meta.staleness_days`. The UI banners above 2 days, so a failed nightly job cannot silently serve stale data as current.

### 8.7 Research-to-serving sync

```bash
cscan sync --to-serving
```

Nightly, after indicators and events.

| Table | Filter | Rows |
|---|---|---|
| `tickers` | all | 750 |
| `universe` | last 8 quarters | 6k |
| `indicators` | trade universe, last 3 years | 45k |
| `events` | cluster heads or last 90 days, **default config only** | 8k |
| `cell_stats` | current `run_id` | 400 |
| `predictions` | last 90 days | 5k |
| `outcomes` | last 90 days | 5k |

~70k rows, under 60 MB, ~20 s. Sweep configs stay local. Transactional per table: a failed sync leaves previous serving data intact rather than half-updating.

### 8.8 Tax reporting

Per ADR 032, backtest output reports pre-tax and after-tax results separately. Sleeve gains are taxed at short-term ordinary rates. The wash-sale interaction is flagged: the core-plus-sleeve structure trades tickers held long term, so selling a sleeve position at a loss triggers disallowance if the ticker was bought within 30 days either side, including the core position and dividend reinvestment.

This is a modeling assumption for evaluation, not tax advice. Rates and rules vary by situation.

---

## 9. Deployment

### 9.1 Environments

| Env | Database | Runs where | Purpose |
|---|---|---|---|
| `local` | Postgres in Docker | Workstation | All research, backtests, training, poller |
| `preview` | Neon branch | Vercel preview | PR builds against a database branch |
| `prod` | Neon main | Vercel production | The served app |

No staging (ADR 079). One user plus branch-per-PR databases makes it ceremony that catches nothing.

### 9.2 Secrets

Vercel receives only `DATABASE_URL_SERVING` and `ANTHROPIC_API_KEY`. Everything else stays local.

`SEC_USER_AGENT` must contain a real email. SEC blocks requests without it at the IP level, persistently.

Config precedence, resolved by one tested function: CLI flag > env var > `config.toml` > dataclass default.

### 9.3 Layout

All Python runs natively on Windows. The only Linux locally is inside the Postgres container (ADR 081).

```
C:/Users/daris/Desktop/School/capitalscan/
  data/cache/     gitignored, ~11,300 files, ~1.2 GB
  logs/           gitignored
  models/         Git LFS on promotion
  scripts/        .bat wrappers + exported Task Scheduler XML
```

Postgres data lives in a Docker named volume, independent of repo location.

**Spawn, not fork.** `ProcessPoolExecutor` uses spawn on Windows, which re-imports the module in every worker. Three consequences that are code requirements, not notes:

1. Every job module must be importable with no side effects. No top-level execution.
2. Every entry point needs `if __name__ == "__main__":`. Without it, spawn recursively creates processes.
3. Everything passed to a worker must be picklable. Frozen dataclasses from `core/config.py` are. Database connections and file handles are not, so **workers open their own connections**.

Worker startup costs roughly 1 second each. The pool reuses workers, so this is a one-time cost per job, not per task.

Postgres tuning for 32 GB shared with other applications: `shared_buffers=1GB`, `work_mem=32MB`, `maintenance_work_mem=512MB`, `effective_cache_size=4GB`, `max_parallel_workers=8`. Peak footprint during backfill near 4 GB including Python workers.

### 9.4 Scheduling

Windows Task Scheduler with **"Run task as soon as possible after a scheduled start is missed"** enabled, so a job missed while the machine was off fires at next boot regardless of time (ADR 080).

```
16:30 ET  nightly   ingest, indicators, events, path capture, outcomes, sync
09:15 ET  poller    runs until 16:00
Sun 02:00 weekly    backtest, cell_stats, sync
1st 03:00 monthly   retrain, calibrate, promote or hold
```

Path capture added to the nightly line 2026-08-06. Task 10.6 deferred the
wiring to 10.7, 10.7 completed it in `jobs/cli.py::nightly`, and this table
was not updated to match. It must run **after** `events`: a signal that fired
tonight needs its `events` row to exist before it can be selected as an
incomplete-window event to capture path rows for.

Implementation status against this table, as of 2026-08-06. `nightly` runs
everything on its line except `sync` (Phase 5). `weekly` runs `backtest`;
`cell_stats` is Phase 4 and `sync` is Phase 5. `monthly` records its slot and
runs nothing (Phase 6). The weekly `backtest` deliberately does **not** run
the Phase 3 validation harness — see `jobs/cli.py::weekly` for why a
2h28m single-threaded harness has no place in a weekly job.

The weekly `backtest` entry is a **label refresh**, not only a research
re-run, and nothing enforced it until 2026-08-06. Event labels (`mfe`, `mae`,
`touched_*pct`, `capture_ratio`, `fwd_ret_*d`) are written only by
`run_backtest`. An event whose forward window was still open when the backtest
last ran keeps those labels frozen at that moment, permanently, while `path`
keeps growing underneath it. See ADR 094, "Materialized labels need a refresh
job, not just an exclusion filter."

Each task runs a `.bat` wrapper in `scripts/` that activates the virtualenv, sets `CAPSCAN_ENV`, and invokes `cscan <job>`. Task definitions are exported to `scripts/tasks/*.xml` and committed, so the schedule is reproducible rather than living only in a GUI.

`scheduled_runs` records `(job, scheduled_for, actual_start, delay_seconds, status, run_id)`. A delay above 3600 s means the machine was off, surfaced by `cscan status`.

This is safe because every job is idempotent (§2.6): overlapping fetch windows, upserts, windowed recomputes.

GitHub Actions runs exactly one workflow: a health check every 6 hours hitting `/api/health`, alerting if staleness exceeds 2 days.

### 9.5 Deploy pipeline

```
push to main
  → Actions: ruff, ruff format --check, mypy, pytest
  → Vercel: build, migrate preview branch, deploy preview
  → merge: migrate prod, deploy prod
```

Migrations run **before** deploy, never after, and are forward-compatible. A column drop ships one release after the code stops reading it. Alembic handles versioning; both databases receive identical migrations, which is what makes the sync in §8.7 safe.

### 9.6 Monitoring

Nothing hosted. Three mechanisms:

- **Job outcomes.** Every job writes a `runs` row. `ie status` prints last run and staleness per job. Failures notify through the same `Notifier` protocol as signals.
- **Data freshness.** `/api/health` returns last ingest, last indicators, last events, staleness in days.
- **Assertion failures.** Any validation rule breaching threshold fails the job loudly rather than logging and continuing (§4.9).

Logs are rotating JSON lines in `logs/` plus stdout, read with `cscan logs --job nightly --tail 100`. No aggregation service.

### 9.7 Backup

| Asset | Backup | Recovery |
|---|---|---|
| Research DB | Nightly `pg_dump` to second local disk (7 daily + 4 weekly); monthly to GitHub Release asset (~300 MB compressed) | 10 min |
| Serving DB | Neon point-in-time restore, 7 days | Minutes |
| Raw cache | None | Refetch |
| Model artifacts | Git LFS on promotion | Instant |
| Code, config | Git | Instant |

Full disaster recovery from workstation loss: clone, backfill, retrain — roughly 4 hours. Everything is reproducible from public data plus committed code.

**The one exception:** `predictions` and `outcomes` cannot be honestly regenerated after the fact. They live in the serving database on Neon, which is managed and backed up, rather than only locally (ADR 083).

### 9.8 Cost

| Item | Monthly |
|---|---|
| Vercel Hobby, Neon free, GitHub Actions, local compute | $0 |
| Anthropic API | $5-15 |

Phase 7 adds one month of Polygon, $29-199, once.

**Ceiling:** if the serving database approaches 400 MB, tighten the sync filters rather than upgrading. Growth past that means the filters drifted from ADR 053's intent.

---

**Ceiling (ADR 053):** if the serving database approaches 400 MB, tighten the filters rather than upgrading. Growth past that means the filters drifted.

---

## 10. Tools and chat

### 11.1 Tool set

Seven tools, closed enums, dates validated against the ingested window, `limit` capped server-side at 200 (ADR 074).

```python
screen_signals(date, signal_types=["confluence_low"], universe="trade",
               dd_bucket=None, min_strength=None, limit=50) -> ScreenResult

get_stats(signal_type, target_pct=3.0, dd_bucket=None, signal_strength=None,
          entry_kind="next_open", split="validate", ticker=None)
          -> CellStats | Suppressed

get_indicators(ticker, start, end, fields) -> IndicatorSeries

get_events(ticker=None, start=None, end=None, signal_types=None,
           cluster_head_only=True, limit=200) -> EventList

predict(ticker, as_of=None) -> Prediction | NotFound

explain_signal(ticker, date) -> Explanation     # features, cell, SHAP top-5

get_universe(as_of=None) -> UniverseResult      # per-criterion pass/fail
```

`compare_positions` joins in Phase 2 with the trade log.

Tool schemas dominate input tokens, so a small tool count is a cost decision as well as a correctness one.

### 11.2 System prompt

Short, because guardrails live in code. The prompt handles framing, not enforcement.

```
You answer questions about a Bollinger Band and Stochastic Oscillator
event-study database covering US mega-cap equities, 2010 to present.

Rules:
- Every statistical claim comes from a tool result. You perform no
  arithmetic. If a question needs a calculation no tool provides, say so.
- Report n_eff and the confidence interval alongside any probability.
  The tools return them; include them.
- The tools return historical frequencies conditional on past states.
  They are not forecasts of individual outcomes. Frame answers that way.
- When a cell returns suppressed, say there is insufficient data. Do not
  substitute a broader cell without saying you did.
- Distinguish reachability from terminal return. A 60% chance of touching
  +5% at some point in 5 days is not a 60% chance of being up 5% on day 5.
- You do not make claims about the user's financial situation, tax
  position, or suitability. Nothing in the database sources those.
```

Model: `ANTHROPIC_MODEL` env var, Sonnet default, Haiku first for single-tool turns with escalation (ADR 077).

### 11.3 Response validator

Post-turn, pre-delivery (ADR 075).

| Check | Rule |
|---|---|
| Naked probability | A percentage with no `n=` or sample reference within 200 chars |
| Unsourced claim | A statistic present with zero tool calls in the turn |
| Unsourced advisory | An advisory sentence with no tool-sourced number in the same paragraph |
| Suppressed leak | A rate stated for a cell whose tool result was `suppressed` |
| Fabricated ticker | Unrecognized uppercase token — flagged and logged, not rejected |

One retry with the failure reason appended. Second failure returns a fixed message plus raw tool results.

**The validator does not ban advisory language.** This is an advisory system; notifications and reports both state what fired and what historically followed. The risk is *unsourced* advice, not advice. So this passes:

> TSM fired confluence-low today in the 10-20% drawdown bucket. That cell resolved up 3% within 5 sessions in 51% of 340 effective cases against a 39% baseline, CI 46-56.

And this fails:

> TSM looks like a good buy here.

Web fetch stays available for company context, attributed explicitly and never merged into a statistical claim.

### 11.4 MCP server

```
capitalscan-mcp
  transport: streamable HTTP at /mcp
  auth: bearer token, read-only scope
  host: local during development

  tools: the seven above, identical schemas, calling the same handlers
  resources:
    cell://{cell_id}           current stats
    universe://current         active trade universe
    run://{run_id}             backtest metadata and full config
    event://{ticker}/{date}    single event with full state
  prompts:
    /analyze {ticker}          guided walkthrough of current state
    /compare {cell_a} {cell_b} side by side with significance
    /audit {run_id}            what config produced these numbers
```

Built after Phase 4 (ADR 027) — an MCP server over an empty `cell_stats` demonstrates nothing.

The practical payoff is the research workflow: instead of writing a script to check whether strength-2 confluence beats strength-1 in the 20-35% drawdown bucket, ask Claude Code and it calls `get_stats` twice.

---

## 11. Frontend

### 11.1 Routes

| Route | Purpose | Phase |
|---|---|---|
| `/` | Today's screener, default landing | 1 |
| `/ticker/[sym]` | Chart, current state, event history | 1 |
| `/positions` | Open and closed positions, exit signals | 2 |
| `/research` | Cell grid, era breakdown, three arms, drawdown slice | 4 |
| `/chat` | Tool-backed conversation | 5 |
| `/forward` | Calibration curve, model vs cell, miss log | 6 |

### 11.2 Screener

```
Ticker | Signal | Str | %B | %K | DD | Cell hit rate | n_eff | Edge | P(+3%) | P(−3%)
```

- Edge renders as a **bar with CI width**, not a number. A wide bar reads as uncertainty faster than "CI 34-56."
- Suppressed cells render "insufficient data," greyed. Never a number.
- `signal_strength = 2` gets visual weight — the dual-alignment case.
- Row click expands inline (signal_types_all, crossover flags, days-to-earnings, VIX, cofire count) before navigating.
- **Default: all signal types shown,** with a toggle and count badge for filtering down.

**Empty state matters more than usual,** because most days nothing fires: "No signals today. Last fire: TSM, 3 days ago" with a link. An empty table with no context reads as broken.

Staleness banner above the table when `meta.staleness_days > 2`.

### 11.3 Ticker page

Replicates the StockCharts SharpCharts layout. They have no public charting API, so this is built, not integrated.

Three stacked panels on a shared x-axis with a synchronized crosshair:

1. **Price (~60% height).** Candlesticks, three band lines, 200-day SMA, volume histogram at the base colored by up/down day. Event markers as triangles below the low; hover shows entry, exit, reason, realized return.
2. **Stochastic (~20%).** %K full and %D full with dashed reference lines at 20 and 80. %K fast as a lighter line (informational per ADR 045).
3. **Context (~20%).** Bandwidth percentile and drawdown from 52-week high — where the ADR 015 story is visible.

Legend text top-left of each panel showing values at the crosshair. Light grid lines, price axis right, date axis bottom only.

Right rail shows current state, cell membership with `n_eff`/CI/q-value, and the three-part prediction (terminal quantiles, reachability ladder, adverse ladder).

### 11.4 Research page

Four sections, each answering one ADR question:

1. **Cell grid.** Rows are cells, columns are targets. Fill by edge, greyed below the `n_eff` threshold. Click for the event list and return distribution.
2. **Era breakdown.** The same cells across four eras, side by side. The ADR 042 stability check; a cell lighting up in one era only should be visually obvious.
3. **Three arms.** Equity curves with the 200-replication random-entry band shaded, plus the summary table (Sharpe, max drawdown, deployment fraction, capital efficiency).
4. **Drawdown slice.** Edge vs drawdown bucket with confidence bands. ADR 015 calls this the project's central claim, so it gets its own chart.

Plus **exit mix**: stacked bars per cell by exit reason.

### 11.5 Reports: two artifacts

| Artifact | Trigger | Content | Lives at |
|---|---|---|---|
| Research page | Manual, static | Historical cells, eras, arms, drawdown slice | `/research`, weekly |
| Signal report | Indicator fires | Live state, live inference, cell stats, reachability ladder, live call overlay | Notification payload + `/ticker/[sym]` |

Signal reports are **stored, not just sent**, so the forward log can compare what you were told against what happened. Same immutability rule as ADR 029.

### 11.6 Component states

Every data component handles five states explicitly:

| State | Rendering |
|---|---|
| Loading | Skeleton matching final layout, not a spinner |
| Empty | Explanatory text plus the most recent non-empty reference |
| Suppressed | "Insufficient data (n_eff 18, threshold 30)", greyed |
| Stale | Banner with last successful ingest date |
| Error | Message plus error code, with retry |

Suppressed is the one people get wrong. It is not an error and not empty — it is a valid answer meaning the sample is too thin, and rendering it as a number with a footnote defeats ADR 026.

### 11.7 Design direction

Dense, not spacious. An instrument panel, not a marketing page. Small type, tight rows, high information density, no hero sections.

Monospace for all numbers so columns align and digit changes are visible. Proportional for prose only.

Color carries meaning and nothing else: direction, suppressed grey, one selection accent. Colorblind-safe pairing. No decorative gradients. Dark by default.

Charts win where shape matters (distributions, equity curves, calibration). Tables win where lookup matters (screener, cell grid).

**Before writing any component, read the frontend-design skill** — it carries the environment's design tokens and styling constraints, which this document does not cover.

### 11.8 Libraries

- `lightweight-charts` (TradingView) for price and stochastic panels. Native candlesticks, multiple series, synchronized crosshairs across panes. Imperative API, needs `'use client'` and a `useEffect` mount.
- `recharts` for statistical charts on `/research` and `/forward`.

Both ship in v1; cut one later if it proves redundant.

### 11.9 Mobile

The screener and notifications are the mobile case: a push says TSM fired, you open it on a phone.

Mobile scope: screener as cards, ticker page with the price panel plus state rail below, positions list. `/research` and `/forward` are desktop-only and should **say so** rather than degrading badly.

---

## 12. Cross-references

| Topic | Document |
|---|---|
| Why any decision was made | `DECISIONS.md` (87 ADRs) |
| Build order and task breakdown | `BUILD.md` |
| Test inventory and acceptance gates | `TESTS.md` |
| Rules for Claude Code | `CLAUDE.md` |
| Recorded results, including nulls | `RESULTS.md` |
