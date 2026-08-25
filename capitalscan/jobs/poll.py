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
from dataclasses import asdict, dataclass
from datetime import date, datetime
from datetime import time as dtime
from typing import Callable, cast
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core import signals as core_signals
from capitalscan.core.config import Config, ExitParams, SignalParams, StatsParams
from capitalscan.core.signals import _breach, _isnan
from capitalscan.core.types import Bands, Bound, Side, SignalType
from capitalscan.jobs import call_overlay, db_io, notify, positions, scheduled_runs, sync
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
    """Now, in ET, **timezone-aware** (ADR 127).

    ADR 081: the poller runs natively on the workstation, which may sit in
    any zone, so session comparisons happen in ET.

    **It returned a naive datetime until 2026-08-19, and that was a bug.**
    The value carried ET wall-clock time with no tzinfo, and it is written
    to `signal_reports.fired_at` and `quotes_live.ts` — both `timestamptz`,
    on a database running `Etc/UTC`. Postgres reads a naive value as UTC,
    so every poller timestamp landed **four hours early** (five under EST).

    One moment, two tables, measured 2026-08-13:

        runs.started_at          13:30:41+00   (SQL wrote it: correct)
        signal_reports.fired_at  09:30:43+00   (the poller wrote it)

    Two seconds apart in reality, four hours apart in storage. On the
    screener a 09:30 open rendered as **05:30**, because `clock()` formats
    in ET over a value that was already ET.

    The manual offset arithmetic is gone too. `time.daylight` is true when
    the zone *observes* DST at all, not when DST is *currently in effect*,
    so `et_offset = -4 if time.daylight else -5` was wrong every January in
    any DST-observing zone. `ZoneInfo` knows the transition dates.

    **Every comparison against this value must also be aware.** Session
    bounds are compared via `.time()`, which drops the offset, so those are
    unaffected — but a future caller subtracting a naive `datetime.now()`
    from this will raise rather than silently skew, which is the failure
    mode worth having.
    """
    return datetime.now(ZoneInfo("America/New_York"))


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


