"""The backtest contract: config in, enriched event table out (BUILD session 9).

This module is the first piece of `research/backtest.py`. It owns nothing
new — it names the config type the rest of session 9 builds against and
re-exports the two labelling functions every later step (entry/exit
resolution, cost application, split-scoped stats) needs, so nothing in
`research/` reaches into `jobs/` directly for them.

**Why `BacktestConfig` is an alias, not a new dataclass.** The session 9
plan named a six-field `BacktestConfig` (`indicators`, `signals`, `exits`,
`costs`, `splits`, `universe`). `core/config.py` already has `Config`, a
frozen dataclass with those six fields plus `stats: StatsParams` — and
`stats` is not optional scope creep: Task 7 reads `StatsParams.reach_targets`
and Task 8 reads `dd_buckets` and `era_bounds`. A second, narrower
`BacktestConfig` would need translating to/from `Config` at every boundary,
and CLAUDE.md invariant 2 ("one signal implementation... never write a
second comparison anywhere") extends to config: two config types that are
supposed to describe the same run is exactly the drift that a second
`config_hash` implementation would cause, just one level up. So
`BacktestConfig` is `Config` under another name, kept for the plan's
readability where a name says "the config a backtest run took," not a
type pytest or Postgres ever have to distinguish.

**Why `config_hash` and `split_key_for` are re-exported, not reimplemented.**
`jobs/config.py:config_hash` is already the exact algorithm this module's
brief specified. `jobs/config.py:split_key_for` is already the exact
train/validate/holdout rule. Both are pure functions of their arguments —
no IO — so importing them here does not violate invariant 1; only calling
IO-performing code from `core/` would. `research/` and `jobs/` sharing one
`config_hash` and one `split_key_for` is the whole point: two hashes or two
split-labelling rules that drift would produce rows whose provenance lies.
"""

from __future__ import annotations

import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, cast

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import DEFAULT_SWEEP, Config, ExitParams
from capitalscan.core.types import Side
from capitalscan.jobs import db_io
from capitalscan.jobs.config import config_hash, split_key_for

# See module docstring: an alias, not a second dataclass (Ruling C1).
BacktestConfig = Config

__all__ = [
    "BacktestConfig",
    "config_hash",
    "split_key_for",
    "BacktestReport",
    "BacktestRunFailed",
    "add_cofire_count",
    "run_backtest",
    "sweep_configs",
]


# ============================================================================
# Task 9b: per-ticker worker, parallel dispatch, cofire post-pass, write
# ============================================================================
#
# `research/candidates.py` (steps 3-6) and `research/enrich.py` (steps 7-11)
# are imported *inside* the functions below, not at module level. `enrich.py`
# already does `from capitalscan.research.backtest import split_key_for`
# (Ruling C2) — a top-level import of `enrich`/`candidates` here would create
# an import cycle the moment `enrich.py` is the first of the two modules
# loaded. A function-body import breaks the cycle without restructuring
# either module, and costs nothing extra: Python caches the import after the
# first call, and the spawned worker process only ever calls
# `_backtest_one_ticker` once per submission anyway.

# One row per `(config_hash, ticker, signal_date, signal_type, entry_kind)` —
# DESIGN §5.7's `events` grain. Column order here is cosmetic (the eventual
# `db_io.upsert` sends a dict per row, not a positional tuple) but pinning it
# keeps `_empty_events_frame()` and every populated frame shape-identical,
# which is what lets `pd.concat` across tickers behave predictably even when
# some workers return zero rows.
_EVENT_COLUMNS = [
    "run_id",
    "config_hash",
    "ticker",
    "signal_date",
    "signal_type",
    "signal_types_all",
    "signal_strength",
    "side",
    "touch_level",
    # state at signal, all read from the t-1 indicator row `_prior_indicator`
    # already looked up (Review Finding 1, session 9 task 9b fix round 1):
    # this worker holds `prior_ind` in hand for every candidate anyway (it
    # is `enrich_context`'s own `ind_row` argument), so leaving these NULL
    # on a row this worker is the sole writer of (`touch_5m`/`touch_30m`/
    # `next_open` — `run_events` never creates those rows, ever, since it
    # hardcodes `entry_kind=EntryKind.TOUCH.value`) would be an unpopulated
    # column, not an honestly-absent one.
    "bb_pctb",
    "bb_width_pct",
    "k_full",
    "d_full",
    "k_fast",
    "k_cross_up",
    "k_cross_down",
    "atr_14",
    "rv_pct_252d",
    "dd_52w",
    "sma200_slope_60",
    "above_sma200",
    "vol_z_20d",
    "days_to_earnings",
    # clustering (§5.3) — backtest-owned (Ruling C5)
    "cluster_id",
    "seq_in_cluster",
    "is_cluster_head",
    "days_since_head",
    # entry (§5.4) — one row per EntryKind
    "entry_kind",
    "entry_date",
    "entry_price",
    "entry_gapped",
    # exit (§5.5)
    "exit_date",
    "exit_price",
    "exit_reason",
    "holding_days",
    "ambiguous",
    # outcome
    "gross_ret",
    "net_ret",
    "mfe",
    "mae",
    "time_to_mfe",
    "capture_ratio",
    # reachability (§5.6), full window regardless of exit timing
    "touched_2pct",
    "day_touched_2pct",
    "touched_3pct",
    "day_touched_3pct",
    "touched_5pct",
    "day_touched_5pct",
    "touched_10pct",
    "day_touched_10pct",
    # unconditional forward returns, baseline comparison
    "fwd_ret_1d",
    "fwd_ret_2d",
    "fwd_ret_3d",
    "fwd_ret_5d",
    "fwd_ret_10d",
    # context tags (step 11) and split assignment (step 12)
    "dd_bucket",
    "bw_regime",
    "era",
    "earnings_in_window",
    "split_key",
    "vix_close",
    "spx_ret_1d",
]

