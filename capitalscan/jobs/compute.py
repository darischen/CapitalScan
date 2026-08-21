"""Compute jobs: indicators, universe, events, and `scan()` (BUILD session 6, ADR 049).

**v1 scope note (ADR 049).** The exit engine — entry resolution beyond
`TOUCH`, exit resolution, MFE/MAE, cost application, context tagging, split
statistics — ships in Phase 3 (`research/backtest.py`, BUILD session 9),
not here. `events` rows written by this module carry every signal-detection
column plus `entry_kind='touch'`, its same-bar entry price (cheap: no
forward bars needed, DESIGN §5.4), and `split_key` (assigned at creation
per invariant 5). Every exit/return column is left null; session 9 fills
them in on the same row via the `(config_hash, ticker, signal_date,
signal_type, entry_kind)` key.

**Closed in Session 9 Task 9a (Ruling C4).** `jobs.db_io.upsert` used to
overwrite every non-key column with `EXCLUDED`, including columns a row
dict omits (Postgres fills those as NULL before `EXCLUDED` sees them). A
plain re-run of `run_events` was always safe on its own — it always sends
the same columns — but the backtest (`research/backtest.py`, Task 9b)
writing entry/exit columns onto a row this job already wrote could not go
through that same blind upsert without nulling its own columns on the next
`run_events` re-run. `db_io.upsert` now takes an optional `update_columns`
list; `run_events` passes `_RUN_EVENTS_UPDATE_COLUMNS` (declared just above
`_build_event_row`), which is scoped to the signal-side columns this job
actually computes.
"""

from __future__ import annotations

import hashlib
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from typing import Any, cast

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core import indicators as core_indicators
from capitalscan.core import signals as core_signals
from capitalscan.core import universe as core_universe
from capitalscan.core.config import (
    Config,
    ExitParams,
    IndicatorParams,
    SignalParams,
    SplitParams,
    UniverseParams,
)
from capitalscan.core.returns import entry_price_for
from capitalscan.core.signals import CLOSE_CONFIRMED_FIELDS, debounce_key
from capitalscan.core.types import Bound, EntryKind
from capitalscan.jobs import db_io
from capitalscan.jobs.config import config_hash, split_key_for
from capitalscan.jobs.ingest import IngestReport, run_job

MIN_BARS_FOR_INDICATORS = 280
INDICATOR_COLUMNS = [
    "bb_mid",
    "bb_upper",
    "bb_lower",
    "bb_pctb",
    "bb_width",
    "bb_width_pct",
    "k_fast",
    "d_fast",
    "k_full",
    "d_full",
    "k_cross_up",
    "k_cross_down",
    "sma_200",
    "sma200_slope_60",
    "atr_14",
    "rv_20d",
    "rv_pct_252d",
    "vol_z_20d",
    "dd_52w",
    # ADR 108. Boolean, unlike every column above it, and nullable through
    # the 273-bar warmup (invariant 4). Migration a9d3c04f7b15.
    "bear_close_above_upper",
]
DD_BUCKETS = (
    (0.10, "0-10"),
    (0.20, "10-20"),
    (0.35, "20-35"),
)


# ============ indicators ============


def _compute_one_ticker(
    ticker: str,
    read_start: date,
    read_end: date,
    write_start: date,
    write_end: date,
    params: IndicatorParams,
    database_url: str | None,
) -> tuple[str, pd.DataFrame | None]:
    """Runs in a worker process under `ProcessPoolExecutor(spawn)` — opens
    its own connection (CLAUDE.md platform note: connections aren't
    picklable) and is importable with no top-level side effects.
    """
    engine = db_io.get_engine(database_url, use_null_pool=True)
    with engine.connect() as conn:
        bars = pd.read_sql(
            text(
                "SELECT * FROM bars WHERE ticker = :ticker AND interval = '1d' "
                "AND ts BETWEEN :start AND :end ORDER BY ts"
            ),
            conn,
            params={"ticker": ticker, "start": read_start, "end": read_end},  # type: ignore[arg-type]
        )
    if len(bars) < MIN_BARS_FOR_INDICATORS:
        return ticker, None
    bars["ts"] = pd.to_datetime(bars["ts"]).dt.tz_localize(None)
    bars = bars.set_index("ts", drop=False)

    ind = core_indicators.compute_all(bars, params)
    # Read window != write window by design (DESIGN §4.5): the extra
    # history feeds warmup, but only the target range gets written.
    ind_dates = cast(pd.DatetimeIndex, ind.index).date
    ind = ind.loc[(ind_dates >= write_start) & (ind_dates <= write_end)]
    ind = ind.copy()
    ind["ticker"] = ticker
    return ticker, ind


def _merge_days_to_earnings(engine: Engine, indicators: pd.DataFrame) -> pd.DataFrame:
    """Nearest future `earnings.report_date` per ticker/bar, in calendar days."""
    tickers = sorted(indicators["ticker"].unique())
    with engine.connect() as conn:
        earnings = pd.read_sql(
            text("SELECT ticker, report_date FROM earnings WHERE ticker = ANY(:tickers)"),
            conn,
            params={"tickers": tickers},  # type: ignore[arg-type]
        )
    if earnings.empty:
        indicators["days_to_earnings"] = None
        return indicators

    earnings["report_date"] = pd.to_datetime(earnings["report_date"])
    out = []
    for ticker, group in indicators.groupby("ticker"):
        reports = earnings.loc[earnings["ticker"] == ticker, "report_date"].sort_values()
        group = group.copy()
        if reports.empty:
            group["days_to_earnings"] = None
        else:
            idx = pd.to_datetime(group["ts"])
            positions = reports.searchsorted(idx, side="left")
            positions = positions.clip(max=len(reports) - 1)
            next_report = reports.to_numpy()[positions]
            days = (next_report - idx.to_numpy()) / pd.Timedelta(days=1)
            group["days_to_earnings"] = pd.Series(days, index=group.index).where(idx <= next_report)
        out.append(group)
    return pd.concat(out, ignore_index=True)


