"""`poll` job: live band/stochastic breach detection (DESIGN §4.8, BUILD §8.1-8.2).

Bands are loaded once at session start and never recomputed intraday
(DESIGN is explicit on this). The loop is two float comparisons per ticker
per tick — `core.signals.breach_live` is the *only* comparison, same
function the backtest calls (ADR 006) — so the real constraints are the
yfinance rate limit and quote staleness, not compute.

**Debounce lives in Postgres, not process memory** (ADR 084/090): a new
breach is only recorded if no `events` row already exists for
`(config_hash, ticker, signal_date, signal_type, entry_kind='touch')`. That
UNIQUE constraint is the debounce state, so a poller restart mid-session
sees exactly what already fired and does not re-notify.

**Invariant 5b**: every event this module writes carries
`split_key = 'holdout'` — never computed from date, because a live signal is
never train or validate data.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Callable

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core import signals as core_signals
from capitalscan.core.config import Config, ExitParams, SignalParams, StatsParams
from capitalscan.core.types import Bands, Side, SignalType
from capitalscan.jobs import call_overlay, db_io, notify, positions, scheduled_runs
from capitalscan.jobs.config import config_hash
from capitalscan.jobs.fetch import yahoo
from capitalscan.jobs.ingest import IngestReport, run_job
from capitalscan.jobs.notify import Notifier
from capitalscan.jobs.provenance import git_sha

MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
SESSION_SECONDS = 6.5 * 3600
MAX_CONSECUTIVE_FAILURES = 3  # DESIGN §4.9: 3 consecutive tick failures alerts and exits


def _now_et() -> datetime:
    # ADR 081: the poller runs natively on the workstation. Convert local
    # time to ET to handle workstations in other timezones (e.g., PT).
    from datetime import timezone as tz
    import time

    local_now = datetime.now()
    # Get offset from local time to UTC
    if time.daylight:
        utc_offset = -time.altzone
    else:
        utc_offset = -time.timezone

    # Convert to UTC then to ET
    utc_now = local_now - timedelta(seconds=utc_offset)
    # ET is UTC-5 (EST) or UTC-4 (EDT)
    et_offset = -4 if time.daylight else -5
    et_now = utc_now + timedelta(hours=et_offset)
    return et_now


def _none(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def _json_safe(value):
    """Recursively coerce numpy/pandas scalars and timestamps so `state_json`
    and `call_overlay_json` serialize cleanly into `jsonb` — `db_io._native`
    only coerces top-level scalars, not values nested inside a dict field.
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def _load_in_trade_tickers(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT ON (ticker) ticker, in_trade FROM universe "
                "ORDER BY ticker, as_of DESC"
            )
        ).fetchall()
    return [r.ticker for r in rows if r.in_trade]


def _load_indicator_rows(engine: Engine, tickers: list[str]) -> dict[str, pd.Series]:
    """Latest indicator row per ticker. Once the session has begun this *is*
    t-1: the nightly job wrote it after yesterday's close, and today's own
    close doesn't exist yet.
    """
    if not tickers:
        return {}
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT DISTINCT ON (ticker) * FROM indicators "
                "WHERE ticker = ANY(:tickers) AND interval = '1d' "
                "ORDER BY ticker, ts DESC"
            ),
            conn,
            params={"tickers": tickers},  # type: ignore[arg-type]
        )
    return {row["ticker"]: row for _, row in df.iterrows()}


def _bands_from(ind_row: pd.Series) -> Bands:
    return Bands(
        bb_lower=float(ind_row["bb_lower"]),
        bb_mid=float(ind_row["bb_mid"]),
        bb_upper=float(ind_row["bb_upper"]),
        k_full=float(ind_row["k_full"]),
        d_full=float(ind_row["d_full"]),
        k_fast=float(ind_row["k_fast"]),
        atr_14=float(ind_row["atr_14"]),
    )


def _load_market_row(engine: Engine, as_of: date) -> pd.Series | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM market_days WHERE ts <= :as_of ORDER BY ts DESC LIMIT 1"),
                {"as_of": as_of},
            )
            .mappings()
            .one_or_none()
        )
    return pd.Series(dict(row)) if row else None


def _fired_by_side(fired_types: list[SignalType]) -> dict[Side, list[SignalType]]:
    """Groups `breach_live`'s flat, specificity-ordered list back by side,
    reusing `core.signals`' own pinned ordering (ADR 057) rather than
    re-declaring it — this is job-layer bookkeeping on top of a comparison
    that already ran, not a second signal implementation (ADR 006).
    """
    from capitalscan.core.signals import _SPECIFICITY

    out: dict[Side, list[SignalType]] = {}
    for side, order in _SPECIFICITY.items():
        matched = [t for t in order if t in fired_types]
        if matched:
            out[side] = matched
    return out


def _already_fired(
    engine: Engine, chash: str, ticker: str, signal_date: date, signal_type: str
) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM events WHERE config_hash = :chash AND ticker = :ticker "
                "AND signal_date = :d AND signal_type = :st AND entry_kind = 'touch'"
            ),
            {"chash": chash, "ticker": ticker, "d": signal_date, "st": signal_type},
        ).one_or_none()
    return row is not None