# `_RUN_BACKTEST_UPDATE_COLUMNS` (Ruling C4/C5): the columns this job may
# overwrite on an `events` row that already exists (e.g. one `run_events`
# wrote first, per `jobs/compute.py`'s module docstring: "session 9 fills
# them in on the same row"). Everything the backtest actually *computes* —
# signal identity, state-at-signal, entry (all four kinds, not just TOUCH),
# exit, outcome, reachability, forward-return, context/split columns, and
# the four cluster columns Ruling C5 assigns to the backtest exclusively.
#
# Review Finding 1 (session 9 task 9b fix round 1) corrected this list: an
# earlier version excluded the state-at-signal columns (`bb_pctb`,
# `bb_width_pct`, `k_full`, `d_full`, `k_fast`, `k_cross_up`, `k_cross_down`,
# `atr_14`, `rv_pct_252d`, `dd_52w`, `sma200_slope_60`, `above_sma200`,
# `vol_z_20d`, `days_to_earnings`) on the theory that `scan_candidates`
# doesn't retain them. That reasoning missed that `_backtest_one_ticker`
# independently looks up the same t-1 indicator row via `_prior_indicator`
# to build `enrich_context`'s `ind_row` argument — the value is already in
# hand, known, and was simply omitted from the row dict. Worse: `run_events`
# hardcodes `entry_kind=EntryKind.TOUCH.value` (`jobs/compute.py:721`), so
# the `touch_5m`/`touch_30m`/`next_open` rows have no second writer, ever —
# there is no future `run_events` pass that fills these in. Leaving them
# NULL there was an unpopulated column, not an honestly-absent one
# (invariant 4 sanctions the latter, not the former).
#
# All of these overlap with `_RUN_EVENTS_UPDATE_COLUMNS` (`run_id`,
# `signal_types_all`, `signal_strength`, `side`, `touch_level`, and now the
# state-at-signal columns too). This is deliberate, not an oversight: both
# jobs derive them from the same `core.signals.detect` output read off the
# same t-1 indicator row (invariant 2 — one signal implementation, reached
# two ways), so the two jobs cannot disagree on these values. Keeping both
# writable is the same precedent `_RUN_EVENTS_UPDATE_COLUMNS` already set for
# `entry_date`/`entry_price`/`entry_gapped`, `dd_bucket`, `split_key`,
# `vix_close`, and `spx_ret_1d`.
_RUN_BACKTEST_UPDATE_COLUMNS = [
    "run_id",
    "signal_types_all",
    "signal_strength",
    "side",
    "touch_level",
    "bb_pctb",
    "bb_width_pct",
    "k_full",
    "d_full",
    "k_fast",
    "k_cross_up",
    "k_cross_down",
    "atr_14",
    "rv_pct_252d",
    "dd_52w",
    "sma200_slope_60",
    "above_sma200",
    "vol_z_20d",
    "days_to_earnings",
    "cluster_id",
    "seq_in_cluster",
    "is_cluster_head",
    "days_since_head",
    "entry_date",
    "entry_price",
    "entry_gapped",
    "exit_date",
    "exit_price",
    "exit_reason",
    "holding_days",
    "ambiguous",
    "gross_ret",
    "net_ret",
    "mfe",
    "mae",
    "time_to_mfe",
    "capture_ratio",
    "touched_2pct",
    "day_touched_2pct",
    "touched_3pct",
    "day_touched_3pct",
    "touched_5pct",
    "day_touched_5pct",
    "touched_10pct",
    "day_touched_10pct",
    "fwd_ret_1d",
    "fwd_ret_2d",
    "fwd_ret_3d",
    "fwd_ret_5d",
    "fwd_ret_10d",
    "dd_bucket",
    "bw_regime",
    "era",
    "cofire_count",
    "earnings_in_window",
    "split_key",
    "vix_close",
    "spx_ret_1d",
]