def _load_pollable_tickers(engine: Engine) -> dict[str, str]:
    """Every ticker the poller watches, mapped to its population.

    `'trade'`, or the `watch_reason` -- `'history'` or `'pullback'` (ADR
    149). Both populations are polled: a watched name is one the universe
    would admit but for a criterion it cannot yet judge, and the whole point
    of watching it is to see it fire.

    **The reason, not merely the fact.** The caller labels the notification
    with it, and the two reasons are different things to read at 06:45 --
    "too new to judge" and "pullback inside an uptrend" were admitted by
    different arguments and ADR 149 stores them separately so they can be
    told apart. A watch alert identical to a tradeable one is how the
    distinction is forgotten.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT ON (ticker) ticker, in_trade, in_watch, watch_reason "
                "FROM universe "
                "ORDER BY ticker, as_of DESC"
            )
        ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        if r.in_trade:
            out[r.ticker] = "trade"
        elif r.in_watch:
            out[r.ticker] = str(r.watch_reason or "watch")
    return out


def _load_in_trade_tickers(engine: Engine) -> list[str]:
    """Trade-universe tickers only. Kept for callers that mean exactly that."""
    return [t for t, pop in _load_pollable_tickers(engine).items() if pop == "trade"]


WATCH_REASONS = ("history", "pullback")


def _watch_tag(population: str) -> str:
    """Notification suffix for a watched name, empty for a tradeable one."""
    return f" [WATCH: {population}]" if population in WATCH_REASONS else ""


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


def _write_session_row(
    engine: Engine,
    session_date: date,
    started_at: datetime,
    ended_at: datetime,
    ticks_completed: int,
    ticks_expected: int,
    notes: str | None,
) -> None:
    """The session row, rewritten every tick rather than once at the end.

    It was a single write on exit, which made it a *record* of a session.
    Written per tick it is also a **heartbeat**, and that is what the
    serving copy needs (ADR 153): a reader that sees no signals cannot
    otherwise tell a quiet session from a poller that died at 07:15.
    `ended_at` is therefore "last tick", not "finished" — the row is
    complete at every moment and simply stops advancing when the poller
    stops.

    Same key, so this is the same one row all session; a tick costs one
    upsert of one row.
    """
    coverage_pct = round(100 * ticks_completed / ticks_expected, 3) if ticks_expected else 0.0
    db_io.upsert(
        engine,
        "poller_sessions",
        [
            {
                "session_date": session_date,
                "started_at": started_at,
                "ended_at": ended_at,
                "ticks_completed": ticks_completed,
                "ticks_expected": ticks_expected,
                "coverage_pct": coverage_pct,
                "notes": notes,
            }
        ],
        ["session_date"],
    )


def _push_live(engine, chash, session_date, run_id, watermark, report):
    """Copy this tick to the serving store, or carry on without it.

    **Never fails the poll.** Serving is a derived copy; the research store
    is the source of truth and has already been written by the time this
    runs. A Pi that is asleep, a moved DHCP lease, or an unset
    `DATABASE_URL_SERVING` must all degrade to "the site is behind", not to
    "the session lost a tick".

    Returns the watermark unchanged on failure, so the next tick re-ships
    whatever this one could not.
    """
    from capitalscan.jobs import sync as sync_job

    try:
        _, advanced = sync_job.run_live_sync(chash, session_date, run_id, watermark, source=engine)
    except Exception as exc:  # noqa: BLE001 - reported, the poll continues
        report.notes = f"live sync skipped: {exc}"
        return watermark
    return advanced


def _write_live_bars(engine: Engine, quotes: pd.DataFrame, today: date, run_id: str) -> None:
    """Upsert today's partial candle per ticker (ADR 128).

    **`bars_live`, never `bars`.** `cscan indicators` computes on whatever
    is in `bars`, so a partial row there would get an indicator row and the
    t-1 lookup would read today's unfinished values as tomorrow's t-1 --
    silent look-ahead. The separate table keeps invariant 3 structural
    rather than making eighteen readers carry a predicate.

    One row per `(ticker, session_date)`, overwritten each tick. Yahoo's
    `regularMarketDayHigh`/`Low` are cumulative session extremes, so a
    later quote is strictly better information and there is nothing to
    accumulate.

    A quote with no `price` never reaches here -- `fetch_quotes` drops it
    (invariant 4) -- and `close` is the only NOT NULL column, so a row
    always describes at least a current price.
    """
    rows = [
        {
            "ticker": q.ticker,
            "session_date": today,
            "ts": q.ts,
            "open": _none(getattr(q, "day_open", None)),
            "high": _none(getattr(q, "day_high", None)),
            "low": _none(getattr(q, "day_low", None)),
            # `itertuples` types every field as the frame's union, so the
            # cast is for mypy rather than for the value: `fetch_quotes`
            # already coerced price to float and drops a row without one.
            "close": float(cast(float, q.price)),
            "volume": _none(getattr(q, "day_volume", None)),
            "run_id": run_id,
        }
        for q in quotes.itertuples()
    ]
    if rows:
        db_io.upsert(
            engine,
            "bars_live",
            rows,
            ["ticker", "session_date"],
            update_columns=["ts", "open", "high", "low", "close", "volume", "run_id"],
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
    population: str = "trade",
) -> dict:
    """`population` is `'trade'` or `'watch'` (ADR 149), and is **required
    to be stated** rather than defaulted: `events.in_trade` is NOT NULL
    DEFAULT true, so a watched name omitted here is written as tradeable and
    joins ADR 112's study population without anything looking wrong."""
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
        # Stated, never defaulted. `events.in_trade` is NOT NULL DEFAULT
        # true, so a watched name omitted here would be written as tradeable
        # and land in ADR 112's population (ADR 149).
        "in_trade": population == "trade",
        "in_watch": population in WATCH_REASONS,
        # No "open" concept for an intraday live quote — left null rather
        # than fabricated (DESIGN §3.11).
        "entry_gapped": None,
        "split_key": "holdout",
    }