def run_indicators(
    tickers: list[str],
    target_start: date,
    target_end: date,
    engine: Engine | None = None,
    params: IndicatorParams | None = None,
    max_workers: int = 1,
) -> IngestReport:
    """`indicators` job (DESIGN §4.5). `max_workers > 1` uses a spawn-mode
    `ProcessPoolExecutor`; each worker opens its own connection.
    """
    engine = engine or db_io.get_engine()
    params = params or IndicatorParams()
    read_start = target_start - timedelta(days=math.ceil(core_indicators.max_warmup() * 1.6))
    # `str(engine.url)` masks the password (renders "***"); workers need the
    # real credential to open their own connection.
    database_url = engine.url.render_as_string(hide_password=False)

    with run_job(
        engine,
        "indicators",
        {"tickers": tickers, "start": str(target_start), "end": str(target_end)},
    ) as report:
        args = [
            (ticker, read_start, target_end, target_start, target_end, params, database_url)
            for ticker in tickers
        ]
        if max_workers > 1:
            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                results = list(pool.map(_compute_one_ticker, *zip(*args)))
        else:
            results = [_compute_one_ticker(*a) for a in args]

        frames = [ind for _, ind in results if ind is not None]
        skipped = [ticker for ticker, ind in results if ind is None]

        if skipped:
            db_io.append(
                engine,
                "bar_rejects",
                [
                    {
                        "ticker": t,
                        "ts": None,
                        "rule": "insufficient_history",
                        "severity": "flag",
                        "payload": {"min_bars": MIN_BARS_FOR_INDICATORS},
                        "run_id": report.run_id,
                    }
                    for t in skipped
                ],
            )
            report.rows_flagged = len(skipped)

        if frames:
            merged = pd.concat(frames, ignore_index=False)
            merged["ts"] = merged.index
            merged = merged.reset_index(drop=True)
            merged = _merge_days_to_earnings(engine, merged)
            merged["interval"] = "1d"
            merged["run_id"] = report.run_id
            cols = ["ticker", "ts", "interval", *INDICATOR_COLUMNS, "days_to_earnings", "run_id"]
            report.rows_written = db_io.upsert(
                engine, "indicators", merged[cols], ["ticker", "ts", "interval"]
            )
        report.tickers = [ticker for ticker, ind in results if ind is not None]
    return report


# ============ universe ============


def _quarter_end(quarter: str) -> date:
    """`'2026Q3'` -> `2026-09-30`."""
    year, q = int(quarter[:4]), int(quarter[5])
    month = q * 3
    next_month_first = date(year, month, 1) + timedelta(days=32)
    return date(next_month_first.year, next_month_first.month, 1) - timedelta(days=1)


def _latest_indicator_row(
    engine: Engine, ticker: str, as_of: date, max_age_days: int
) -> pd.Series | None:
    """The newest indicator row on or before `as_of`, **and no older than
    one rebalance period** (`core.universe.evaluation_max_age_days`).

    The floor is the whole point. Without it this filtered `ts <= as_of`
    with no lower bound, so a ticker that stopped trading kept returning
    its final row forever and passed every criterion on frozen data: AET
    (acquired by CVS 2018-11-29) was `in_trade` at 2026-06-30 on November
    2018 numbers, 31 consecutive quarters with no bars behind any of them.

    Returning `None` is already "skip this ticker", so the caller needs no
    change — an unevaluable name simply gets no `universe` row for the
    quarter, which is what "we did not look" should have meant all along.

    The sibling defect is documented on `QuarterNotEnded`: an `as_of` in
    the future reads whatever the latest real data is and stamps it with a
    future label. Same query, opposite end, and the docstring there
    described that one without noticing this one.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT close_join.close, i.sma_200, i.sma200_slope_60 FROM indicators i "
                "JOIN bars close_join ON close_join.ticker = i.ticker AND close_join.ts = i.ts "
                "AND close_join.interval = '1d' "
                "WHERE i.ticker = :ticker AND i.interval = '1d' AND i.ts <= :as_of "
                "AND i.ts > :floor "
                "ORDER BY i.ts DESC LIMIT 1"
            ),
            {"ticker": ticker, "as_of": as_of, "floor": as_of - timedelta(days=max_age_days)},
        ).one_or_none()
    if row is None:
        return None
    return pd.Series(
        {"close": row.close, "sma_200": row.sma_200, "sma200_slope_60": row.sma200_slope_60}
    )


def _latest_shares(engine: Engine, ticker: str, as_of: date) -> tuple[float, date] | None:
    """Latest filing with `filed_on < as_of` (DESIGN §2.4) — the ~45-day
    10-Q lag is real and deliberate, not a bug to fix.

    This deliberately does not filter on `shares_outstanding.source`. Rows
    with `source = 'yahoo_shares_full'` (`jobs/ingest.py` `run_shares`,
    added when SEC's fact is missing or stale — see `SHARES_STALENESS_DAYS`
    there) are safe to compete on `filed_on` recency with `'sec_xbrl'` rows
    for the exact reason DESIGN §2.4 rejects holding current shares
    constant backward: each Yahoo row's `filed_on` is a real historical
    date `yfinance.Ticker.get_shares_full` attributes that share count to,
    not today's count re-stamped onto the past. A row dated 2016 describes
    2016 regardless of which fetcher produced it, so ordering by `filed_on`
    alone already prevents a current count from silently answering a
    historical `as_of` — the one thing this function must not do.

    Returns `(shares, filed_on)`. The filing date is not decoration: it is
    the left edge of the split window `_split_ratios_since` needs, because
    the count is stated on that date's share basis and every split after it
    has already moved the price series without moving this number.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT shares, filed_on FROM shares_outstanding WHERE ticker = :ticker "
                "AND filed_on < :as_of ORDER BY filed_on DESC LIMIT 1"
            ),
            {"ticker": ticker, "as_of": as_of},
        ).one_or_none()
    return (float(row.shares), row.filed_on) if row is not None else None