class BacktestRunFailed(RuntimeError):
    """Raised by `run_backtest` when every dispatched ticker's worker raised
    (Review Finding A, fix round 2).

    Distinguishes a **config-level fault** from ordinary **per-ticker bad
    data**. Every worker resolves the identical `config`, so a fault in the
    config itself (Finding 5's example: `StatsParams.reach_targets` swept to
    a value the fixed `events` schema has no column for) raises identically
    on every ticker — the same failure N times, not N independent problems.
    Ordinary bad data (a `tag_clusters` gap on one ticker's trading
    calendar, a corrupt row) fails on some tickers and not others, and stays
    in `BacktestReport.failed_tickers` for a partial run that still writes
    what succeeded (Finding 4's fix, unchanged by this one).

    Finding 4's per-ticker `try/except` made total failure silent: it turned
    a hard config break into `BacktestReport(rows_written=0)`, indistinguishable
    from a legitimate empty run to a caller checking only `rows_written`. A
    Task 12 sweep running 18 configs needs a malformed one to stop the
    sweep, not report a quiet zero next to 17 real results.

    `failed_tickers` is preserved as an attribute (the full `{ticker:
    "ExcType: message"}` mapping) for a caller that wants ticker-level
    detail. The exception's own message deduplicates the failure strings —
    a config fault produces the identical message on every ticker, and
    repeating one line N times would bury the useful information instead of
    surfacing it.
    """

    def __init__(self, failed_tickers: dict[str, str]):
        self.failed_tickers = dict(failed_tickers)
        unique_messages = sorted(set(failed_tickers.values()))
        super().__init__(
            f"run_backtest: all {len(failed_tickers)} dispatched ticker(s) failed — "
            "a config-level fault is more likely than that many independent "
            f"per-ticker data problems. Distinct failure(s): {'; '.join(unique_messages)}"
        )


@dataclass
class BacktestReport:
    """Mirrors `jobs.ingest.IngestReport`'s shape without importing `jobs/`
    at module level for it — `research/` owns its own IO per CLAUDE.md, and
    the two report types have no reason to share an implementation.

    `failed_tickers` (Review Finding 4, fix round 1): `{ticker: "ExcType:
    message"}` for every ticker whose worker raised. `run_backtest` writes
    every *successful* ticker's rows regardless — a `tag_clusters` or
    `resolve_exit_for_entry` raise on one ticker must not discard hours of
    work already done for the rest of a full-universe run. Mirrors
    `IngestReport.rows_flagged`'s idiom of surfacing a problem on the report
    object rather than the caller having no way to find out which ticker(s)
    need a rerun.
    """

    run_id: str
    rows_written: int = 0
    tickers: list[str] = field(default_factory=list)
    failed_tickers: dict[str, str] = field(default_factory=dict)


def _empty_events_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_EVENT_COLUMNS)


# ---------------------------------------------------------------------------
# Reads (research/ owns its own IO; no cross-import into jobs/compute.py's
# read helpers, since those default their windows differently — see
# `_backtest_one_ticker`'s own docstring for the read-window rule this
# module uses instead).
# ---------------------------------------------------------------------------


def _read_bars(engine: Engine, ticker: str, start: date, interval: str) -> pd.DataFrame:
    params: dict[str, Any] = {"ticker": ticker, "interval": interval, "start": start}
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT * FROM bars WHERE ticker = :ticker AND interval = :interval "
                "AND ts >= :start ORDER BY ts"
            ),
            conn,
            params=params,
        )
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
    return df


def _read_indicators(engine: Engine, ticker: str, start: date) -> pd.DataFrame:
    params: dict[str, Any] = {"ticker": ticker, "start": start}
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT * FROM indicators WHERE ticker = :ticker AND interval = '1d' "
                "AND ts >= :start ORDER BY ts"
            ),
            conn,
            params=params,
        )
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
    return df


