"""Yahoo Finance fetcher: daily bars, hourly bars, actions, quotes, chains.

DESIGN §4.3 (`bars_daily`) and §4.4 (`bars_hourly`) pin the exact call
shape. This module returns tidy frames only — validation (DESIGN §2.3)
and upserts belong to the `jobs/ingest` layer (BUILD session 5), not here.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import cast

import pandas as pd
import yfinance as yf
from yfinance.data import YfData

from capitalscan.jobs.fetch.base import cached, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 0.5
# Symbols per `yf.download` call, for both the daily and hourly batch paths.
# **Raised 50 -> 100 on 2026-08-26.** The universe is ~1,470 and heading for
# ~2,000, and at 0.5 req/s each halving of the batch count is a halving of
# the wall clock: 30 requests becomes 15, ~60s becomes ~30s per window.
#
# Unlike `QUOTE_BATCH_SIZE` this is not a URL-length constraint --
# `yf.download` POSTs the symbol list rather than putting it in a query
# string, so the proxy failure mode that caps quotes at 100 does not apply
# here. 100 is a deliberately conservative step rather than a measured
# ceiling; raise it again only with a measurement.
#
# **Changing this changes every cache key.** `_batch_key` and
# `_hourly_batch_key` are built from the ticker list, so a different
# batching produces different keys. That is correct rather than dangerous:
# new keys miss and re-fetch, and no existing entry is ever read as an
# answer to a question it was not asked. The old entries become garbage and
# can be deleted.
DAILY_BATCH_SIZE = 100
HOURLY_WINDOW_DAYS = 60
# Symbols per `/v7/finance/quote` request. Yahoo accepts more, but the URL
# is a GET query string and oversized batches start failing at the proxy
# rather than returning a parseable error, so this stays well inside it.
QUOTE_BATCH_SIZE = 100
# How old a quote may be and still count as live. Yahoo's free feed is
# documented as delayed up to 15 minutes on some venues, so anything
# inside that is a legitimately-delayed live quote; anything beyond it is
# a stale feed reporting its last known price, which `fetch_quotes` must
# not hand to the poller as current (see that function's docstring).
QUOTE_MAX_AGE_SECONDS = 900

_QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"

_BARS_COLUMNS = [
    "ticker",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "adj_factor",
]
_HOURLY_COLUMNS = ["ticker", "ts", "open", "high", "low", "close", "volume"]
_ACTIONS_COLUMNS = ["ticker", "ts", "action_type", "value"]
# `day_open` is ADR 108's live half: the poller compares the current price
# against today's open, which no other consumer needs. It is nullable — a
# quote without `regularMarketOpen` yields NaN rather than dropping the
# quote, because the band signals do not need it and dropping would blind
# the poller to an ordinary breach over a missing field it never uses.
# ADR 128 adds the three session aggregates Yahoo was already sending and
# this parser was discarding. `regularMarketDayHigh`/`Low` are cumulative
# extremes for the session, not the last interval, so a poller that missed
# an hour still gets the correct high on its next tick -- there is nothing
# to accumulate and no gap to reconstruct.
_QUOTE_COLUMNS = [
    "ticker",
    "ts",
    "price",
    "day_open",
    "day_high",
    "day_low",
    "day_volume",
]


def _empty_bars_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_BARS_COLUMNS)


def _empty_hourly_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_HOURLY_COLUMNS)


def _split_batches(tickers: list[str], size: int = DAILY_BATCH_SIZE) -> list[list[str]]:
    return [tickers[i : i + size] for i in range(0, len(tickers), size)]


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_daily(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    # threads=False: yfinance's internal threading trips rate limiting
    # faster than sequential batching, and the batch is already the
    # parallelism (DESIGN §4.3). auto_adjust=False is mandatory — the
    # default overwrites OHLC with adjusted values and destroys the
    # split-adjusted series band levels are computed from.
    #
    # `end + 1 day` because **yfinance's `end` is exclusive** and every
    # other date range in this codebase is inclusive on both ends
    # (`run_events`, `run_indicators`, `scan`, `split_key_for`). This
    # function was the single place that disagreed, and the disagreement
    # was silent.
    #
    # Found 2026-08-17. `cscan nightly` runs at 16:30 local, after the
    # close, and sets `end = date.today()` — so it requested bars through
    # *yesterday* and never ingested the session it had just run after.
    # After a clean ten-phase nightly that evening, `max(bars.ts)`,
    # `max(indicators.ts)` and `max(events.signal_date)` were all
    # 2026-08-14 with zero rows for 2026-08-17, a full trading day.
    #
    # Nothing failed. Every phase reported `ok`, `bar_rejects` was empty,
    # and `cscan scan --date <today>` printed "no events found" — which
    # reads as *nothing fired today* rather than *today was never
    # fetched*. Fixed here rather than by adding `+ 1` at each call site,
    # so no future caller has to know about the asymmetry.
    return cast(
        pd.DataFrame,
        yf.download(
            tickers,
            start=start,
            end=end + timedelta(days=1),
            auto_adjust=False,
            actions=True,
            group_by="ticker",
            threads=False,
            progress=False,
        ),
    )


def _extract_ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return pd.DataFrame()
        return cast(pd.DataFrame, raw[ticker])
    return raw


def _has_no_data(sub: pd.DataFrame) -> bool:
    return bool(sub.empty or "Close" not in sub.columns or sub["Close"].isna().all())


def _tidy_daily(sub: pd.DataFrame, ticker: str) -> pd.DataFrame:
    sub = sub.dropna(subset=["Close"])
    index = cast(pd.DatetimeIndex, sub.index)
    idx = index.tz_localize(None) if index.tz is not None else index
    out = pd.DataFrame(
        {
            "ticker": ticker,
            "ts": idx,
            "open": sub["Open"].to_numpy(),
            "high": sub["High"].to_numpy(),
            "low": sub["Low"].to_numpy(),
            "close": sub["Close"].to_numpy(),
            "adj_close": sub["Adj Close"].to_numpy(),
            "volume": sub["Volume"].to_numpy(),
        }
    )
    out["adj_factor"] = out["adj_close"] / out["close"]
    return out.reset_index(drop=True)


# Above this many tickers the joined form is replaced by a digest. 20 keeps
# every existing single-ticker and small-batch key byte-identical, so the
# cache built before 2026-08-21 stays readable -- the threshold exists to
# bound the filename, not to re-file work already done.
_KEY_TICKER_LIMIT = 20


def _batch_key(tickers: list[str], start: date, end: date) -> str:
    """A cache key for one batch fetch, bounded in length.

    **The joined form has an OS limit and the failure is silent.** Windows
    caps a filename at 255 characters; 317 tickers joined by hyphens is
    several thousand, and `to_parquet` raises `OSError: [Errno 22]` *after*
    the fetch and parse have succeeded. `cscan bars` exited **0** having
    written no rows, on 2026-08-21, because the error landed on the cache
    write rather than on the work.

    So a long list is keyed by digest instead. The digest is over the same
    sorted ticker list the joined form uses, so two batches differing only in
    argument order remain one entry, and it is truncated to 16 hex characters
    -- 64 bits, against a corpus of at most a few thousand batch shapes.

    **Short batches keep their readable key**, and that is deliberate rather
    than nostalgic: it means this change re-files nothing. Every entry
    written before today still answers, which matters because CLAUDE.md's
    rule is that a *source* bump is what discards a cache, and this is not
    one -- what a fetcher returns for given arguments is untouched.
    """
    joined = "-".join(sorted(tickers))
    if len(tickers) <= _KEY_TICKER_LIMIT:
        return f"{joined}_{start}_{end}"
    digest = hashlib.sha256(joined.encode()).hexdigest()[:16]
    return f"batch{len(tickers)}-{digest}_{start}_{end}"


# `_v2` invalidates every entry written before the exclusive-`end` fix.
#
# `_batch_key` is `tickers_start_end`. The fix changed what a given
# `(start, end)` *fetches* without changing the key, so a cached v1 entry
# answers a post-fix request with pre-fix data — silently one session short,
# the exact defect the fix was for.
#
# Caught 2026-08-17: the nightly after the fix still had `market_days` ending
# 2026-08-14, and `run_market` completed in **46 ms**, which is a cache read,
# not a network call. Daily bars looked correct only because the manual
# recovery script had used `end = today + 1`, a different key.
#
# Bumping the source rather than deleting the files makes the invalidation
# declarative: the reason lives next to the change that caused it, and any
# future semantic change to what a key means gets the same treatment.
# `data/cache/yahoo_daily/` (394 MB) and `data/cache/yahoo_hourly/` are now
# unread and safe to delete.
@cached(source="yahoo_daily_v2", key_fn=_batch_key)
def _fetch_daily_batch(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """One batch, with the partial-batch-failure retry (DESIGN §4.3)."""
    raw = _download_daily(tickers, start, end)
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for ticker in tickers:
        sub = _extract_ticker_frame(raw, ticker)
        if _has_no_data(sub):
            failed.append(ticker)
            continue
        frames.append(_tidy_daily(sub, ticker))

    # yfinance returns NaN columns for failed tickers inside an otherwise
    # successful batch rather than raising; retry those individually.
    for ticker in failed:
        raw_single = _download_daily([ticker], start, end)
        sub = _extract_ticker_frame(raw_single, ticker)
        if not _has_no_data(sub):
            frames.append(_tidy_daily(sub, ticker))

    return pd.concat(frames, ignore_index=True) if frames else _empty_bars_frame()


def fetch_bars_daily(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Daily OHLCV plus `adj_factor` for every ticker with data in range.

    A ticker silently missing from the result means Yahoo returned nothing
    for it (delisting, wrong symbol, or full-batch failure after the
    individual retry) — the caller records that against `first_bar` /
    `last_bar` coverage, per DESIGN §4.3's silent-truncation note.
    """
    batches = [_fetch_daily_batch(batch, start, end) for batch in _split_batches(tickers)]
    non_empty = [b for b in batches if not b.empty]
    if not non_empty:
        return _empty_bars_frame()
    return cast(
        pd.DataFrame,
        pd.concat(non_empty, ignore_index=True)
        .sort_values(["ticker", "ts"])
        .reset_index(drop=True),
    )


