"""`get_universe` — membership as of a date, with the criteria that decided it.

Reads `v_universe` when `as_of` is None and `universe` directly otherwise.
The two differ in a way worth stating: `v_universe` is `DISTINCT ON (ticker)
ORDER BY as_of DESC`, so it is *current* membership, while a historical
`as_of` needs the row in force on that date - the latest rebalance at or
before it. Answering a historical question from the current view is how a
survivorship bias gets in (ADR 002), so the two paths are separate queries
rather than one with a shifted predicate.

The five `crit_*` booleans travel with each row. ADR 003 makes membership
the conjunction of criteria that are individually checkable, and a row
saying `in_trade = false` with no reason is a row nobody can audit.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Engine

from capitalscan.core.config import StatsParams, UniverseParams
from capitalscan.handlers import _db, enums
from capitalscan.handlers.types import UniverseResult, UniverseRow
from capitalscan.handlers.validate import validated

CRITERIA_COLUMNS: tuple[str, ...] = (
    "crit_mcap",
    "crit_above_sma200",
    "crit_sma200_slope",
    "crit_rel_return",
    "crit_rev_growth",
)

_CURRENT = f"""
SELECT ticker, name, sector, industry, as_of, in_train, in_trade,
       mcap_usd, mcap_rank, adv_20d_usd, is_active, delisted_on,
       {", ".join(CRITERIA_COLUMNS)}
FROM v_universe
ORDER BY mcap_rank NULLS LAST, ticker
"""

# `DISTINCT ON (u.ticker) ... ORDER BY u.as_of DESC` bounded at `:as_of`:
# the membership row in force on that date, which is the last rebalance at
# or before it. Rebalances are quarterly (ADR 002), so a mid-quarter date
# has no row of its own and must inherit the previous one rather than come
# back empty.
_HISTORICAL = f"""
SELECT * FROM (
    SELECT DISTINCT ON (u.ticker)
           u.ticker, t.name, t.sector, t.industry, u.as_of, u.in_train, u.in_trade,
           u.mcap_usd, u.mcap_rank, u.adv_20d_usd, t.is_active, t.delisted_on,
           {", ".join("u." + c for c in CRITERIA_COLUMNS)}
    FROM universe u
    JOIN tickers t ON t.ticker = u.ticker
    WHERE u.as_of <= :as_of
    ORDER BY u.ticker, u.as_of DESC
) s
ORDER BY s.mcap_rank NULLS LAST, s.ticker
"""


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _to_row(row: dict[str, Any]) -> UniverseRow:
    return UniverseRow(
        ticker=row["ticker"],
        name=row.get("name"),
        sector=row.get("sector"),
        industry=row.get("industry"),
        as_of=row.get("as_of"),
        in_train=row.get("in_train"),
        in_trade=row.get("in_trade"),
        mcap_usd=_f(row.get("mcap_usd")),
        mcap_rank=None if row.get("mcap_rank") is None else int(row["mcap_rank"]),
        adv_20d_usd=_f(row.get("adv_20d_usd")),
        criteria={name: row.get(name) for name in CRITERIA_COLUMNS},
        is_active=row.get("is_active"),
        delisted_on=row.get("delisted_on"),
    )


def _fetch(engine: Engine, as_of: date | None) -> list[dict[str, Any]]:
    if as_of is None:
        return _db.rows(engine, _CURRENT)
    return _db.rows(engine, _HISTORICAL, {"as_of": as_of})


def get_universe(
    as_of: date | None = None,
    universe: str | None = None,
    engine: Engine | None = None,
    sp: StatsParams | None = None,
    up: UniverseParams | None = None,
) -> UniverseResult:
    """Every ticker with a membership row, current or as of a date.

    **`limit` does not apply here and that is deliberate.** ADR 074 caps
    row-returning tools at 200 to bound a response; the universe is the
    denominator of every breadth statistic (ADR 104) and a truncated
    universe silently changes what a percentage means. It is also small and
    bounded by construction - a few hundred mega-caps, not a table that
    grows with time.

    `universe=None` returns every row with both flags, so a caller can see
    the train population and the trade subset in one pass. Passing `train`
    or `trade` filters to that membership.
    """
    sp = sp or StatsParams()
    up = up or UniverseParams()
    chosen = enums.parse_universe(universe) if universe is not None else None

    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    _, last_bar = _db.bar_window(engine)

    found = _fetch(engine, as_of)
    rows = tuple(_to_row(r) for r in found)
    if chosen == "train":
        rows = tuple(r for r in rows if r.in_train)
    elif chosen == "trade":
        rows = tuple(r for r in rows if r.in_trade)

    # Counted over the *returned* rows, so a filtered result's counts
    # describe what it contains rather than what the table holds. A
    # `n_trade` that stayed at the unfiltered total while `rows` shrank
    # would be the kind of number a reader divides by.
    return validated(
        UniverseResult(
            as_of=as_of or (rows[0].as_of if rows else None),
            rows=rows,
            n_train=sum(1 for r in rows if r.in_train),
            n_trade=sum(1 for r in rows if r.in_trade),
            meta=_db.build_meta(engine, config_hash=config_hash, as_of=last_bar),
        ),
        sp,
    )