def _state_json(
    band: Bands, ind_row: pd.Series, price: float, day_open: float = float("nan")
) -> dict:
    payload = {k: _none(v) for k, v in ind_row.items()}
    payload["live_price"] = price
    # `day_open` is stored so a reader can re-judge the reversal call
    # afterwards. Without it the report carries the band and the price but
    # not the third number the rule compares, so "how close was this?" can
    # only be answered by going back to the quote feed, which is gone.
    payload["day_open"] = _none(day_open)
    payload["bands"] = {
        "bb_lower": band.bb_lower,
        "bb_mid": band.bb_mid,
        "bb_upper": band.bb_upper,
        "k_full": band.k_full,
        "d_full": band.d_full,
        "k_fast": band.k_fast,
        "atr_14": band.atr_14,
    }
    # `asdict`, not the dataclass itself: `_json_safe` walks dicts and
    # lists and passes anything else through, so a bare `ReversalState`
    # would reach the jsonb insert as a Python object. Found by
    # `test_state_json_carries_the_open_and_the_reversal_block`.
    payload["bear_reversal"] = _json_safe(asdict(reversal_state(price, day_open, band)))
    # ADR 144. Stored beside it rather than instead of it: a wide bar can
    # break both bands, and a reader asking "how close was this?" should get
    # the same answer shape whichever side they are looking at.
    payload["bull_reversal"] = _json_safe(asdict(bull_reversal_state(price, day_open, band)))
    return _json_safe(payload)  # type: ignore[no-any-return]


def is_bull_reversal(price: float, day_open: float, bands: Bands) -> bool:
    """ADR 144's live half: price below the band, but above today's open.

    ```
    price <= bb_lower  AND  price > day_open
    ```

    The long-side mirror of `is_bear_reversal`, and a mirror in the same
    limited sense the stored flags are: same shape, opposite `Bound` and
    opposite open/close comparison, nothing else different. The stored flag
    is `close > open AND close <= bb_lower[t]` and can only be evaluated at
    the close; mid-session the current quote stands in for it, so this is a
    live estimate that can stop being true before the bell.

    `bb_lower` comes from the prior close (the poller's `bands`), matching
    the stored flag's band and invariant 3.

    A NaN `day_open` returns False. "Cannot evaluate" is not "fired".
    """
    if _isnan(price) or _isnan(day_open):
        return False
    return _breach(price, bands.bb_lower, Bound.LOWER) and price > day_open


def is_bear_reversal(price: float, day_open: float, bands: Bands) -> bool:
    """ADR 108's live half: price above the band, but below today's open.

    The intraday analogue of `bear_close_above_upper`, and deliberately
    **not** the same predicate. The stored flag is close-confirmed —
    `open > close AND close >= bb_upper[t-1]` — and can only be evaluated
    once the session ends. Mid-session there is no close, so the current
    quote stands in for it:

    ```
    price >= bb_upper  AND  price < day_open
    ```

    A name satisfying this at the close satisfies the stored flag too, since
    the closing quote *is* the close. Intraday it is a live estimate that
    can stop being true before the bell, which is exactly why the poller
    surfaces it as "currently" rather than as a confirmed pattern.

    `bb_upper` comes from the prior close (the poller's `bands`), matching
    the stored flag's t-1 band and invariant 3.

    A NaN `day_open` — a quote missing `regularMarketOpen` — returns False.
    "Cannot evaluate" is not "fired", and `_breach` already treats NaN the
    same way on the band side.
    """
    if _isnan(day_open) or _isnan(price):
        return False
    return _breach(price, bands.bb_upper, Bound.UPPER) and price < day_open