def _hourly_windows(start: date, end: date) -> list[tuple[date, date]]:
    # Yahoo caps hourly data at ~730 days; cap start date conservatively at 725 days (DESIGN §4.4)
    max_start = end - timedelta(days=725)
    clamped_start = max(start, max_start)

    windows = []
    cur = clamped_start
    while cur < end:
        window_end = min(cur + timedelta(days=HOURLY_WINDOW_DAYS), end)
        windows.append((cur, window_end))
        cur = window_end
    return windows


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_hourly(ticker: str, start: date, end: date) -> pd.DataFrame:
    # `end + 1 day`, same exclusive-bound reason as `_download_daily`.
    # Kept identical rather than shared through a helper: the two calls
    # differ in every other argument, and a one-line wrapper around
    # `yf.download` would hide which knobs each interval actually sets.
    #
    # This one is less visible than the daily case because
    # `_window_key`/`_hourly_windows` walk 60-day windows and the caller
    # asks for a long span, so losing the final day looks like a partial
    # last window rather than a missing session — but the current day's
    # intraday bars were dropped exactly the same way.
    return cast(
        pd.DataFrame,
        yf.download(
            ticker,
            start=start,
            end=end + timedelta(days=1),
            interval="1h",
            auto_adjust=False,
            actions=False,
            threads=False,
            progress=False,
        ),
    )