def _get_event_id(
    engine: Engine, chash: str, ticker: str, signal_date: date, signal_type: str
) -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id FROM events WHERE config_hash = :chash AND ticker = :ticker "
                "AND signal_date = :d AND signal_type = :st AND entry_kind = 'touch'"
            ),
            {"chash": chash, "ticker": ticker, "d": signal_date, "st": signal_type},
        ).one()
    return int(row.id)


def _build_live_event_row(
    side: Side,
    types: list[SignalType],
    ticker: str,
    price: float,
    signal_date: date,
    ind_row: pd.Series,
    market_row: pd.Series | None,
    chash: str,
    run_id: str,
) -> dict:
    from capitalscan.jobs.compute import _dd_bucket

    touched_band = (side is Side.LONG and SignalType.BB_LOWER_TOUCH in types) or (
        side is Side.SHORT and SignalType.BB_UPPER_TOUCH in types
    )
    touch_level = None
    if touched_band:
        touch_level = (
            float(ind_row["bb_lower"]) if side is Side.LONG else float(ind_row["bb_upper"])
        )

    return {
        "run_id": run_id,
        "config_hash": chash,
        "ticker": ticker,
        "signal_date": signal_date,
        "signal_type": types[0].value,
        "signal_types_all": [t.value for t in types],
        "signal_strength": len(types),
        "side": side.value,
        "touch_level": touch_level,
        "bb_pctb": _none(ind_row.get("bb_pctb")),
        "bb_width_pct": _none(ind_row.get("bb_width_pct")),
        "k_full": _none(ind_row.get("k_full")),
        "d_full": _none(ind_row.get("d_full")),
        "k_fast": _none(ind_row.get("k_fast")),
        "k_cross_up": _none(ind_row.get("k_cross_up")),
        "k_cross_down": _none(ind_row.get("k_cross_down")),
        "atr_14": _none(ind_row.get("atr_14")),
        "rv_pct_252d": _none(ind_row.get("rv_pct_252d")),
        "dd_52w": _none(ind_row.get("dd_52w")),
        "sma200_slope_60": _none(ind_row.get("sma200_slope_60")),
        # Live approximation: the session isn't closed yet, so this compares
        # the live quote to t-1's SMA200 rather than a settled close (unlike
        # the backtest, which uses bar t's own close).
        "above_sma200": None
        if pd.isna(ind_row.get("sma_200"))
        else bool(price > float(ind_row["sma_200"])),
        "vol_z_20d": _none(ind_row.get("vol_z_20d")),
        "days_to_earnings": _none(ind_row.get("days_to_earnings")),
        "vix_close": None if market_row is None else _none(market_row.get("vix_close")),
        "spx_ret_1d": None if market_row is None else _none(market_row.get("spx_ret_1d")),
        "dd_bucket": _dd_bucket(ind_row.get("dd_52w")),
        "entry_kind": "touch",
        "entry_date": signal_date,
        "entry_price": price,
        # No "open" concept for an intraday live quote — left null rather
        # than fabricated (DESIGN §3.11).
        "entry_gapped": None,
        "split_key": "holdout",
    }


def _state_json(band: Bands, ind_row: pd.Series, price: float) -> dict:
    payload = {k: _none(v) for k, v in ind_row.items()}
    payload["live_price"] = price
    payload["bands"] = {
        "bb_lower": band.bb_lower,
        "bb_mid": band.bb_mid,
        "bb_upper": band.bb_upper,
        "k_full": band.k_full,
        "d_full": band.d_full,
        "k_fast": band.k_fast,
        "atr_14": band.atr_14,
    }
    return _json_safe(payload)  # type: ignore[no-any-return]