@dataclass(frozen=True)
class ReversalState:
    """How far a short-side signal is from being a bear reversal (ADR 117).

    `is_bear_reversal` answers yes or no. This answers *by how much*, which
    is the question a reader actually has when a `confluence_high` fires
    without confirming: MPC on 2026-08-18 fired 6.14 above its band and a
    little above its open, and the yes/no answer alone cannot distinguish
    that from a name trading mid-channel.

    `open_gap_atr` is the one to read. Negative means confirmed - price is
    below the open, which is the reversal. Small positive means the name is
    above its open by a fraction of a day's range and could cross before the
    bell. Large positive means it is running, not reversing.

    Normalised by ATR rather than reported in dollars or percent, because
    the reader is comparing five tickers at once and a $6 gap on MPC and a
    $0.03 gap on WBD are not comparable in any other unit. `atr_14` is
    already on the band the poller holds, so it costs nothing.
    """

    above_band: bool
    confirmed: bool
    band_gap: float | None
    open_gap: float | None
    open_gap_atr: float | None

    @property
    def label(self) -> str:
        """One word for a notification subject or a CSV column."""
        if not self.above_band:
            return "n/a"
        return "confirmed" if self.confirmed else "not_confirmed"


def bull_reversal_state(price: float, day_open: float, bands: Bands) -> ReversalState:
    """ADR 144's mirror of `reversal_state`, reusing the same dataclass.

    **`above_band` means "beyond the band on this signal's own side."** For a
    long-side state that is *below* the lower band, so the field reads True
    when price has broken down through it. Reusing the name rather than
    adding `below_band` keeps one shape for the two sides -- the screener's
    badge, the notification and the CSV all read one set of fields -- and the
    field's meaning is "the band condition holds", which is side-relative by
    nature. `ReversalState.label` works unchanged for the same reason.

    **`open_gap_atr` keeps its sign convention, and so its reading flips.**
    It is always `(price - open) / ATR`. For a bear reversal *negative*
    confirms (price below its open); here *positive* confirms (price above
    it). The alternative -- negating it so "negative is always confirming" --
    would make the same column mean two different things depending on a
    sibling field, which is worse than a sign the reader has to interpret
    once.
    """
    below_band = _breach(price, bands.bb_lower, Bound.LOWER)
    confirmed = is_bull_reversal(price, day_open, bands)

    band_gap = None if _isnan(price) or _isnan(bands.bb_lower) else price - bands.bb_lower
    open_gap = None if _isnan(price) or _isnan(day_open) else price - day_open

    atr = bands.atr_14
    open_gap_atr = None
    if open_gap is not None and not _isnan(atr) and atr > 0:
        open_gap_atr = open_gap / atr

    return ReversalState(
        above_band=below_band,
        confirmed=confirmed,
        band_gap=band_gap,
        open_gap=open_gap,
        open_gap_atr=open_gap_atr,
    )


def reversal_state(price: float, day_open: float, bands: Bands) -> ReversalState:
    """The reversal call and the distances behind it.

    Deliberately not folded into `is_bear_reversal`. That function is the
    *rule* and is compared against the stored `bear_close_above_upper` flag
    by `test_poll_bear_reversal.py`; this one is presentation, and mixing
    them would let a display change alter a signal definition.

    Every distance is `None` when it cannot be computed - a missing open, a
    missing price, a zero ATR - rather than 0.0, which reads as "no gap"
    and is the opposite of "unknown".
    """
    above_band = _breach(price, bands.bb_upper, Bound.UPPER)
    confirmed = is_bear_reversal(price, day_open, bands)

    band_gap = None if _isnan(price) or _isnan(bands.bb_upper) else price - bands.bb_upper
    open_gap = None if _isnan(price) or _isnan(day_open) else price - day_open

    atr = bands.atr_14
    open_gap_atr = None
    if open_gap is not None and not _isnan(atr) and atr > 0:
        open_gap_atr = open_gap / atr

    return ReversalState(
        above_band=above_band,
        confirmed=confirmed,
        band_gap=band_gap,
        open_gap=open_gap,
        open_gap_atr=open_gap_atr,
    )


# The confirmed-reversal wording, unchanged since ADR 108's live half so
# that anything grepping notification history for it keeps matching.
REVERSAL_CONFIRMED_TAG = " [bear reversal: above band, below today's open]"


