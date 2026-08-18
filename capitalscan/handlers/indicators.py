"""`get_indicators` — a daily indicator series for one ticker.

The tool the ticker chart is built on (session 17). Reads `indicators`
joined to `bars`, daily interval only.

**`fields` is a closed set, checked against the table.** A caller passing an
unknown field gets an `InvalidEnum` naming the valid ones rather than a
Postgres `column does not exist`, and - the part that matters - the field
names are interpolated into the SELECT list, so an unchecked value would be
an injection point. That is why this module has an allowlist and the others
do not: it is the only handler where a caller's string reaches the query
text rather than a bound parameter.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Engine

from capitalscan.core.config import StatsParams
from capitalscan.handlers import _db, enums
from capitalscan.handlers.errors import InvalidEnum
from capitalscan.handlers.types import IndicatorPoint, IndicatorSeries
from capitalscan.handlers.validate import validated

# Everything a chart or an explanation can ask for, and nothing that would
# let a caller read a column this layer has not thought about. `close`,
# `open`, `high`, `low`, and `volume` come from `bars`; the rest from
# `indicators`.
#
# **Split-adjusted `close`, not `adj_close`.** CLAUDE.md's price-series
# table: indicators and live band comparisons are computed on the
# split-adjusted series, and returns are measured on the total-return one.
# A chart that drew bands from `close` and a price line from `adj_close`
# would show the price leaving the band on every dividend date.
BAR_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
INDICATOR_FIELDS: tuple[str, ...] = (
    "bb_lower",
    "bb_mid",
    "bb_upper",
    "bb_pctb",
    "bb_width",
    "bb_width_pct",
    "k_full",
    "d_full",
    "k_fast",
    "k_cross_up",
    "k_cross_down",
    "sma_200",
    "sma200_slope_60",
    "atr_14",
    "rv_20d",
    "rv_pct_252d",
    "vol_z_20d",
    "dd_52w",
    "days_to_earnings",
)

# The chart default. Both `%K` series, because ADR 110 made the agreement
# between them part of the signal definition: the raw `k_fast` is the
# trigger and the smoothed `k_full` must agree within
# `SignalParams.fast_agreement_tol`. A stochastic panel drawing one of them
# is drawing half the rule.
DEFAULT_FIELDS: tuple[str, ...] = (
    "close",
    "bb_lower",
    "bb_mid",
    "bb_upper",
    "k_fast",
    "k_full",
    "volume",
)

ALL_FIELDS: tuple[str, ...] = BAR_FIELDS + INDICATOR_FIELDS


def parse_fields(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if values is None:
        return DEFAULT_FIELDS
    if len(values) == 0:
        raise InvalidEnum(
            "fields=[] selects no series. Pass None for the chart default, "
            f"or one or more of: {', '.join(ALL_FIELDS)}"
        )
    for value in values:
        if value not in ALL_FIELDS:
            raise InvalidEnum(
                f"fields entry {value!r} is not an indicator or bar column. "
                f"Allowed: {', '.join(ALL_FIELDS)}"
            )
    # Deduplicated, order preserved. A repeated field is a caller mistake
    # with no consequence worth an exception, and duplicating the column in
    # the SELECT list would make the row dict shorter than the field tuple.
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)


def _select_list(fields: tuple[str, ...]) -> str:
    # Every name here has already been checked against `ALL_FIELDS`, so the
    # interpolation is over a closed set rather than over caller input.
    return ", ".join(f"b.{name}" if name in BAR_FIELDS else f"i.{name}" for name in fields)


def _fetch(
    engine: Engine, fields: tuple[str, ...], params: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    return _db.rows(
        engine,
        f"SELECT i.ts, {_select_list(fields)} "
        "FROM indicators i "
        "JOIN bars b ON b.ticker = i.ticker AND b.ts = i.ts AND b.interval = i.interval "
        "WHERE i.ticker = :ticker AND i.interval = '1d' "
        "AND (:start IS NULL OR i.ts >= :start) "
        "AND (:end IS NULL OR i.ts <= :end) "
        f"ORDER BY i.ts LIMIT {int(limit)}",
        params,
    )


def _value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        # `k_cross_up` is a boolean and `IndicatorPoint.values` is numeric.
        # 1.0/0.0 rather than a separate map: a chart plots a crossover as a
        # marker at a level, and a second dict keyed differently would make
        # every consumer branch on the field name.
        return 1.0 if raw else 0.0
    return float(raw)


def get_indicators(
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    fields: list[str] | None = None,
    limit: int | None = None,
    engine: Engine | None = None,
    sp: StatsParams | None = None,
) -> IndicatorSeries:
    """A daily series for one ticker over `[start, end]`.

    A ticker with no rows returns an empty `points` tuple and a populated
    `meta`, not an error. A delisted name is the common case for this, and
    "this ticker has no data in the requested window" is a legitimate
    answer that a raise would turn into an outage on the ticker page.

    `limit` is ADR 074's cap, and it bites harder here than anywhere else: a
    fifteen-year daily series is ~3,700 rows and the cap is 200. Callers
    wanting a full chart page a window at a time, which is what a chart does
    anyway.
    """
    sp = sp or StatsParams()
    chosen = parse_fields(fields)
    limit_n = enums.clamp_limit(limit)

    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    first_bar, last_bar = _db.bar_window(engine)
    enums.check_date_window(start, first_bar, last_bar)
    enums.check_date_window(end, first_bar, last_bar)

    found = _fetch(
        engine,
        chosen,
        {"ticker": ticker.upper(), "start": start, "end": end},
        limit_n,
    )
    points = tuple(
        IndicatorPoint(
            ts=row["ts"] if isinstance(row["ts"], date) else row["ts"].date(),
            values={name: _value(row.get(name)) for name in chosen},
        )
        for row in found
    )
    return validated(
        IndicatorSeries(
            ticker=ticker.upper(),
            fields=chosen,
            points=points,
            meta=_db.build_meta(engine, config_hash=config_hash, as_of=last_bar),
        ),
        sp,
    )