def _process_tick(
    engine: Engine,
    chash: str,
    sp: SignalParams,
    ep: ExitParams,
    stats: StatsParams,
    bands: dict[str, Bands],
    ind_rows: dict[str, pd.Series],
    tickers: list[str],
    notifiers: list[Notifier],
    now: datetime,
    run_id: str,
) -> int:
    """One tick: quote every ticker, record and notify any new breach.
    Returns the count of new events fired this tick.
    """
    quotes = yahoo.fetch_quotes(tickers)
    if quotes.empty:
        return 0
    today = now.date()
    market_row = _load_market_row(engine, today)
    fired_count = 0

    for row in quotes.itertuples():
        ticker = row.ticker
        price = float(row.price)
        band = bands.get(ticker)
        ind_row = ind_rows.get(ticker)
        if band is None or ind_row is None:
            continue

        fired_types = core_signals.breach_live(price, band, sp)
        if not fired_types:
            continue

        for side, side_types in _fired_by_side(fired_types).items():
            signal_type = side_types[0].value
            if _already_fired(engine, chash, ticker, today, signal_type):
                continue

            event_row = _build_live_event_row(
                side, side_types, ticker, price, today, ind_row, market_row, chash, run_id
            )
            db_io.upsert(
                engine,
                "events",
                [event_row],
                ["config_hash", "ticker", "signal_date", "signal_type", "entry_kind"],
            )
            event_id = _get_event_id(engine, chash, ticker, today, signal_type)

            breached = "lower" if side is Side.LONG else "upper"
            db_io.upsert(
                engine,
                "quotes_live",
                [
                    {
                        "ticker": ticker,
                        "ts": now,
                        "price": price,
                        "breached": breached,
                        "event_id": event_id,
                    }
                ],
                ["ticker", "ts"],
            )

            overlay = None
            if side is Side.LONG:
                overlay = call_overlay.build_overlay(
                    ticker, price, stats.reach_targets, as_of=today
                )

            atr = float(ind_row.get("atr_14") or 0.0)
            stop_offset = ep.stop_atr_k * atr
            stop_level = price - stop_offset if side is Side.LONG else price + stop_offset
            positions.emit_order_intent(
                engine,
                event_id,
                ticker,
                today,
                signal_type,
                side.value,
                event_row["touch_level"],
                stop_level,
                run_id=run_id,
                git_sha=git_sha(),
            )

            subject = f"{ticker} {signal_type}"
            body = (
                f"{ticker} fired {signal_type} at {price:.2f} "
                f"(touch level {event_row['touch_level']}, k_full {ind_row.get('k_full')})."
            )
            channels_sent = notify.notify_all(notifiers, subject, body)

            db_io.append(
                engine,
                "signal_reports",
                [
                    {
                        "event_id": event_id,
                        "ticker": ticker,
                        "fired_at": now,
                        "state_json": _state_json(band, ind_row, price),
                        "cell_id": None,
                        "prediction_id": None,
                        "call_overlay_json": _json_safe(overlay) if overlay else None,
                        "channels_sent": channels_sent,
                        "model_version": None,
                        "git_sha": git_sha(),
                    }
                ],
            )
            fired_count += 1
    return fired_count


def run_poll(
    interval: int = 300,
    tickers: list[str] | None = None,
    engine: Engine | None = None,
    sp: SignalParams | None = None,
    ep: ExitParams | None = None,
    stats: StatsParams | None = None,
    notifiers: list[Notifier] | None = None,
    now_fn: Callable[[], datetime] = _now_et,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_ticks: int | None = None,
) -> IngestReport:
    """The `poll` job (DESIGN §4.8).

    `now_fn`/`sleep_fn`/`max_ticks` exist purely for tests: production calls
    leave them at their defaults and the loop runs to market close;
    `max_ticks` lets a test drive a handful of iterations without waiting on
    wall-clock time or real market hours.
    """
    engine = engine or db_io.get_engine()
    sp = sp or SignalParams()
    ep = ep or ExitParams()
    stats = stats or StatsParams()
    chash = config_hash(Config(signals=sp))
    notifiers = notifiers if notifiers is not None else notify.active_notifiers()

    with run_job(engine, "poll", {"interval": interval}) as report:
        scheduled_runs.record(engine, "poll", run_id=report.run_id, now=now_fn())

        resolved_tickers = tickers or _load_in_trade_tickers(engine)
        if not resolved_tickers:
            report.notes = "no in_trade tickers on file"
            return report

        ind_rows = _load_indicator_rows(engine, resolved_tickers)
        bands = {t: _bands_from(row) for t, row in ind_rows.items()}

        ticks_expected = max(1, round(SESSION_SECONDS / interval))
        ticks_completed = 0
        consecutive_failures = 0
        session_date = now_fn().date()
        started_at = now_fn()

        tick = 0
        while True:
            if max_ticks is not None and tick >= max_ticks:
                break
            now = now_fn()
            if max_ticks is None and not (MARKET_OPEN <= now.time() <= MARKET_CLOSE):
                break
            try:
                fired = _process_tick(
                    engine,
                    chash,
                    sp,
                    ep,
                    stats,
                    bands,
                    ind_rows,
                    resolved_tickers,
                    notifiers,
                    now,
                    report.run_id,
                )
                report.rows_written += fired
                ticks_completed += 1
                consecutive_failures = 0
            except Exception as exc:  # DESIGN §4.9: log, sleep, continue
                consecutive_failures += 1
                report.notes = f"tick failure: {exc}"
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    break
            tick += 1
            if max_ticks is not None and tick >= max_ticks:
                break
            sleep_fn(interval)

        coverage_pct = round(100 * ticks_completed / ticks_expected, 3) if ticks_expected else 0.0
        db_io.upsert(
            engine,
            "poller_sessions",
            [
                {
                    "session_date": session_date,
                    "started_at": started_at,
                    "ended_at": now_fn(),
                    "ticks_completed": ticks_completed,
                    "ticks_expected": ticks_expected,
                    "coverage_pct": coverage_pct,
                    "notes": report.notes,
                }
            ],
            ["session_date"],
        )
    return report