def _reversal_tag(state: ReversalState) -> str:
    """The subject-line tag for a short-side confluence.

    Three outcomes, never silence. The old version emitted a tag only when
    confirmed, so a near miss and a name running away from its open were
    indistinguishable in the alert list.
    """
    if not state.above_band:
        # `confluence_high` requires an upper-band touch, so reaching here
        # means the price fell back between the touch and the notification.
        # Rare, and worth saying rather than rounding to "not confirmed".
        return " [no reversal: back inside the band]"
    if state.confirmed:
        return REVERSAL_CONFIRMED_TAG
    if state.open_gap_atr is None:
        return " [no reversal: today's open unavailable]"
    return f" [no reversal: {state.open_gap_atr:+.2f} ATR vs today's open]"


def _reversal_body(state: ReversalState, price: float, day_open: float, bands: Bands) -> str:
    """The three numbers the rule compares, always, in one sentence.

    `bb_upper <= price < open` is the rule. Printing all three lets the
    reader apply it themselves, which is the point: the poller reports, and
    the decision to treat a near miss as good enough is the reader's.
    """
    band_part = f"upper band {bands.bb_upper:.2f}, price {price:.2f}"
    if _isnan(day_open):
        return (
            f"{band_part}, today's open unavailable. The reversal rule is "
            "bb_upper <= price < open and cannot be evaluated without it."
        )

    open_part = f"today's open {day_open:.2f}"
    gap = "" if state.open_gap_atr is None else f" ({state.open_gap_atr:+.2f} ATR)"
    if state.confirmed:
        return (
            f"{band_part}, {open_part}{gap}. Above the band and below the "
            "open: the intraday shape of bear_close_above_upper, which only "
            "confirms at the close."
        )
    return (
        f"{band_part}, {open_part}{gap}. Above the band but not below the "
        "open, so the reversal has not formed. It still can before the bell."
    )


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
    population: dict[str, str] | None = None,
) -> int:
    """One tick: quote every ticker, record and notify any new breach.
    Returns the count of new events fired this tick.

    `population` maps ticker to `'trade'` or `'watch'` (ADR 149). Defaulted
    to empty rather than required so existing callers keep working, and a
    missing entry reads as `'trade'` -- which is the pre-ADR-149 behaviour
    and the only safe default, since every ticker the poller saw before this
    was in-trade by construction.
    """
    population = population or {}
    quotes = yahoo.fetch_quotes(tickers)
    if quotes.empty:
        return 0
    today = now.date()
    market_row = _load_market_row(engine, today)
    fired_count = 0

    # ADR 128. Every quoted ticker, before the breach filter -- the partial
    # candle is for the chart, and a chart that only had today's bar for
    # names that happened to fire would be stranger than having none.
    _write_live_bars(engine, quotes, today, run_id)

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

        # ADR 108's live half (user's decision, 2026-08-13). Reported as a
        # tag on an existing signal, never as its own event: the stored
        # `bear_close_above_upper` type is close-confirmed and cannot be
        # decided mid-session, so writing one here would put a row in
        # `events` that tonight's `run_events` might legitimately disagree
        # with. `breach_live` never returns that type for the same reason.
        day_open = float(getattr(row, "day_open", float("nan")))

        for side, side_types in _fired_by_side(fired_types).items():
            signal_type = side_types[0].value
            if _already_fired(engine, chash, ticker, today, signal_type):
                continue

            event_row = _build_live_event_row(
                side,
                side_types,
                ticker,
                price,
                today,
                ind_row,
                market_row,
                chash,
                run_id,
                population.get(ticker, "trade"),
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

            # ADR 117. Every confluence still fires, is still written, and
            # is still notified. What changed on 2026-08-18 is that a
            # short-side confluence now says *where* it sits relative to the
            # reversal rule instead of saying nothing when it misses.
            #
            # The previous version tagged only confirmed reversals, so a
            # near miss and a name running hard away from its open produced
            # byte-identical alerts. MPC that morning fired 6.14 above its
            # band and 0.39 ATR above its open - a reversal that had not
            # happened yet rather than one that was not going to - and
            # nothing in the notification could tell the two apart.
            #
            # `cscan scan --actionable` still filters these out (ADR 111).
            # The poller deliberately does not: it is the surface where the
            # reader makes the call, so it needs the numbers, not a verdict.
            rev = reversal_state(price, day_open, band)
            is_short_confluence = SignalType.CONFLUENCE_HIGH in side_types
            tag = _reversal_tag(rev) if is_short_confluence else ""

            # ADR 149: a watched name says so, and says which reason
            # admitted it. It is not tradeable under the four criteria, and
            # an alert that hides that is the distinction being forgotten at
            # the exact moment it matters.
            watch = _watch_tag(population.get(ticker, "trade"))
            subject = f"{ticker} {signal_type}{tag}{watch}"
            body = (
                f"{ticker} fired {signal_type} at {price:.2f} "
                f"(touch level {event_row['touch_level']}, k_full {ind_row.get('k_full')})."
            )
            if watch:
                body += (
                    f" NOT in the trade universe: watched for {population[ticker]}"
                    " (ADR 149). It passes market cap and the SMA200 slope; the"
                    " remaining criterion is unjudgeable or a pullback, so this"
                    " name trains nothing and carries no cell statistics."
                )
            if is_short_confluence:
                body += " " + _reversal_body(rev, price, day_open, band)
            channels_sent = notify.notify_all(notifiers, subject, body)

            db_io.append(
                engine,
                "signal_reports",
                [
                    {
                        "event_id": event_id,
                        "ticker": ticker,
                        "fired_at": now,
                        "state_json": _state_json(band, ind_row, price, day_open),
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
    config: Config | None = None,
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
    # `config` when the caller has one, never `Config(signals=sp)`.
    #
    # That rebuild dropped every non-`signals` section, so a `config.toml`
    # or `CAPSCAN_*` override of `universe`, `splits`, `indicators`, or
    # `costs` produced a hash here that `run_events` and `run_backtest`
    # would not produce for the identical resolved config. The poller then
    # wrote live rows under an identity no other job used, and they would
    # never be overwritten by the nightly pass or joined by any statistic.
    #
    # Exactly the defect `test_events_backtest_config_agreement.py` was
    # written for after it was found in `run_events` — same rebuild, same
    # dropped sections, different module. Harmless while no override is
    # set, which is why it survived: the two hashes agree on defaults.
    #
    # `cli.poll` already resolves the full config (ADR 091's
    # all-or-nothing contract) and simply was not passing it.
    chash = config_hash(config or Config(signals=sp, exits=ep, stats=stats))
    notifiers = notifiers if notifiers is not None else notify.active_notifiers()

    with run_job(engine, "poll", {"interval": interval}) as report:
        scheduled_runs.record(engine, "poll", run_id=report.run_id, now=now_fn())

        # ADR 149: trade **and** watch. `population` labels the
        # notification and is written to the event row, so a watched name
        # never reaches the study population by omission.
        population = _load_pollable_tickers(engine)
        resolved_tickers = tickers or sorted(population)
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

        watermark = sync.LiveWatermark()

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
                    population,
                )
                report.rows_written += fired
                ticks_completed += 1
                consecutive_failures = 0
                # Heartbeat first, so a serving reader can date this tick
                # even if the push below fails.
                _write_session_row(
                    engine,
                    session_date,
                    started_at,
                    now,
                    ticks_completed,
                    ticks_expected,
                    report.notes,
                )
                watermark = _push_live(
                    engine, chash, session_date, report.run_id, watermark, report
                )
            except Exception as exc:  # DESIGN §4.9: log, sleep, continue
                consecutive_failures += 1
                report.notes = f"tick failure: {exc}"
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    break
            tick += 1
            if max_ticks is not None and tick >= max_ticks:
                break
            sleep_fn(interval)

        _write_session_row(
            engine,
            session_date,
            started_at,
            now_fn(),
            ticks_completed,
            ticks_expected,
            report.notes,
        )
    return report