def _read_market_days(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT * FROM market_days ORDER BY ts"), conn)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
    return df


def _read_universe_flags(engine: Engine, ticker: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text("SELECT ticker, as_of, in_trade FROM universe WHERE ticker = :ticker"),
            conn,
            params={"ticker": ticker},
        )


def _prior_indicator(ind_by_date: pd.DataFrame, signal_date: date) -> pd.Series | None:
    """Latest indicator row strictly before `signal_date` (Ruling C3 —
    date-based, never positional). Same lookup `research.candidates.
    scan_candidates` already performs internally to detect the signal in
    the first place; that function does not surface the row it used (its
    own candidate columns carry no indicator fields — see
    `_RUN_BACKTEST_UPDATE_COLUMNS`'s comment), so this worker re-derives it
    once more, here, purely as a date lookup — not a second detection
    implementation (invariant 2 covers the *signal* decision, not "what row
    did the signal read").
    """
    if ind_by_date.empty:
        return None
    prior_dates = ind_by_date.index[ind_by_date.index < signal_date]
    if len(prior_dates) == 0:
        return None
    return cast("pd.Series", ind_by_date.loc[prior_dates.max()])


def _entry_idx_for(entry: dict, ticker_bars: pd.DataFrame) -> int | None:
    """Position of the bar `entry` actually filled on, inside `ticker_bars`
    (a Timestamp-indexed, date-sorted frame — see `_backtest_one_ticker`).

    Derived from `entry["entry_date"]`, never from `entry["entry_kind"]`
    (CONSTRAINTS.md item 3): `TOUCH`/`TOUCH_5M`/`TOUCH_30M` fill on the
    signal bar, `NEXT_OPEN` fills one bar later, and reading the date off
    the entry dict itself is what keeps a future entry kind from silently
    inheriting the wrong assumption. `None` when the entry never filled at
    all (`entry_date is None` — the terminal-bar `NEXT_OPEN` case; DESIGN
    §5.4), which is distinct from a NaN `entry_price` at a *known* date
    (the pre-2024 hourly case) — that one still resolves a real index here,
    because `resolve_exit_for_entry` needs it to short-circuit correctly
    and `path_metrics`' unconditional `fwd_ret_*d` still wants a price
    anchor even when the specific entry kind never filled.
    """
    if entry["entry_date"] is None:
        return None
    ts = pd.Timestamp(entry["entry_date"])
    if ts not in ticker_bars.index:
        return None
    # `ticker_bars` is date-indexed and unique (see the docstring), so this
    # is `get_loc`'s scalar branch — the declared `int | None` return already
    # commits to that.
    return cast("int", ticker_bars.index.get_loc(ts))


def _backtest_one_ticker(
    ticker: str,
    config: Config,
    run_id: str,
    database_url: str | None,
    today: date | None = None,
) -> pd.DataFrame:
    """Runs in a worker process under `ProcessPoolExecutor(spawn)` — mirrors
    `jobs.compute._compute_one_ticker`'s spawn-safety template (CLAUDE.md
    "Platform"): opens its own connection (engines are not picklable),
    performs no top-level side effects, and is fully importable on its own
    so the spawned process's re-import does nothing but define this
    function.

    Executes DESIGN §5.2 steps 1-12 for one ticker: load bars/indicators ->
    join universe -> candidate scan -> eligibility -> debounce -> cluster
    tag -> entry resolution -> exit resolution -> path metrics -> costs ->
    context -> split. Step 13 (`cofire_count`) groups across tickers and
    cannot run here — see `add_cofire_count`, called once by `run_backtest`
    after every worker returns.

    Returns one row per (candidate event, entry kind) — up to four rows per
    signal, `research.enrich.resolve_entries`' `EntryKind` fan-out. An entry
    that never filled (`entry_price` is NaN: a pre-2024 hourly kind, or
    `NEXT_OPEN` on the terminal bar) is still written, never dropped
    (invariant 4; DESIGN §5.4 treats pre-2024 hourly absence as an expected
    data limitation, not an error a missing row should silently hide from a
    downstream `COUNT(*)`). Every exit/outcome/path column on that row is
    null instead. State-at-signal columns (`bb_pctb`, `k_full`, `dd_52w`,
    ...), by contrast, are the SAME on all four rows for one signal — they
    describe the t-1 indicator row the signal fired against, not the entry
    kind — so every row gets them, never just `touch` (Review Finding 1).

    No wall-clock read (ADR 060): `apply_eligibility`'s `today` bound
    defaults to `None`, which resolves to `max(bars.ts)` — a function of the
    loaded data, not the clock, so the same config against the same database
    snapshot always resolves the same bound. `run_backtest` may pass an
    explicit `today` to override this uniformly across every ticker in a
    run (controller ruling, fix round 1); `None` here always means
    "per-ticker, derived from that ticker's own data."
    """
    # Deferred import — see the module-level comment above `_EVENT_COLUMNS`
    # on why `candidates`/`enrich` cannot be imported at module scope here.
    from capitalscan.research import candidates as research_candidates
    from capitalscan.research import enrich as research_enrich

    engine = db_io.get_engine(database_url)
    chash = config_hash(config)

    bars = _read_bars(engine, ticker, date.fromisoformat(config.splits.ingest_start), "1d")
    if bars.empty:
        return _empty_events_frame()

    indicators = _read_indicators(engine, ticker, date.fromisoformat(config.splits.event_start))
    hourly = _read_bars(engine, ticker, date.fromisoformat(config.splits.ingest_start), "1h")
    market = _read_market_days(engine)
    universe_flags = _read_universe_flags(engine, ticker)

    candidates, _null_rejects = research_candidates.scan_candidates(
        bars, indicators, config.signals
    )
    if candidates.empty:
        return _empty_events_frame()

    resolved_today = today if today is not None else bars["ts"].max().date()
    eligible, _elig_rejects = research_candidates.apply_eligibility(
        candidates, universe_flags, config.splits, today=resolved_today
    )
    if eligible.empty:
        return _empty_events_frame()

    deduped = research_candidates.debounce(eligible)
    trading_dates = {ticker: sorted(bars["ts"].dt.date.unique().tolist())}
    clustered = research_candidates.tag_clusters(deduped, config.exits.max_hold_days, trading_dates)

    # Timestamp index, not `date` (unlike `research.enrich._ticker_bars`'s
    # own convention): `core.exits.resolve_exit` calls `.date()` on
    # `fwd_bars.index[exit_idx]` to build `ExitResult.exit_date`, which
    # requires a `pd.Timestamp` there, not a plain `date`.
    ticker_bars = bars.sort_values("ts").set_index("ts", drop=False)
    if indicators.empty:
        aligned_ind = pd.DataFrame(index=ticker_bars.index)
        ind_by_date = indicators
    else:
        ind_by_ts = indicators.sort_values("ts").set_index("ts")
        # Reindexed onto the bars' own dates so `resolve_exit_for_entry`'s
        # `bars.iloc[k]` and `indicators.iloc[k]` address the same trading
        # day (that function's own documented precondition). Dates with no
        # indicator row (pre-warmup history) come back NaN, which every
        # reader downstream (`core.exits._level`, `_isnan`) already treats
        # as "no data," never a substituted value.
        aligned_ind = ind_by_ts.reindex(ticker_bars.index)
        ind_by_date = indicators.set_index(indicators["ts"].dt.date)

    market_by_date = market.set_index(market["ts"].dt.date) if not market.empty else market
    hourly_or_none = hourly if not hourly.empty else None
    horizon_span = max(config.stats.fwd_ret_horizons) if config.stats.fwd_ret_horizons else 0

    rows: list[dict] = []
    for _, cand in clustered.iterrows():
        signal_date = cand["signal_date"]
        if isinstance(signal_date, pd.Timestamp):
            signal_date = signal_date.date()
        side = Side(cand["side"])

        prior_ind = _prior_indicator(ind_by_date, signal_date)
        if prior_ind is None:
            prior_ind = pd.Series(dtype=object)
        market_row = (
            market_by_date.loc[signal_date]
            if (not market.empty and signal_date in market_by_date.index)
            else None
        )

        # State at signal (Review Finding 1): read once per candidate — the
        # same t-1 row backs all four entry-kind rows for this signal, since
        # it describes the state the signal fired against, not the entry.
        # `above_sma200` needs the signal bar's own `close` (not `prior_ind`,
        # which is the day *before*), matching `jobs.compute._build_event_
        # row`'s `bool(bar["close"] > ind_row["sma_200"])` exactly — including
        # its null test: `pd.isna`, not `core.signals._isnan` (Review
        # Finding C, fix round 2). The two writers of this same column
        # should use the same null spelling, not merely an equivalent one;
        # `_isnan` also tolerates non-numeric strings via a try/except
        # `float()` that `above_sma200`'s inputs never need. Genuine null
        # (not an omission) when either side is unavailable — e.g. this
        # module's own test fixtures, which do not carry `sma_200` at all.
        signal_ts = pd.Timestamp(signal_date)
        bar_at_signal = ticker_bars.loc[signal_ts] if signal_ts in ticker_bars.index else None
        sma_200 = prior_ind.get("sma_200")
        close_at_signal = None if bar_at_signal is None else bar_at_signal.get("close")
        above_sma200 = (
            None
            if pd.isna(sma_200) or pd.isna(close_at_signal)
            else bool(float(cast("float", close_at_signal)) > float(sma_200))
        )

        # `bars`, not `ticker_bars`: `resolve_entries` -> `_ticker_bars`
        # does its own `sort_values("ts")` + date-indexing internally, and
        # `ticker_bars` below is already indexed *by* `ts` (kept as a
        # column too, for `resolve_exit_for_entry`'s positional slicing) —
        # handing that back in would make "ts" both an index level and a
        # column label, which pandas' own `sort_values` rejects as
        # ambiguous.
        entries = research_enrich.resolve_entries(cand, bars, hourly_or_none, config.costs)
        for entry in entries:
            entry_idx = _entry_idx_for(entry, ticker_bars)
            if entry_idx is None:
                # Terminal-bar NEXT_OPEN: no bar to open on at all, so there
                # is nothing for `resolve_exit_for_entry` to validate an
                # index against. Same null shape that function returns for
                # its own two "can't resolve" cases (see its docstring).
                exit_result: dict = {
                    "exit_idx": None,
                    "exit_date": None,
                    "exit_price": float("nan"),
                    "exit_reason": None,
                    "holding_days": None,
                    "ambiguous": None,
                }
                fwd_window = ticker_bars.iloc[0:0]
                adj_close_fwd = None
            else:
                exit_result = research_enrich.resolve_exit_for_entry(
                    entry, entry_idx, side, ticker_bars, aligned_ind, config.exits
                )
                # Split-adjusted OHLC, the FULL [t+1, t+max_hold] window
                # regardless of where the exit landed (DESIGN §5.6,
                # CONSTRAINTS.md item 5) — `path_metrics` truncates to
                # `exit_idx` internally for MFE/MAE and uses the full frame
                # for reachability.
                fwd_window = ticker_bars.iloc[
                    entry_idx + 1 : entry_idx + 1 + config.exits.max_hold_days
                ]
                # Total-return adjusted close (DESIGN §2.2) — `adj_close`,
                # never `close` — because `fwd_ret_*d` measures return, not
                # price level. Starts at the entry bar itself per
                # `path_metrics`' own precondition.
                adj_close_fwd = ticker_bars["adj_close"].iloc[
                    entry_idx : entry_idx + horizon_span + 1
                ]

            path = research_enrich.path_metrics(
                entry_price=entry["entry_price"],
                side=side,
                fwd_bars=fwd_window,
                exit_idx=exit_result["exit_idx"],
                exit_price=exit_result["exit_price"],
                targets=config.stats.reach_targets,
                adj_close_fwd=adj_close_fwd,
                horizons=config.stats.fwd_ret_horizons,
                capture_ratio_cap=config.exits.capture_ratio_cap,
            )
            # Review Finding 5: `StatsParams.reach_targets`/`fwd_ret_horizons`
            # are sweepable (invariant 9's config values, not literals), but
            # the `events` schema's reachability/forward-return columns are
            # fixed (DESIGN §5.7: 2/3/5/10pct, 1/2/3/5/10d). A sweep cell
            # that changes either tuple would make `path_metrics` return a
            # column name `_EVENT_COLUMNS` has no slot for —
            # `pd.DataFrame(rows, columns=_EVENT_COLUMNS)` would silently
            # drop it, and the "real" column stays NaN with no error at all.
            # Raise loudly instead.
            unknown_path_keys = set(path) - set(_EVENT_COLUMNS)
            if unknown_path_keys:
                raise ValueError(
                    f"path_metrics returned column(s) {sorted(unknown_path_keys)} not "
                    "present in _EVENT_COLUMNS — StatsParams.reach_targets or "
                    "fwd_ret_horizons was swept to a value the fixed `events` schema "
                    "has no column for (DESIGN §5.7 fixes these at 2/3/5/10pct and "
                    "1/2/3/5/10d)."
                )

            context = research_enrich.enrich_context(
                {
                    "side": side,
                    "signal_date": signal_date,
                    "entry_price": entry["entry_price"],
                    "exit_price": exit_result["exit_price"],
                    "holding_days": exit_result["holding_days"],
                },
                prior_ind,
                market_row,
                config.stats,
                config.splits,
                config.costs,
                config.exits,
            )

            ambiguous = exit_result["ambiguous"]
            row = {
                "run_id": run_id,
                "config_hash": chash,
                "ticker": ticker,
                "signal_date": signal_date,
                "signal_type": cand["signal_type"],
                "signal_types_all": cand["signal_types_all"],
                "signal_strength": cand["signal_strength"],
                "side": side.value,
                "touch_level": cand["touch_level"],
                "bb_pctb": prior_ind.get("bb_pctb"),
                "bb_width_pct": prior_ind.get("bb_width_pct"),
                "k_full": prior_ind.get("k_full"),
                "d_full": prior_ind.get("d_full"),
                "k_fast": prior_ind.get("k_fast"),
                "k_cross_up": prior_ind.get("k_cross_up"),
                "k_cross_down": prior_ind.get("k_cross_down"),
                "atr_14": prior_ind.get("atr_14"),
                "rv_pct_252d": prior_ind.get("rv_pct_252d"),
                "dd_52w": prior_ind.get("dd_52w"),
                "sma200_slope_60": prior_ind.get("sma200_slope_60"),
                "above_sma200": above_sma200,
                "vol_z_20d": prior_ind.get("vol_z_20d"),
                "days_to_earnings": prior_ind.get("days_to_earnings"),
                "cluster_id": cand["cluster_id"],
                "seq_in_cluster": cand["seq_in_cluster"],
                "is_cluster_head": cand["is_cluster_head"],
                "days_since_head": cand["days_since_head"],
                "entry_kind": entry["entry_kind"],
                "entry_date": entry["entry_date"],
                "entry_price": entry["entry_price"],
                "entry_gapped": entry["entry_gapped"],
                "exit_date": exit_result["exit_date"],
                "exit_price": exit_result["exit_price"],
                "exit_reason": exit_result["exit_reason"],
                "holding_days": exit_result["holding_days"],
                # NOT NULL DEFAULT false in the schema (DESIGN §5.7); an
                # unresolved position has no stop/target collision to flag,
                # which is honestly `False`, not an unrepresentable NULL.
                "ambiguous": bool(ambiguous) if ambiguous is not None else False,
                "gross_ret": context["gross_ret"],
                "net_ret": context["net_ret"],
                **path,
                "dd_bucket": context["dd_bucket"],
                "bw_regime": context["bw_regime"],
                "era": context["era"],
                "earnings_in_window": context["earnings_in_window"],
                "split_key": context["split_key"],
                "vix_close": context["vix_close"],
                "spx_ret_1d": context["spx_ret_1d"],
            }
            rows.append(row)

    return pd.DataFrame(rows, columns=_EVENT_COLUMNS) if rows else _empty_events_frame()


def add_cofire_count(events: pd.DataFrame) -> pd.DataFrame:
    """DESIGN §5.2 step 13. Single-threaded post-pass, run once after every
    per-ticker worker returns — it groups **across** tickers by
    `(signal_date, signal_type)`, which a per-ticker worker structurally
    cannot see (`_backtest_one_ticker` only ever has its own ticker's rows
    in hand).

    Counts distinct **tickers**, not rows: the grain is `(config_hash,
    ticker, signal_date, signal_type, entry_kind)`, so a single ticker's own event
    contributes up to four rows (one per `EntryKind`) that must not inflate
    the count. `groupby(...).transform("nunique")` on `ticker` is immune to
    that fan-out — two tickers firing the same type on the same date read
    `cofire_count == 2` regardless of how many entry-kind rows each wrote.

    **This counts only the tickers present in `events`.** It is correct
    precisely when `events` covers the whole trade universe for its
    `signal_date` range — a partial-ticker run undercounts every co-fire,
    and `run_backtest`'s `full_universe` parameter (Review Finding 3, fix
    round 1) exists to keep an undercount from silently overwriting a
    previously-correct universe-wide value in the database. This function
    itself has no way to know whether its input is the whole universe or
    not — that judgment belongs to the caller, which is why `run_backtest`
    makes it an explicit, named parameter rather than inferring it here.

    Returns a new frame (CLAUDE.md "never mutate in place").
    """
    out = events.copy()
    if out.empty:
        out["cofire_count"] = pd.Series(dtype="int64")
        return out
    out["cofire_count"] = out.groupby(["signal_date", "signal_type"])["ticker"].transform("nunique")
    return out


def run_backtest(
    tickers: list[str],
    config: Config,
    run_id: str,
    engine: Engine | None = None,
    max_workers: int = 1,
    today: date | None = None,
    full_universe: bool = True,
) -> BacktestReport:
    """DESIGN §5.1/§5.8: config in, `events` rows out. Dispatches one worker
    per ticker (`_backtest_one_ticker`), runs the cross-ticker
    `add_cofire_count` post-pass, and writes with `db_io.upsert`, scoped to
    `_RUN_BACKTEST_UPDATE_COLUMNS` (Ruling C4).

    Determinism (ADR 060): `tickers` is sorted before dispatch and the
    collected frame is sorted by `(ticker, signal_date, entry_kind)` before
    writing, because `as_completed` (used when `max_workers > 1`) returns in
    completion order, not submission order. `run_id` is accepted as a
    parameter, never generated here — no wall-clock read, and no `runs` row
    is created by this function either; the caller (Task 11's CLI) owns
    that, the same way `run_id` itself is already owned by the caller.

    `engine.url.render_as_string(hide_password=False)` — never `str(engine.
    url)`, which masks the password as `***` and leaves a spawned worker
    unable to connect (CONSTRAINTS.md item 1) — is what turns the (unpicklable)
    `Engine` into a string every worker can rebuild its own connection from.

    `today` (controller ruling, fix round 1): `None` (the default) means
    "per-ticker, derived from that ticker's own loaded bars" — the original
    ADR 060-compliant behavior, still the honest bound for a ticker whose
    data happens to end earlier than another's. Passing an explicit `date`
    overrides that derivation uniformly across every ticker in this run, for
    a caller (Task 11's future CLI) that needs one fixed "as of" boundary
    instead.

    `full_universe` (Review Finding 3, fix round 1): `add_cofire_count`
    counts distinct tickers **within this run's own frame**, which is only
    the true cross-ticker co-fire count when `tickers` is the whole trade
    universe. `full_universe=True` (the default, matching every existing
    caller) asserts that it is, and `cofire_count` is written normally.
    Pass `full_universe=False` for a deliberately partial run (e.g. a future
    `--tickers` debug flag) — `cofire_count` is still computed and returned
    on the frame for visibility, but is dropped from this write's
    `update_columns`, so a subset run can never silently overwrite a
    previously-correct universe-wide count with an undercount capped at
    `len(tickers)`. A brand-new row from a partial run still gets the
    subset-scoped count on INSERT (nothing else has computed one yet); nothing
    corrects it until a following whole-universe run passes back over the
    same rows. A `UserWarning` is raised in this case so the gap is loud,
    not silent.

    Raises `BacktestRunFailed` (Review Finding A, fix round 2) if **every**
    dispatched ticker's worker raised — a config-level fault, since every
    worker resolves the same `config`. Partial failure (some tickers failed,
    at least one succeeded) does not raise: it writes what succeeded and
    records the rest on `BacktestReport.failed_tickers`, per Finding 4.
    """
    engine = engine or db_io.get_engine()
    sorted_tickers = sorted(set(tickers))
    database_url = engine.url.render_as_string(hide_password=False)

    if not full_universe:
        warnings.warn(
            "run_backtest(full_universe=False): cofire_count computed over "
            f"this run's {len(sorted_tickers)}-ticker subset will NOT overwrite "
            "any existing universe-wide value in the database (excluded from "
            "this write's update_columns) — it is only correct within this "
            "subset. A following whole-universe run is required to correct it.",
            UserWarning,
            stacklevel=2,
        )

    # Review Finding 4: one ticker's worker raising (`tag_clusters` on a
    # missing trading date, `resolve_exit_for_entry` on an entry_idx
    # mismatch — both live, not theoretical) must not discard every other
    # ticker's already-completed work. Caught per-ticker, recorded on the
    # report, and the run proceeds to write whatever succeeded.
    frames: list[pd.DataFrame] = []
    failed_tickers: dict[str, str] = {}
    if max_workers <= 1:
        for ticker in sorted_tickers:
            try:
                frames.append(_backtest_one_ticker(ticker, config, run_id, database_url, today))
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                failed_tickers[ticker] = f"{type(exc).__name__}: {exc}"
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _backtest_one_ticker, ticker, config, run_id, database_url, today
                ): ticker
                for ticker in sorted_tickers
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    frames.append(future.result())
                except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                    failed_tickers[ticker] = f"{type(exc).__name__}: {exc}"

    # Review Finding A, fix round 2: total failure re-raises rather than
    # returning a report Finding 4's partial-failure path would otherwise
    # make look like a clean, empty run. Partial failure (some tickers
    # failed, at least one succeeded) is untouched — that path is correct
    # as-is per the round 1 review.
    if sorted_tickers and len(failed_tickers) == len(sorted_tickers):
        raise BacktestRunFailed(failed_tickers)

    events = pd.concat(frames, ignore_index=True) if frames else _empty_events_frame()
    events = add_cofire_count(events)
    if not events.empty:
        # Final-review Finding 2: `signal_type` must be part of the sort key.
        # One ticker can fire both a long-side and a short-side signal on
        # the same day, giving two rows equal on `["ticker", "signal_date",
        # "entry_kind"]` alone — `sort_values`'s default `kind="quicksort"`
        # is not stable, so those rows' relative order was not reproducible
        # across runs without this (ADR 060 determinism). `signal_type`
        # rather than `kind="stable"` because it names the actual tiebreaker
        # explicitly, instead of leaning on sort-algorithm behavior to paper
        # over an underspecified key.
        events = events.sort_values(
            ["ticker", "signal_date", "signal_type", "entry_kind"]
        ).reset_index(drop=True)

    update_columns = _RUN_BACKTEST_UPDATE_COLUMNS
    if not full_universe:
        update_columns = [c for c in _RUN_BACKTEST_UPDATE_COLUMNS if c != "cofire_count"]

    rows_written = 0
    if not events.empty:
        rows_written = db_io.upsert(
            engine,
            "events",
            events,
            ["config_hash", "ticker", "signal_date", "signal_type", "entry_kind"],
            update_columns=update_columns,
        )

    return BacktestReport(
        run_id=run_id,
        rows_written=rows_written,
        tickers=sorted(events["ticker"].unique().tolist()) if not events.empty else [],
        failed_tickers=failed_tickers,
    )