def _window_key(ticker: str, start: date, end: date) -> str:
    return f"{ticker}_{start}_{end}"


# `_v2` for the same reason as `yahoo_daily_v2` above: `_window_key` is
# `ticker_start_end` and the exclusive-`end` fix changed what those dates
# fetch.
@cached(source="yahoo_hourly_v2", key_fn=_window_key)
def _fetch_hourly_window(ticker: str, start: date, end: date) -> pd.DataFrame:
    raw = _download_hourly(ticker, start, end)
    if raw.empty:
        return _empty_hourly_frame()

    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Normalize exchange-local timestamps to America/New_York, then keep
    # the regular session only (09:30-16:00). Pre-market and after-hours
    # bars compare meaninglessly against a prior-close band (DESIGN §4.4).
    idx = df.index
    idx = (
        idx.tz_localize("America/New_York")
        if idx.tz is None
        else idx.tz_convert("America/New_York")
    )
    df.index = idx
    df = df.between_time("09:30", "16:00")

    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": df.index,
            "open": df["Open"].to_numpy(),
            "high": df["High"].to_numpy(),
            "low": df["Low"].to_numpy(),
            "close": df["Close"].to_numpy(),
            "volume": df["Volume"].to_numpy(),
        }
    ).reset_index(drop=True)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_hourly_batch(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """One hourly request for many tickers.

    Same call as `_download_hourly` with a list instead of a string, and the
    same `end + 1 day` exclusive-bound correction. `group_by="ticker"` gives
    the column MultiIndex `_extract_ticker_frame` already parses for daily.

    **The 60-day cap is unaffected.** Yahoo limits hourly *history per
    request*, not tickers per request, so the window walk in
    `fetch_bars_hourly_many` is unchanged -- this only collapses the
    per-ticker dimension.
    """
    return cast(
        pd.DataFrame,
        yf.download(
            tickers,
            start=start,
            end=end + timedelta(days=1),
            interval="1h",
            auto_adjust=False,
            actions=False,
            group_by="ticker",
            threads=False,
            progress=False,
        ),
    )


def _shape_hourly(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Raw hourly frame -> the project's hourly shape, for one ticker.

    Extracted verbatim from `_fetch_hourly_window` so the batched and
    per-ticker paths cannot drift: the session filter and the timezone
    normalisation are the parts that would silently differ, and a
    difference there changes which bars a band comparison sees.
    """
    if raw.empty:
        return _empty_hourly_frame()

    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    idx = cast(pd.DatetimeIndex, df.index)
    idx = (
        idx.tz_localize("America/New_York")
        if idx.tz is None
        else idx.tz_convert("America/New_York")
    )
    df.index = idx
    df = df.between_time("09:30", "16:00")
    if df.empty:
        return _empty_hourly_frame()

    return pd.DataFrame(
        {
            "ticker": ticker,
            "ts": df.index,
            "open": df["Open"].to_numpy(),
            "high": df["High"].to_numpy(),
            "low": df["Low"].to_numpy(),
            "close": df["Close"].to_numpy(),
            "volume": df["Volume"].to_numpy(),
        }
    ).reset_index(drop=True)


def _hourly_batch_key(tickers: list[str], start: date, end: date) -> str:
    """`{n}_{sha1(tickers)}_{start}_{end}`.

    A hash rather than the joined list, because 50 symbols exceed a sane
    filename. The count is kept in the clear so a cache directory is
    readable at a glance.
    """
    import hashlib

    joined = ",".join(sorted(tickers))
    digest = hashlib.sha1(joined.encode()).hexdigest()[:16]
    return f"{len(tickers)}_{digest}_{start}_{end}"


# `yahoo_hourly_v3`, not `_v2`. The key now identifies a *set* of tickers
# rather than one, so a `_v2` entry answers a different question than the
# one being asked. CLAUDE.md records what reusing a source string through a
# semantic change costs: the `yahoo_daily` fix merged, passed CI, and never
# ran, because every cached entry answered the post-fix request with the
# pre-fix result.
@cached(source="yahoo_hourly_v3", key_fn=_hourly_batch_key)
def _fetch_hourly_window_batch(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """One 60-day window for a batch, long-form with a `ticker` column.

    Falls back to per-ticker on a batch that returns nothing, matching
    `_fetch_daily_batch`'s partial-failure retry: one bad symbol must not
    cost the other forty-nine.
    """
    raw = _download_hourly_batch(tickers, start, end)
    frames: list[pd.DataFrame] = []
    missing: list[str] = []

    for ticker in tickers:
        sub = _extract_ticker_frame(raw, ticker) if len(tickers) > 1 else raw
        shaped = _shape_hourly(sub, ticker)
        if shaped.empty:
            missing.append(ticker)
        else:
            frames.append(shaped)

    # A symbol absent from a multi-ticker response is indistinguishable from
    # one that genuinely has no bars in the window, so it is retried alone.
    # Expected to be common: most of the delisted union has no hourly data.
    for ticker in missing:
        shaped = _shape_hourly(_download_hourly(ticker, start, end), ticker)
        if not shaped.empty:
            frames.append(shaped)

    if not frames:
        return _empty_hourly_frame()
    return cast(pd.DataFrame, pd.concat(frames, ignore_index=True))


def fetch_bars_hourly_many(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Hourly bars for many tickers, one request per window per batch.

    The per-ticker `fetch_bars_hourly` costs one request per ticker per
    window. Measured 2026-08-26 in a nightly: **54.5 minutes for 1,470
    tickers** at `RATE_LIMIT_PER_SEC = 0.5`, against 4.9 minutes for the
    batched daily path over the same universe.

    Returns a dict so a caller that iterates tickers keeps doing so; a
    ticker with no bars is absent, matching `fetch_bars_hourly` returning
    an empty frame.
    """
    per_ticker: dict[str, list[pd.DataFrame]] = {t: [] for t in tickers}
    for w_start, w_end in _hourly_windows(start, end):
        for batch in _split_batches(tickers):
            got = _fetch_hourly_window_batch(batch, w_start, w_end)
            if got.empty:
                continue
            for ticker, sub in got.groupby("ticker", sort=False):
                per_ticker[str(ticker)].append(sub)

    out: dict[str, pd.DataFrame] = {}
    for ticker, parts in per_ticker.items():
        if not parts:
            continue
        out[ticker] = cast(
            pd.DataFrame,
            pd.concat(parts, ignore_index=True)
            .drop_duplicates(subset=["ticker", "ts"])
            .sort_values("ts")
            .reset_index(drop=True),
        )
    return out


def fetch_bars_hourly(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Hourly regular-session bars, walked in 60-day windows (DESIGN §4.4)."""
    windows = [
        _fetch_hourly_window(ticker, w_start, w_end)
        for w_start, w_end in _hourly_windows(start, end)
    ]
    non_empty = [w for w in windows if not w.empty]
    if not non_empty:
        return _empty_hourly_frame()
    return cast(
        pd.DataFrame,
        pd.concat(non_empty, ignore_index=True)
        .drop_duplicates(subset=["ticker", "ts"])
        .sort_values("ts")
        .reset_index(drop=True),
    )


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_actions(ticker: str) -> pd.DataFrame:
    return cast(pd.DataFrame, yf.Ticker(ticker).actions)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_actions_batch(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Splits and dividends for many tickers, one request.

    `actions=True` makes `yf.download` project `Dividends` and `Stock
    Splits` beside the OHLCV columns, so the batch shape is the one
    `_extract_ticker_frame` already parses. Same `end + 1 day` correction as
    every other download here -- yfinance's `end` is exclusive, and CLAUDE.md
    records what forgetting it cost.

    **Windowed, unlike `_download_actions`.** `yf.Ticker(t).actions` returns
    a ticker's entire history; this returns what falls inside the range.
    That difference is the whole reason `run_actions` keeps both paths.
    """
    return cast(
        pd.DataFrame,
        yf.download(
            tickers,
            start=start,
            end=end + timedelta(days=1),
            interval="1d",
            auto_adjust=False,
            actions=True,
            group_by="ticker",
            progress=False,
            threads=False,
        ),
    )


def _actions_batch_key(tickers: list[str], start: date, end: date) -> str:
    """Same shape as `_hourly_batch_key`: a hash of the set, plus the window.

    **The window is what `yahoo_actions` was missing.** Its key was the bare
    ticker, so the first fetch of a name answered every later one forever --
    measured 2026-08-26, three cohorts whose newest `ex_date` equalled their
    cache date exactly. A key must carry everything that determines the
    answer, and for "what happened to this ticker" that includes when the
    question was asked.
    """
    import hashlib

    joined = ",".join(sorted(tickers))
    digest = hashlib.sha1(joined.encode()).hexdigest()[:16]
    return f"{len(tickers)}_{digest}_{start}_{end}"


@cached(source="yahoo_actions_batch_v1", key_fn=_actions_batch_key)
def _fetch_actions_window_batch(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """One window, one batch, tidied to `_ACTIONS_COLUMNS`.

    A symbol absent from the response is indistinguishable from one with no
    actions in the window, and both are correct as "nothing to record", so
    there is no per-ticker retry here -- unlike the bars paths, a missing
    row is not missing data.
    """
    raw = _download_actions_batch(tickers, start, end)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=_ACTIONS_COLUMNS)

    records: list[dict] = []
    for ticker in tickers:
        try:
            sub_frame = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        if sub_frame is None or sub_frame.empty:
            continue
        records.extend(_actions_records(sub_frame.reset_index(), ticker))
    return pd.DataFrame(records, columns=_ACTIONS_COLUMNS)


def _actions_records(df: pd.DataFrame, ticker: str) -> list[dict]:
    """`Dividends`/`Stock Splits` columns -> one record per non-zero event.

    Shared by the per-ticker and batched paths so the two cannot drift into
    disagreeing about what an action row looks like.
    """
    ts_col = "Date" if "Date" in df.columns else df.columns[0]
    df = df.rename(columns={ts_col: "ts"})
    records: list[dict] = []
    for column, action_type in (("Stock Splits", "split"), ("Dividends", "dividend")):
        if column not in df.columns:
            continue
        hit = df.loc[df[column].fillna(0) != 0]
        for _, row in hit.iterrows():
            records.append(
                {
                    "ticker": ticker,
                    "ts": row["ts"],
                    "action_type": action_type,
                    "value": row[column],
                }
            )
    return records


def fetch_actions_many(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Windowed actions for many tickers, batched.

    The nightly path. `fetch_actions` stays for a ticker with no history on
    file, where the full series is genuinely wanted.
    """
    out: dict[str, list[pd.DataFrame]] = {t: [] for t in tickers}
    for batch in _split_batches(tickers):
        got = _fetch_actions_window_batch(batch, start, end)
        if got.empty:
            continue
        for ticker, sub_frame in got.groupby("ticker", sort=False):
            out[str(ticker)].append(sub_frame)
    return {t: pd.concat(parts, ignore_index=True) for t, parts in out.items() if parts}


def _actions_key(ticker: str) -> str:
    """`{ticker}_{today}`.

    **The date is the fix.** `yahoo_actions` keyed on the bare ticker, so a
    ticker fetched on 2026-07-31 still answered with 2026-07-31's history a
    month later -- measured at 640 tickers with zero August actions against
    a 22% base rate in the fresh cohort. Keying on the day makes a stale
    entry unreachable rather than merely old.
    """
    return f"{ticker}_{date.today().isoformat()}"


@cached(source="yahoo_actions_v2", key_fn=_actions_key)
def fetch_actions(ticker: str) -> pd.DataFrame:
    """A ticker's **entire** split and dividend history, tidied.

    Used where the full series is wanted -- a ticker with nothing on file.
    `fetch_actions_many` is the incremental path and is what nightly runs.

    `yahoo_actions_v2`, not `yahoo_actions`: the key now carries the date,
    so every `_v1` entry answers a different question than the one being
    asked. Reusing the source string is the failure CLAUDE.md records, where
    a correct fix merged, passed CI and never ran.
    """
    raw = _download_actions(ticker)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=_ACTIONS_COLUMNS)

    return pd.DataFrame(_actions_records(raw.reset_index(), ticker), columns=_ACTIONS_COLUMNS)


def _opt_float(value: object) -> float:
    """`float(value)` or NaN, never a default (invariant 4).

    Yahoo omits a field rather than sending null when a session has not
    produced it -- pre-market, a halted name. Absent must stay absent: a
    volume of 0 and an unknown volume are different facts, and only one of
    them can be drawn.
    """
    return float(value) if value is not None else float("nan")  # type: ignore[arg-type]


def _download_quotes(symbols: list[str]) -> dict:
    """One HTTP GET against Yahoo's batch quote endpoint.

    Its own function so `fetch_quotes` has a seam the request-count test
    can patch — the count is the whole point of this path, and asserting
    on rows returned (the only prior coverage) never sees it.

    Routed through `yfinance`'s `YfData` rather than a bare HTTP client
    because Yahoo rejects requests that do not carry a browser TLS
    fingerprint plus a valid cookie/crumb pair, and `YfData` already
    maintains that handshake. That is internal yfinance API, so it is
    confined to this one function: if it moves, only this breaks.
    """
    raw = YfData().get_raw_json(_QUOTE_URL, params={"symbols": ",".join(symbols)})
    return cast(dict, raw)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def fetch_quotes(tickers: list[str], *, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Batch live quote for the poller (DESIGN §4.8: 1-2 requests total).

    Never cached — a stale quote is a wrong signal, not a saved network
    call. That rule is enforced here, not merely asserted: a quote whose
    `regularMarketTime` is older than `QUOTE_MAX_AGE_SECONDS`, or that
    carries no timestamp at all, is dropped rather than returned. The
    chart endpoint this replaced expressed a stale feed as an empty
    frame; the quote endpoint expresses it as the last known price with
    an old timestamp, which would otherwise reach `breach_live` looking
    exactly like a current one.

    One request per `QUOTE_BATCH_SIZE` symbols. The previous
    implementation called `yf.download(tickers, ...)`, which accepts a
    list and so reads as batched, but fans out to one request per symbol
    internally: 140 per tick against this universe, ~10,900 per session,
    against a design budget of 1-2. `@rate_limited` sits on this function
    and throttles calls to it, never the requests inside one call, so the
    fan-out was entirely unthrottled.

    `now` is injectable for tests; it defaults to the current UTC time.
    Reading the clock is allowed here because this is `jobs/`, never
    `core/` (invariant 1).
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    rows = []
    for batch in _split_batches(tickers, QUOTE_BATCH_SIZE):
        payload = _download_quotes(batch)
        for quote in (payload.get("quoteResponse") or {}).get("result") or []:
            price = quote.get("regularMarketPrice")
            quoted_at = quote.get("regularMarketTime")
            # Invariant 4: a quote missing either half is dropped, never
            # defaulted to zero and never carried forward from the last
            # tick. An undateable price cannot be shown to be current.
            if price is None or quoted_at is None:
                continue
            ts = pd.to_datetime(quoted_at, unit="s", utc=True)
            if (now - ts).total_seconds() > QUOTE_MAX_AGE_SECONDS:
                continue
            day_open = quote.get("regularMarketOpen")
            rows.append(
                {
                    "ticker": quote.get("symbol"),
                    "ts": ts,
                    "price": float(price),
                    # Invariant 4: absent stays absent. NaN propagates to
                    # `_is_bear_reversal`, which treats it as "cannot
                    # evaluate" rather than as a passing comparison.
                    "day_open": float(day_open) if day_open is not None else float("nan"),
                    # Same absent-stays-absent rule (invariant 4). A quote
                    # without a high is not a quote with a high equal to
                    # the price -- the partial candle simply cannot be
                    # drawn, and NaN says so.
                    "day_high": _opt_float(quote.get("regularMarketDayHigh")),
                    "day_low": _opt_float(quote.get("regularMarketDayLow")),
                    "day_volume": _opt_float(quote.get("regularMarketVolume")),
                }
            )
    return pd.DataFrame(rows, columns=_QUOTE_COLUMNS)


_SHARES_FULL_COLUMNS = ["ticker", "filed_on", "shares"]


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_shares_full(ticker: str, start: date, end: date) -> pd.Series | None:
    # `Ticker.get_shares_full` hits Yahoo's own "quoteSummary" endpoint, not
    # `yf.download` — a 404 (bad symbol) surfaces as a caught-and-logged
    # error inside yfinance itself, which returns `None` rather than
    # raising. `_has_no_data`-style emptiness handling lives in the caller;
    # this wrapper exists only to give the `None` case the same
    # rate-limit/retry treatment as every other network call in this module.
    return cast(
        "pd.Series | None",
        yf.Ticker(ticker).get_shares_full(start=start, end=end),
    )


def _shares_full_key(ticker: str, start: date, end: date) -> str:
    return f"{ticker}_{start}_{end}"


@cached(source="yahoo_shares_full", key_fn=_shares_full_key)
def fetch_shares_full(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Dated shares-outstanding series, the current-shares fallback (BUILD follow-up).

    `Ticker(ticker).info["sharesOutstanding"]` was the obvious first fallback
    for the ~69 tickers whose SEC XBRL fact is missing or stale (see the
    `SHARES_STALENESS_DAYS` comment in `jobs/ingest.py`), and it was
    rejected: it is one number, true only *today*, and DESIGN §2.4 already
    names the failure mode this would reproduce ("the rejected alternative
    was holding current shares constant backward... AAPL's 2016 cap would
    come out ~50% too low"). Stamping today's count onto every historical
    `as_of` is exactly that mistake with a different data source.

    `get_shares_full(start, end)` is different in kind, not just a wider
    window: verified live against BRK-B, MA, V, and META, it returns a
    `pd.Series` of Yahoo's own dated share-count estimates reaching back to
    ~2015 for all four, each value tied to the date Yahoo attributes it to
    (buybacks and issuances move the count between points). That is a
    genuine point-in-time series, not "current, applied backward" — a row
    dated 2016 in this frame describes 2016, the same contract SEC's
    `filed_on` rows carry. It is why this is the fallback used, and why
    `run_shares` inserts every point in the requested window rather than
    only the latest one: a single-most-recent write would silently
    re-introduce the "current constant held backward" problem for any
    `as_of` between two Yahoo observation dates prior to the most recent one.

    Returns an empty frame (not raising) when Yahoo has nothing for
    `ticker` in `[start, end)` — a delisted or never-covered symbol is a
    `run_shares` skip note, not a fetch failure.
    """
    raw = _download_shares_full(ticker, start, end)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=_SHARES_FULL_COLUMNS)
    index = cast(pd.DatetimeIndex, raw.index)
    idx = index.tz_localize(None) if index.tz is not None else index
    return pd.DataFrame(
        {
            "ticker": ticker,
            "filed_on": idx.date,
            "shares": raw.to_numpy(dtype="int64"),
        }
    ).reset_index(drop=True)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def fetch_option_expirations(ticker: str) -> list[str]:
    """Every available expiration date, nearest first (ADR 050's call
    overlay needs the full list to pick "nearest expiry past day 5";
    `fetch_chain` below only returns one expiry's calls at a time). Never
    cached — new expiries list weekly.
    """
    return list(yf.Ticker(ticker).options)


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def fetch_chain(ticker: str, expiration: str | None = None) -> pd.DataFrame:
    """Live option chain for the call overlay (ADR 050). Never cached."""
    tk = yf.Ticker(ticker)
    exp = expiration or (tk.options[0] if tk.options else None)
    if exp is None:
        return pd.DataFrame()
    calls = cast(pd.DataFrame, tk.option_chain(exp).calls.copy())
    calls["ticker"] = ticker
    calls["expiration"] = exp
    return calls


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _download_info(ticker: str) -> dict:
    """Uncached, rate-limited `Ticker.info`. Split out so `fetch_sector`
    keeps the same shape as every other cached fetcher in this module."""
    return dict(yf.Ticker(ticker).info or {})


def _net_assets_key(ticker: str) -> str:
    """`{ticker}_{today}`.

    Dated for the same reason `_actions_key` is: net assets move daily
    through creation and redemption, so a key without a date freezes a
    fund's size at whenever it was first asked about. `yahoo_actions` made
    exactly that mistake and held 640 tickers at their 2026-07-31 values.
    """
    return f"{ticker}_{date.today().isoformat()}"


@cached(source="yahoo_net_assets_v1", key_fn=_net_assets_key)
def fetch_net_assets(ticker: str) -> pd.DataFrame:
    """A fund's net assets and price, for ADR 156's derived share count.

    **Why this exists.** `sharesOutstanding` is wrong for every ETF here,
    not merely stale: measured 2026-08-26, SPY and QQQ carry one constant
    repeated on all 96-99 dated rows, ending 2021-03-17, identical to what
    `info` returns today. An ETF's share count moves daily through creation
    and redemption -- that mechanism is what holds the price near NAV -- so
    a constant is not a disagreeing measurement, it is a wrong one. VOO and
    IBIT return nothing at all.

    `netAssets` is published for all four. For a fund `price x shares` **is**
    net assets by definition of NAV, so `netAssets / price` recovers the
    share count exactly rather than estimating it. That identity is what
    makes ADR 156's option (i) a change of units instead of a fabrication.

    Returns a one-row frame -- `@cached` writes with `to_parquet`. An empty
    frame means Yahoo published neither field, which is a skip, not a
    failure: an ordinary company has no `netAssets` and should never reach
    here.
    """
    raw = _download_info(ticker)
    cols = ["ticker", "net_assets", "price"]
    if not raw:
        return pd.DataFrame(columns=cols)
    net = raw.get("netAssets")
    # `regularMarketPrice` first: `previousClose` is a day stale, and a
    # fund's NAV and price move together intraday.
    price = raw.get("regularMarketPrice") or raw.get("previousClose")
    if net is None or not price:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([{"ticker": ticker, "net_assets": float(net), "price": float(price)}])


def _sector_key(ticker: str) -> str:
    return ticker


@cached(source="yahoo_sector_v1", key_fn=_sector_key)
def fetch_sector(ticker: str) -> pd.DataFrame:
    """Yahoo's sector and industry for one ticker (ADR 148).

    **`Ticker.info` is used here and was rejected for shares, and the two
    are not in tension.** `fetch_shares_full`'s docstring rejects
    `info["sharesOutstanding"]` because it is one number true only *today*,
    so stamping it onto every historical `as_of` reproduces the
    "current constant held backward" error DESIGN 2.4 names. Sector is not a
    series: DESIGN 7.3 lists it under **Static**, and a company's GICS
    sector is a classification rather than a measurement. There is no
    as-of to get wrong.

    Returns a one-row frame rather than a string because `@cached` writes
    with `to_parquet` and the cache contract is a DataFrame. An empty frame
    means Yahoo has nothing, which is a skip note and not a failure -- a
    delisted symbol is expected to return nothing.

    The raw Yahoo vocabulary is returned unmapped. `core.sectors.
    normalize_yahoo_sector` does the GICS rename, so this stays a fetcher
    and the taxonomy decision stays pure and testable.
    """
    raw = _download_info(ticker)
    if not raw:
        return pd.DataFrame(columns=["ticker", "sector", "industry"])
    return pd.DataFrame(
        [
            {
                "ticker": ticker,
                "sector": raw.get("sector"),
                "industry": raw.get("industry"),
            }
        ]
    )