def _split_ratios_since(engine: Engine, ticker: str, filed_on: date) -> tuple[float, ...]:
    """Every split ratio with `ex_date > filed_on`, in any order.

    **Deliberately unbounded on the right.** Splits dated after the `as_of`
    being evaluated are included, because `bars.close` is adjusted to
    today's basis and has already absorbed them. See
    `core.universe.split_adjusted_shares` for why that is not look-ahead:
    market cap is split-invariant, so the factor cancels and only the
    agreement of the two bases matters.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ratio FROM corporate_actions WHERE ticker = :ticker "
                "AND action_type = 'split' AND ex_date > :filed_on AND ratio IS NOT NULL"
            ),
            {"ticker": ticker, "filed_on": filed_on},
        ).fetchall()
    return tuple(float(r.ratio) for r in rows)


def _rel_return_756d(
    engine: Engine,
    ticker: str,
    as_of: date,
    lookback_days: int,
    cache: dict[tuple, float | None] | None = None,
) -> float | None:
    """Trailing return over `lookback_days` bars ending on or before `as_of`.

    `cache`, when given, is an explicit call-scoped memo (perf task,
    controller-added — see `run_universe`'s docstring for why it must not
    outlive one call). The key is `(ticker, as_of, lookback_days)` — every
    argument this function's answer actually depends on. `None` (too little
    history) is a real, cacheable answer, so this checks membership
    (`key in cache`) rather than truthiness, and stores `None` explicitly
    rather than skipping the store.
    """
    if cache is not None:
        key = (ticker, as_of, lookback_days)
        if key in cache:
            return cache[key]

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ts, adj_close FROM bars WHERE ticker = :ticker AND interval = '1d' "
                "AND ts <= :as_of ORDER BY ts DESC LIMIT :n"
            ),
            {"ticker": ticker, "as_of": as_of, "n": lookback_days + 1},
        ).fetchall()
    if len(rows) < lookback_days + 1:
        result = None
    else:
        latest, oldest = float(rows[0].adj_close), float(rows[-1].adj_close)
        result = None if oldest == 0 else latest / oldest - 1.0

    if cache is not None:
        cache[key] = result
    return result


def _adv_20d(engine: Engine, ticker: str, as_of: date) -> float | None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT close, volume FROM bars WHERE ticker = :ticker AND interval = '1d' "
                "AND ts <= :as_of ORDER BY ts DESC LIMIT 20"
            ),
            {"ticker": ticker, "as_of": as_of},
        ).fetchall()
    if not rows:
        return None
    dollar_vol = [float(r.close) * float(r.volume or 0) for r in rows]
    return sum(dollar_vol) / len(dollar_vol)


def _revenue_growth_positive(
    engine: Engine, ticker: str, cik: str | None, as_of: date
) -> bool | None:
    """YoY revenue growth from XBRL, or None with fewer than four quarters.

    Best-effort v1 implementation (DESIGN §4.6): `sec.fetch_company_facts`
    currently pulls `EntityCommonStockSharesOutstanding` only, not a
    revenue tag, so this reads whatever the `shares_outstanding` ingest
    already landed and otherwise returns None — a documented simplification,
    not a silent guess. Extending the fetcher to pull `us-gaap:Revenues`
    is the natural follow-up once this job's shape is validated.

    Returning `None` here is honest and must stay that way — it is NOT the
    bug. The bug this stub used to cause (`in_trade` false for all 625
    tickers) was `is_tradeable` requiring all five criteria including this
    permanently-null one; that is fixed in `run_universe` by passing
    `UniverseParams.required_criteria`, which excludes `crit_rev_growth`
    until this function stops stubbing. See that field's comment.
    """
    return None


def _sector_median_return(
    engine: Engine,
    sector: str | None,
    as_of: date,
    lookback_days: int,
    cache: dict[tuple, Any] | None = None,
) -> tuple[float | None, bool]:
    """Median trailing return across tickers sharing `sector` at `as_of`.

    Returns `(median, used_fallback)`. Fewer than 5 members in the sector
    falls back to the universe-wide median, flagged (DESIGN §4.6).

    `sector is None` (currently every row: `tickers.sector` is unpopulated
    for all 711 rows, confirmed by SELECT) used to short-circuit to
    `(None, True)` here without ever computing the fallback median. DESIGN
    §4.6 only names one fallback path — "sector with too few members" — and
    an unknown sector is the most extreme case of that, not a third silent
    state. Folding it into the same `fallback` branch is what actually
    makes `crit_rel_return` resolve to a real True/False instead of always
    None; the "too few members" flag continues to mean the same thing it
    always did.

    `cache`, when given, is the same call-scoped memo `_rel_return_756d`
    takes (perf task, controller-added). This function is pure in
    `(sector, as_of, lookback_days)` within one `run_universe` call, so the
    `(median, used_fallback)` pair itself is memoized under
    `("sector_median", sector, as_of, lookback_days)` — a 4-tuple tagged
    with a literal, which can never collide with `_rel_return_756d`'s
    3-tuple `(ticker, as_of, lookback_days)` keys even if a ticker symbol
    happened to equal the tag string. The same `cache` is threaded into
    every `_rel_return_756d(..., cache)` call below so a peer's return
    computed here is reused by `run_universe`'s own per-ticker call, and by
    every other ticker's identical fallback-branch median — this is what
    collapses the O(n^2) query pattern (DESIGN §4.6's fallback iterates all
    active tickers, once per caller, unmemoized).
    """
    if cache is not None:
        median_key = ("sector_median", sector, as_of, lookback_days)
        if median_key in cache:
            return cast("tuple[float | None, bool]", cache[median_key])

    tickers: list[str] = []
    if sector is not None:
        with engine.connect() as conn:
            peers = conn.execute(
                text("SELECT ticker FROM tickers WHERE sector = :sector AND is_active"),
                {"sector": sector},
            ).fetchall()
        tickers = [r.ticker for r in peers]
    fallback = sector is None or len(tickers) < 5
    if fallback:
        with engine.connect() as conn:
            tickers = [
                r.ticker for r in conn.execute(text("SELECT ticker FROM tickers WHERE is_active"))
            ]

    def _peer_return(t: str) -> float | None:
        # Only pass `cache` through when it was actually given, so a caller
        # (or test) that monkeypatches `_rel_return_756d` with the old
        # 4-argument shape keeps working unmodified.
        if cache is not None:
            return _rel_return_756d(engine, t, as_of, lookback_days, cache)
        return _rel_return_756d(engine, t, as_of, lookback_days)

    returns = [r for t in tickers if (r := _peer_return(t)) is not None]
    result: tuple[float | None, bool]
    if not returns:
        result = (None, fallback)
    else:
        returns.sort()
        mid = len(returns) // 2
        median = returns[mid] if len(returns) % 2 else (returns[mid - 1] + returns[mid]) / 2
        result = (median, fallback)

    if cache is not None:
        cache[median_key] = result
    return result


class FutureQuarterError(ValueError):
    """Raised when `run_universe` is asked to evaluate a quarter that has
    not ended yet (DEFECT 3 fix).

    `_quarter_end('2026Q3')` returns 2026-09-30 regardless of what day it
    actually is. Every helper this job calls (`_latest_indicator_row`,
    `_latest_shares`, `_rel_return_756d`, ...) filters on `ts <= as_of` /
    `filed_on < as_of`, so an `as_of` in the future does not read fabricated
    future data — it silently reads whatever the *latest real* data happens
    to be and stamps today's numbers with a September label. That
    mislabeled row then makes `_in_trade` (compute.py) inert: every current
    `signal_date` is before the row's `as_of`, so the "on or before" match
    fails, `_in_trade` hits its zero-rows branch and fails OPEN (True) for
    every ticker — no error, no log line — until the calendar catches up to
    the label, at which point the same row starts failing every event
    closed, also with no error and no log line. A quarter-end `as_of` is
    only a safe cache key for "the last evaluation available" if the
    quarter has actually finished; refusing early converts a silent,
    calendar-triggered flip into an immediate, loud one at job-submission
    time, which is the whole point of ADR 014's "evaluated only on
    information available at t-1" — a quarter can't be evaluated on data
    from inside itself.
    """


def _evaluate_universe_row(
    ticker: str,
    as_of: date,
    ind_row: pd.Series,
    mcap: float | None,
    rel_return: float | None,
    sector_median: float | None,
    rev_growth: bool | None,
    adv_20d: float | None,
    up: UniverseParams,
) -> dict:
    """Pure(ish) assembly of one `universe` row from already-fetched inputs.

    Split out of `run_universe` so the ADR 014 criteria wiring — in
    particular DEFECT 1's `required` subset — is unit-testable without a
    database (`tests/unit/test_universe_membership.py`).
    """
    ind_row = ind_row.copy()
    ind_row["rel_return_756d"] = rel_return
    criteria = core_universe.evaluate_criteria(ind_row, mcap, sector_median, rev_growth, up)
    # DEFECT 1: `required` is deliberately a subset (UniverseParams.
    # required_criteria), not all five — see that field's comment. Passing
    # `None` here (i.e. "require all five") is what silently zeroed
    # `in_trade` for every ticker, since `crit_rev_growth` is a permanent
    # stub with no ingested revenue data behind it.
    in_trade = core_universe.is_tradeable(criteria, required=set(up.required_criteria))

    return {
        "ticker": ticker,
        "as_of": as_of,
        # v1 simplification: any ticker on file counts as in the
        # wide training universe. Point-in-time reconstruction
        # from `data/universe_union.csv`'s add/remove dates is
        # deferred — see the module docstring's scope note.
        "in_train": True,
        "in_trade": in_trade,
        "mcap_usd": mcap,
        "adv_20d_usd": adv_20d,
        **criteria,
    }


def run_universe(
    quarter: str,
    tickers: list[str] | None = None,
    engine: Engine | None = None,
    up: UniverseParams | None = None,
    today: date | None = None,
) -> IngestReport:
    """`universe` job (DESIGN §4.6). `quarter` like `'2026Q3'`.

    `today` is the reference wall-clock date for the future-quarter guard
    (DEFECT 3) — injectable so tests do not depend on the real calendar;
    defaults to `date.today()` for real runs (jobs own clock access,
    invariant 1's IO carve-out — same pattern as `run_shares`'s `as_of`).
    """
    engine = engine or db_io.get_engine()
    up = up or UniverseParams()
    today = today or date.today()
    as_of = _quarter_end(quarter)
    if as_of > today:
        raise FutureQuarterError(
            f"cannot evaluate {quarter} (ends {as_of}): that quarter has not ended yet "
            f"(today is {today}). ADR 014's filter is causal — ask again after the "
            "quarter closes, or evaluate the most recently completed quarter instead."
        )

    with run_job(engine, "universe", {"quarter": quarter}) as report:
        if tickers is None:
            with engine.connect() as conn:
                tickers = [
                    r[0] for r in conn.execute(text("SELECT ticker FROM tickers WHERE is_active"))
                ]

        # Call-scoped memo for `_rel_return_756d` / `_sector_median_return`.
        # Both are pure in inputs that are constant across this loop
        # (`as_of`, `up.rel_return_lookback_days`) or repeat heavily across
        # it (`tickers.sector` is null for every row today, so every ticker
        # takes the identical "median over all active tickers" fallback).
        # A plain dict created here and threaded through by parameter -- not
        # a module-level or `functools.lru_cache` -- so it lives exactly as
        # long as this one call: it goes out of scope when `run_universe`
        # returns, so a second call (a different quarter, a different
        # `as_of`) starts with an empty cache and can never serve another
        # quarter's stale answer, and a long-lived process (the scheduler)
        # never accumulates entries across runs.
        rel_return_cache: dict[tuple, Any] = {}

        # One rebalance period, derived rather than declared: an evaluation
        # must rest on data from inside the period it describes.
        max_age_days = core_universe.evaluation_max_age_days(up.rebalance_freq)

        rows = []
        for ticker in tickers:
            ind_row = _latest_indicator_row(engine, ticker, as_of, max_age_days)
            if ind_row is None:
                continue
            with engine.connect() as conn:
                tk = conn.execute(
                    text("SELECT cik, sector FROM tickers WHERE ticker = :ticker"),
                    {"ticker": ticker},
                ).one_or_none()
            sector = tk.sector if tk else None
            cik = tk.cik if tk else None

            # ADR filings report ordinary shares while `close` is per ADR;
            # `adr_adjusted_shares` reconciles the two before pricing. A
            # non-ADR passes through untouched.
            # Two corrections, both restating the filed count onto the
            # basis `close` is already on. ADR first (ordinary -> ADR), then
            # splits (filing basis -> today's basis); they commute, and the
            # order is for reading rather than arithmetic.
            filing = _latest_shares(engine, ticker, as_of)
            shares = core_universe.adr_adjusted_shares(
                ticker, filing[0] if filing is not None else None, up
            )
            if shares is not None and filing is not None:
                shares = core_universe.split_adjusted_shares(
                    shares, _split_ratios_since(engine, ticker, filing[1])
                )
            mcap = shares * float(ind_row["close"]) if shares is not None else None
            rel_return = _rel_return_756d(
                engine, ticker, as_of, up.rel_return_lookback_days, rel_return_cache
            )
            sector_median, _ = _sector_median_return(
                engine, sector, as_of, up.rel_return_lookback_days, rel_return_cache
            )
            rev_growth = _revenue_growth_positive(engine, ticker, cik, as_of)
            adv_20d = _adv_20d(engine, ticker, as_of)

            rows.append(
                _evaluate_universe_row(
                    ticker, as_of, ind_row, mcap, rel_return, sector_median, rev_growth, adv_20d, up
                )
            )

        report.rows_written = db_io.upsert(engine, "universe", rows, ["ticker", "as_of"])
        report.tickers = [str(r["ticker"]) for r in rows]
    return report


# ============ events ============


def _dd_bucket(dd_52w: float | None) -> str | None:
    if dd_52w is None or (isinstance(dd_52w, float) and math.isnan(dd_52w)):
        return None
    for threshold, label in DD_BUCKETS:
        if dd_52w <= threshold:
            return label
    return "35+"


def _split_key(signal_date: date, sp: SplitParams) -> str:
    """Assigned at event creation, never at query time (invariant 5).

    Thin delegate to `jobs.config.split_key_for` — the one implementation
    shared with `research/backtest.py` (Ruling C2, session 9 task 2). Kept
    as a wrapper, rather than inlined at the call site, so existing unit
    tests importing `_split_key` from this module keep working unchanged.
    """
    return split_key_for(signal_date, sp)


def _deterministic_id(*parts: object) -> int:
    """A stable bigint from `parts` — reruns of `run_events` must produce
    the same `cluster_id` for the same (ticker, side, cluster head), or a
    downstream join across two runs would silently drift.
    """
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:15], 16)  # 60 bits, comfortably inside bigint


def _tag_clusters(events: list[dict], gap_days: int) -> list[dict]:
    """Overlapping events on one (ticker, side) share a cluster (DESIGN §5.3).

    A new cluster starts when the gap since that pair's last signal exceeds
    `gap_days` (the exit engine's max hold, ExitParams.max_hold_days by
    default) — the natural "still in a position" window. DESIGN pins the
    concept but not this exact threshold; documented here as the judgment
    call it is.
    """
    events = sorted(events, key=lambda e: (e["ticker"], e["side"], e["signal_date"]))
    state: dict[tuple, dict] = {}
    for event in events:
        key = (event["ticker"], event["side"])
        prev = state.get(key)
        if prev is None or (event["signal_date"] - prev["last_date"]).days > gap_days:
            head_date = event["signal_date"]
            seq = 1
        else:
            head_date = prev["head_date"]
            seq = prev["seq"] + 1
        cluster_id = _deterministic_id(event["ticker"], event["side"], head_date)
        event["cluster_id"] = cluster_id
        event["seq_in_cluster"] = seq
        event["is_cluster_head"] = seq == 1
        event["days_since_head"] = (event["signal_date"] - head_date).days
        state[key] = {"last_date": event["signal_date"], "head_date": head_date, "seq": seq}
    return events


def _read_bars_range(engine: Engine, tickers: list[str], start: date, end: date) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT * FROM bars WHERE ticker = ANY(:tickers) AND interval = '1d' "
                "AND ts BETWEEN :start AND :end ORDER BY ticker, ts"
            ),
            conn,
            params={"tickers": tickers, "start": start, "end": end},  # type: ignore[arg-type]
        )
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
    return df


def _read_indicators_range(
    engine: Engine, tickers: list[str], start: date, end: date
) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT * FROM indicators WHERE ticker = ANY(:tickers) AND interval = '1d' "
                "AND ts BETWEEN :start AND :end ORDER BY ticker, ts"
            ),
            conn,
            params={"tickers": tickers, "start": start, "end": end},  # type: ignore[arg-type]
        )
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
    return df


def _read_market_days(engine: Engine, start: date, end: date) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text("SELECT * FROM market_days WHERE ts BETWEEN :start AND :end"),
            conn,
            params={"start": start, "end": end},
        )


def _read_universe_flags(engine: Engine, tickers: list[str]) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text("SELECT ticker, as_of, in_trade FROM universe WHERE ticker = ANY(:tickers)"),
            conn,
            params={"tickers": tickers},  # type: ignore[arg-type]
        )


# `events` column ownership for `db_io.upsert`'s `update_columns` (Ruling
# C4). `run_events` (this module) and `run_backtest` (research/backtest.py,
# Task 9b) both write onto the same `(config_hash, ticker, signal_date,
# signal_type, entry_kind)` row. Each names only the columns it computes so
# a re-run of one job never nulls the other's columns by omission.
#
# Cluster columns are owned exclusively by the backtest (Ruling C5):
# `research.candidates.tag_clusters` counts trading bars while
# `_tag_clusters` below counts calendar days, and the two disagree on
# cluster boundaries. `_tag_clusters` still populates these on every row
# dict here so a brand-new event (INSERT, not UPDATE) is never missing
# them, but they are deliberately absent from `_RUN_EVENTS_UPDATE_COLUMNS`
# below — once the backtest has written a row, a plain `run_events` re-run
# must leave its cluster columns alone.
_CLUSTER_COLUMNS = ["cluster_id", "seq_in_cluster", "is_cluster_head", "days_since_head"]

# The signal-detection columns `_build_event_row` computes, i.e. everything
# `run_events` actually knows how to fill in. `run_id` is included even
# though `run_backtest` also writes it: Ruling C4 keeps `run_id` writable by
# both jobs, since each row should record whichever run last touched it.
_RUN_EVENTS_UPDATE_COLUMNS = [
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
    "vix_close",
    "spx_ret_1d",
    "dd_bucket",
    "entry_date",
    "entry_price",
    "entry_gapped",
    "split_key",
    # ADR 122. Updatable because `cscan universe` re-evaluates membership
    # every quarter and can revise a past `as_of`; a row that kept the
    # answer from the first time it was detected would disagree with the
    # table the answer came from.
    "in_trade",
]


def _build_event_row(
    hit: Any,
    bar: pd.Series,
    ind_row: pd.Series,
    market_row: pd.Series | None,
    chash: str,
    run_id: str,
    splits: SplitParams | None = None,
    in_trade: bool = True,
) -> dict:
    bound = Bound.LOWER if hit.side.value == "long" else Bound.UPPER
    entry_gapped = None
    entry_price = None
    if hit.touch_level is not None:
        entry_price = entry_price_for(EntryKind.TOUCH, bar, None, hit.touch_level, hit.side)
        entry_gapped = core_signals._breach(float(bar["open"]), hit.touch_level, bound)

    return {
        "run_id": run_id,
        "config_hash": chash,
        "ticker": hit.ticker,
        "signal_date": hit.ts,
        "signal_type": hit.signal_type.value,
        "signal_types_all": [t.value for t in hit.signal_types_all],
        "signal_strength": hit.signal_strength,
        "side": hit.side.value,
        # ADR 122. Point-in-time: membership as of *this bar*, not today's.
        # A name that left the trade universe in 2019 has true on its 2015
        # events and false on its 2021 ones, which is the only reading that
        # lets a statistics population be reconstructed from the table.
        "in_trade": in_trade,
        "touch_level": hit.touch_level,
        "bb_pctb": hit.pctb,
        "bb_width_pct": ind_row.get("bb_width_pct"),
        "k_full": hit.k_full,
        "d_full": ind_row.get("d_full"),
        "k_fast": ind_row.get("k_fast"),
        "k_cross_up": ind_row.get("k_cross_up"),
        "k_cross_down": ind_row.get("k_cross_down"),
        "atr_14": ind_row.get("atr_14"),
        "rv_pct_252d": ind_row.get("rv_pct_252d"),
        "dd_52w": ind_row.get("dd_52w"),
        "sma200_slope_60": ind_row.get("sma200_slope_60"),
        "above_sma200": None
        if pd.isna(ind_row.get("sma_200")) or pd.isna(bar.get("close"))
        else bool(float(bar["close"]) > float(ind_row["sma_200"])),
        "vol_z_20d": ind_row.get("vol_z_20d"),
        "days_to_earnings": ind_row.get("days_to_earnings"),
        "vix_close": None if market_row is None else market_row.get("vix_close"),
        "spx_ret_1d": None if market_row is None else market_row.get("spx_ret_1d"),
        "dd_bucket": _dd_bucket(ind_row.get("dd_52w")),
        "entry_kind": EntryKind.TOUCH.value,
        "entry_date": hit.ts if entry_price is not None else None,
        "entry_price": entry_price,
        "entry_gapped": entry_gapped,
        "split_key": _split_key(hit.ts, splits or SplitParams()),
    }


def run_events(
    tickers: list[str],
    target_start: date,
    target_end: date,
    engine: Engine | None = None,
    sp: SignalParams | None = None,
    config: Config | None = None,
) -> IngestReport:
    """`events` job (DESIGN §4.7). See module docstring for v1 scope.

    **Final-review Finding 1 fix.** Before this, `config_hash` was computed
    from `Config(signals=sp)` (every other section silently defaulted) and
    `split_key` from a hardcoded `SplitParams()` (ignoring `config.splits`
    entirely) — so the moment a caller resolved a `Config` with any
    non-`signals` override, this job's `config_hash` diverged from
    `research.backtest.run_backtest`'s, which correctly uses the full
    resolved `Config`. Since `config_hash` is part of the natural key
    `(config_hash, ticker, signal_date, signal_type, entry_kind)`, a
    divergent hash makes `run_backtest`'s upsert miss this job's row
    entirely and INSERT a disconnected duplicate instead of enriching it.

    `config` takes the full resolved `Config` — the same shape
    `run_backtest` takes — rather than a narrower `SplitParams`-only fix,
    specifically so `config_hash` and `split_key` are guaranteed to derive
    from the identical object `run_backtest` uses. A `SplitParams`-only
    parameter would have repaired the split-key drift but left the hash
    divergence (the join-breaking half of the bug) untouched.

    `sp` is kept for backward compatibility (existing callers, existing
    tests): passing only `sp` behaves exactly as before, wrapping it in a
    `Config(signals=sp)` with every other section defaulted. Passing both
    `sp` and `config` is only accepted when they agree — `config.signals ==
    sp` — since disagreeing values would leave it ambiguous which one this
    job's caller actually meant to run.
    """
    engine = engine or db_io.get_engine()
    if config is not None:
        if sp is not None and sp != config.signals:
            raise ValueError(
                "run_events: `sp` and `config.signals` disagree "
                f"({sp!r} vs {config.signals!r}); pass one, not conflicting "
                "values for both"
            )
        resolved_config = config
    else:
        resolved_config = Config(signals=sp or SignalParams())
    sp = resolved_config.signals
    chash = config_hash(resolved_config)

    with run_job(
        engine, "events", {"tickers": tickers, "start": str(target_start), "end": str(target_end)}
    ) as report:
        # Extra lookback so the earliest bar in range still has a t-1
        # indicator row inside the frame.
        ind_start = target_start - timedelta(days=10)
        bars = _read_bars_range(engine, tickers, target_start, target_end)
        indicators = _read_indicators_range(engine, tickers, ind_start, target_end)
        market = _read_market_days(engine, target_start, target_end).set_index("ts")
        universe_flags = _read_universe_flags(engine, tickers)

        # Debounce on `debounce_key(hit)` — the explicit (ticker, signal_date,
        # bound) tuple, never on the `SignalHit` dataclass itself (DESIGN
        # §4.7): a NaN field hashes stably but compares unequal, so a set
        # keyed on the dataclass would silently keep duplicates.
        seen: set[tuple] = set()
        deduped: list[dict] = []
        skipped_null = 0
        for ticker in tickers:
            ind_group = indicators.loc[indicators["ticker"] == ticker].sort_values("ts")
            if ind_group.empty:
                continue
            ind_group = ind_group.set_index(ind_group["ts"].dt.date)
            bar_group = bars.loc[bars["ticker"] == ticker].sort_values("ts")
            # `detect()` reads the bar's date from `bar.name` when set
            # (core.signals._bar_date), falling back to the `ts` column only
            # when `bar.name is None`. Without this, `bar.name` is the
            # DataFrame's integer position and `detect()` would silently
            # misread every signal_date as the epoch.
            bar_group = bar_group.set_index(bar_group["ts"].dt.date, drop=False)

            for _, bar in bar_group.iterrows():
                bar_date = bar["ts"].date()
                prior_dates = ind_group.index[ind_group.index < bar_date]
                if len(prior_dates) == 0:
                    continue
                prior_ind = ind_group.loc[prior_dates.max()]

                # Step 2: skip if any required field is null (DESIGN §4.7).
                if any(pd.isna(prior_ind.get(f)) for f in ("bb_lower", "bb_upper", "k_full")):
                    skipped_null += 1
                    continue
                # Step 3 (ADR 122): **record** trade-universe membership,
                # do not filter on it.
                #
                # This was a `continue`, and it meant a train-universe name
                # had bars, indicators, real band touches, and no events at
                # all — SMCI: 192 band touches since 2024, zero events,
                # never once in the trade universe across 66 snapshots. The
                # ticker page could not show it, and the absence read as a
                # broken job rather than as a policy.
                #
                # A signal that fired is a fact about the ticker. Whether
                # you would have traded it is a different fact, and storing
                # the second lets each consumer decide, rather than one
                # decision being frozen into what the table contains.
                #
                # **Every consumer that must not widen now says so.**
                # `scan()` most of all: its docstring named this skip as the
                # reason it accepted a `universe` argument without filtering
                # on it. `test_events_in_trade_filter.py` fails if a
                # production read of `events` stops carrying the predicate.
                bar_in_trade = core_universe.in_trade(universe_flags, ticker, bar_date)

                # ADR 108: attach bar t's close-confirmed flags. Mirrors
                # `research.candidates.scan_candidates` exactly, and shares
                # its allowlist constant rather than restating the field —
                # the two detection callers drifting apart on which fields
                # cross from row t is precisely the failure this guards.
                own_ind = ind_group.loc[bar_date] if bar_date in ind_group.index else None
                for field in CLOSE_CONFIRMED_FIELDS:
                    value = None if own_ind is None else own_ind.get(field)
                    bar[field] = False if value is None or pd.isna(value) else bool(value)

                for hit in core_signals.detect(bar, prior_ind, sp):
                    key = debounce_key(hit)
                    if key in seen:
                        continue
                    seen.add(key)
                    market_row = market.loc[bar_date] if bar_date in market.index else None
                    deduped.append(
                        _build_event_row(
                            hit,
                            bar,
                            prior_ind,
                            market_row,
                            chash,
                            report.run_id,
                            resolved_config.splits,
                            in_trade=bar_in_trade,
                        )
                    )

        clustered = _tag_clusters(deduped, ExitParams().max_hold_days)

        if clustered:
            report.rows_written = db_io.upsert(
                engine,
                "events",
                clustered,
                ["config_hash", "ticker", "signal_date", "signal_type", "entry_kind"],
                update_columns=_RUN_EVENTS_UPDATE_COLUMNS,
            )
        report.rows_flagged = skipped_null
        report.tickers = sorted({row["ticker"] for row in clustered})
    return report


# ============ scan() ============

_SCAN_COLUMNS = [
    "ticker",
    "signal_date",
    "signal_type",
    "signal_types_all",
    "signal_strength",
    "side",
    "bb_upper",
    # The same-day band, alongside the t-1 band above rather than replacing
    # it (2026-08-15, user's decision). ADR 109 made
    # `bear_close_above_upper` the one condition read from bar t's own band,
    # while every other column here stays at t-1 per invariant 3. A reader
    # checking a fired row against a chart was comparing the close to the
    # *previous* day's band and seeing `bb_pctb < 1` next to a firing row —
    # ANET, HPE, and PH all showed this on the 2026-08-14 scan.
    #
    # An extra column rather than a conditional swap: swapping would make
    # one column mean t-1 on six signal types and t on the seventh, which is
    # worse to read than two columns that each mean one thing.
    "bb_upper_sameday",
    "bb_mid",
    "bb_lower",
    "bb_pctb",
    "k_full",
    "k_fast",
    "k_cross_up",
    "k_cross_down",
    "dd_bucket",
    "trend_state",
]


def scan(
    tickers: list[str] | None = None,
    signal_types: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    universe: str = "trade",
    engine: Engine | None = None,
) -> pd.DataFrame:
    """ADR 049's v1 acceptance surface: one row per event, signal columns only.

    No statistics, no predictions, no exits — those columns exist on
    `events` for Phase 3 but are never in `scan()`'s output (see this
    module's docstring for why the row may still be null there today).

    **`universe` is still accepted and still not branched on, but the
    reason changed (ADR 122).** It used to be that `events` could not hold
    an out-of-trade row, because `run_events` skipped the bar. It now can:
    detection records membership rather than filtering on it, so the
    ticker page can show a train-universe name's firings. This function
    therefore carries an explicit `e.in_trade` predicate, and `cscan scan`
    and the poller list exactly the names they listed before.

    Widening this to the train universe is a one-line change and is
    deliberately not made: `scan` is the actionable surface, and ADR 001's
    two-universe split exists so that what you look at is narrower than
    what you measure on.
    """
    engine = engine or db_io.get_engine()

    # DEFECT (Session 9): scan() selected neither config_hash nor entry_kind,
    # so one signal returned once per (config_hash, entry_kind) combination
    # -- 148 rows for 27 real signals on 2026-07-31, growing without bound as
    # the sweep and the backtest add more of each. `events`' grain is
    # (config_hash, ticker, signal_date, signal_type, entry_kind)
    # (CONSTRAINTS.md); ADR 049 wants one row per event, so both dimensions
    # must be pinned to a single value here.
    #
    # config_hash: the same GUC v_events already reads (invariant 5b -- no
    # second config-selection mechanism). `current_setting(..., true)`
    # returns NULL rather than raising when unset; resolving it with a
    # separate SELECT (instead of embedding current_setting() in the main
    # query) makes "unset" an explicit branch instead of a silent
    # `config_hash = NULL` clause that happens to match zero rows. Returning
    # everything (the current bug) is wrong, and guessing a config is worse
    # than admitting there is nothing to show, so unset returns empty --
    # the same outcome `v_events` already produces in that state.
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT current_setting('capitalscan.default_config_hash', true)")
        ).fetchone()
    default_chash = row[0] if row else None
    if not default_chash:
        return pd.DataFrame(columns=_SCAN_COLUMNS)

    clauses = ["1=1"]
    params: dict[str, Any] = {"config_hash": default_chash, "entry_kind": EntryKind.TOUCH.value}
    clauses.append("e.config_hash = :config_hash")
    # entry_kind: filter to one kind rather than SELECT DISTINCT on the
    # projected columns. Every entry_kind row for one event shares identical
    # signal-level columns today (that is *why* they were byte-identical
    # duplicates), but DISTINCT silently stops deduplicating the moment a
    # future column varies by entry_kind -- filtering to one kind stays
    # correct regardless. 'touch' is the row `run_events` itself writes at
    # detection time (jobs/compute.py `_build_event_row`), before any
    # backtest-added entry_kind row exists, and it's the same kind
    # `jobs/poll.py` already treats as canonical for existing-event lookups.
    clauses.append("e.entry_kind = :entry_kind")
    # ADR 122. **This predicate replaces a guarantee that used to come from
    # `run_events`.** Until ADR 122 the detector skipped every out-of-trade
    # bar, so `events` could not contain one and this function's docstring
    # said so in as many words while accepting a `universe` argument it
    # never used. Detection now records membership instead of filtering on
    # it, and `cscan scan` would have started listing names outside the
    # trade universe on the first run after — quietly, with no error and no
    # visible change other than more rows.
    clauses.append("e.in_trade")
    if tickers:
        clauses.append("e.ticker = ANY(:tickers)")
        params["tickers"] = tickers
    if signal_types:
        clauses.append("e.signal_type = ANY(:signal_types)")
        params["signal_types"] = signal_types
    if start:
        clauses.append("e.signal_date >= :start")
        params["start"] = start
    if end:
        clauses.append("e.signal_date <= :end")
        params["end"] = end

    # DEFECT 4 fix: the signal fired off the indicator row at t-1 (invariant
    # 3; `run_events` reads `prior_ind = ind_group.loc[prior_dates.max()]`,
    # the latest indicator strictly before the bar date). Joining on
    # `e.signal_date = i.ts` instead pulled the *same-day* (t) bands —
    # confirmed against real data: TSM's 2026-07-30 event was compared
    # against the 2026-07-29 bands (bb_upper 456.523644 / bb_mid 418.677000
    # / bb_lower 380.830356), but the old join displayed 2026-07-30's own
    # bands (453.131161 / 416.631000 / 380.130839) — internally consistent
    # looking, silently wrong. A LATERAL join re-derives "latest ts strictly
    # before signal_date" per row, which is the same rule `run_events`
    # already applied when it computed `bb_pctb`/`k_full` onto the event —
    # this display join must agree with it, not re-decide it.
    query = f"""
        SELECT e.ticker, e.signal_date, e.signal_type, e.signal_types_all, e.signal_strength,
               e.side, i.bb_upper, isd.bb_upper AS bb_upper_sameday,
               i.bb_mid, i.bb_lower, e.bb_pctb, e.k_full, e.k_fast,
               e.k_cross_up, e.k_cross_down, e.dd_bucket, e.above_sma200
        FROM events e
        LEFT JOIN LATERAL (
            SELECT bb_upper, bb_mid, bb_lower
            FROM indicators i2
            WHERE i2.ticker = e.ticker AND i2.interval = '1d' AND i2.ts < e.signal_date
            ORDER BY i2.ts DESC
            LIMIT 1
        ) i ON true
        -- ADR 109's band: bar t's own, the one `bear_close_above_upper`
        -- is actually evaluated against. Equality on `ts`, not the
        -- `ts < signal_date` ordering above, so this cannot silently
        -- degrade into a second copy of the t-1 column. LEFT JOIN because
        -- an event dated ahead of its own indicator row (a live poll event
        -- before the EOD indicators job) must render as empty rather than
        -- drop the row.
        LEFT JOIN indicators isd
            ON isd.ticker = e.ticker AND isd.interval = '1d' AND isd.ts = e.signal_date
        WHERE {" AND ".join(clauses)}
        ORDER BY e.ticker, e.signal_date
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=_SCAN_COLUMNS)

    df["trend_state"] = df["above_sma200"].map({True: "above_sma200", False: "below_sma200"})
    return df[_SCAN_COLUMNS]