def sweep_configs(base: BacktestConfig) -> list[BacktestConfig]:
    """DESIGN §5.9's exit-parameter grid (Session 9 Task 12): exactly 18
    `Config` variants of `base`, each with a distinct `config_hash`.

    **Config generator only — this function does not run anything.** It
    reads no bars, opens no database connection, and resolves no entry or
    exit. Turning the 18 configs it returns into 18 `run_backtest` calls
    (the actual sweep) is a decision the controller has explicitly held
    pending human approval — see CONSTRAINTS.md and task-12-report.md — so
    it is out of scope here on purpose, not an oversight.

    Grid: `stop_mode` (atr, fixed, none) x `stop_atr_k` (1.0, 1.5, 2.0, 2.5)
    x `target_pct` (0.03, 0.04, 0.05), collapsing to `(4 + 1 + 1) x 3 = 18`,
    never `3 x 4 x 3 = 36`. `stop_atr_k` only has behavioral meaning under
    `stop_mode="atr"` (`core/exits.py`: `"fixed"` uses `stop_fixed_pct`
    instead, `"none"` places no stop at all), so sweeping it under `"fixed"`
    or `"none"` too would produce pairs of configs that resolve to the
    identical stop but hash differently — Phase 4 would then report the
    same result twice as if it were two independent findings. Instead this
    builds exactly 6 stop variants (4 atr k-values + 1 fixed + 1 none), each
    then paired with all 3 targets.

    Under `"fixed"`/`"none"`, `stop_atr_k` is held at `base.exits.
    stop_atr_k` rather than swept — it has no effect on either mode's
    resolved stop, so sweeping it there would be varying a parameter the
    engine never reads, purely to manufacture a distinct hash. Holding it
    at the base value keeps every hash difference in the 18-config set tied
    to an actual behavioral difference, per the brief.

    `SweepParams` (`core/config.py`) holds the grid literals, not this
    function, per invariant 9 — see that dataclass's docstring for why it
    is not a field of `Config` (it does not change `config_hash` for any
    resolved config, swept or not).

    `dataclasses.replace` derives each variant from `base` without mutating
    it (CLAUDE.md "never mutate in place"); only `exits` differs from
    `base` in the returned configs — `indicators`, `signals`, `costs`,
    `universe`, `stats`, and `splits` are untouched, including
    `ExitParams.exit_stoch_threshold`/`exit_stoch_threshold_short`, which
    DESIGN §5.9 does not put in this grid.
    """
    stop_variants: list[ExitParams] = [
        replace(base.exits, stop_mode="atr", stop_atr_k=k) for k in DEFAULT_SWEEP.stop_atr_ks
    ]
    stop_variants.append(replace(base.exits, stop_mode="fixed"))
    stop_variants.append(replace(base.exits, stop_mode="none"))

    return [
        replace(base, exits=replace(stop_exits, target_pct=target))
        for stop_exits in stop_variants
        for target in DEFAULT_SWEEP.target_pcts
    ]


if __name__ == "__main__":
    # CLAUDE.md "Platform": every job module needs this guard so
    # ProcessPoolExecutor's spawn re-import does not re-run anything at
    # import time. There is nothing to run standalone here — Task 11 owns
    # the CLI entry point — this guard exists purely so the import-time
    # side-effect check passes.
    pass
