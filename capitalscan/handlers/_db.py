"""Database access shared by the seven handlers, and nothing else.

This is the only module in `handlers/` that opens a connection. The tool
modules import `rows`, `resolve_config_hash`, and `build_meta` from here and
hold their own SQL; the split is so a unit test can stub one handler's fetch
without stubbing the engine, and so there is exactly one place that decides
what `as_of` and `staleness_days` mean.

**Sessions 16, 17, and 18 may not import this module.** Their gates assert
it: an MCP tool or a web route that reaches the database directly has
bypassed the validator, and the validator is the reason the layer exists.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Engine, text

from capitalscan.core.config import MonitoringThresholds
from capitalscan.handlers.errors import NotConfigured
from capitalscan.handlers.types import Meta

# The GUC every config-scoped query reads. Named once here rather than
# spelled into seven queries: `compute.scan` and `v_events` already read the
# same setting, and invariant 5b's "no second config-selection mechanism"
# applies to the serving layer too.
CONFIG_HASH_GUC = "capitalscan.default_config_hash"


def engine_or_default(engine: Engine | None) -> Engine:
    # Imported lazily so that importing `handlers` does not require a
    # DATABASE_URL_RESEARCH in the environment. The type-only tests and the
    # validator tests import this package and must run in CI's fast tier,
    # which has no database at all.
    if engine is not None:
        return engine
    from capitalscan.jobs import db_io

    return db_io.get_engine()


def rows(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every read in this layer. Returns plain dicts, never a DataFrame.

    pandas is the right shape for the compute path, where a column is
    operated on as a unit. It is the wrong shape here: a handler builds one
    frozen dataclass per row, and going through a DataFrame would coerce
    every integer column containing a null into `float64` and hand
    `n_eff=30.0` to a field annotated `int | None`.
    """
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]


def resolve_config_hash(engine: Engine) -> str:
    """The config whose events and statistics this layer serves.

    Raises rather than returning empty when the GUC is unset. `compute.scan`
    returns an empty frame in that state, which is right for a batch job and
    wrong here: a screener showing zero rows every day looks like a quiet
    market, and the operator never learns the database was never configured.
    """
    found = rows(engine, f"SELECT current_setting('{CONFIG_HASH_GUC}', true) AS chash")
    chash = found[0]["chash"] if found else None
    if not chash:
        raise NotConfigured(
            f"{CONFIG_HASH_GUC} is unset on this database, so no config's "
            "events or statistics can be selected. Set it with "
            "ALTER DATABASE <db> SET " + CONFIG_HASH_GUC + " = '<hash>'."
        )
    return str(chash)


def bar_window(engine: Engine) -> tuple[date | None, date | None]:
    """First and last ingested daily bar date, across all tickers.

    The window every date argument validates against (ADR 074). Daily only:
    hourly bars cover 60 days at a time and would report a window that
    excludes most of the history a caller can legitimately ask about.
    """
    found = rows(
        engine,
        "SELECT min(ts)::date AS first_ts, max(ts)::date AS last_ts "
        "FROM bars WHERE interval = '1d'",
    )
    if not found:
        return None, None
    return found[0]["first_ts"], found[0]["last_ts"]


def trading_days_since(engine: Engine, since: date | None, until: date | None = None) -> int | None:
    """Sessions strictly after `since` and on or before `until`.

    Trading days, not calendar days, and the distinction is not pedantry: a
    Monday-morning query against Friday's close is three calendar days stale
    and zero sessions stale. Counting calendar days would raise the staleness
    banner every Monday and over every holiday, which trains the reader to
    dismiss it - and a banner that is always on is a banner that is off.
    """
    if since is None:
        return None
    # `market_date()`, not `CURRENT_DATE` (ADR 119). The database runs
    # `Etc/UTC`, so between 00:00 UTC and midnight ET the two differ by a
    # day and this over-counts by one session. Measured live at 2026-08-19
    # 03:06 UTC: 2 sessions reported against a true 1, with the banner
    # firing above `stale_after_days` = 2 -- so it would have raised a full
    # day early, which is the failure mode a staleness banner has to avoid.
    found = rows(
        engine,
        "SELECT count(*) AS n FROM trading_days "
        "WHERE d > :since AND d <= COALESCE(:until, public.market_date())",
        {"since": since, "until": until},
    )
    return int(found[0]["n"]) if found else None


def build_meta(
    engine: Engine,
    config_hash: str | None = None,
    run_id: str | None = None,
    split: str | None = None,
    as_of: date | None = None,
    thresholds: MonitoringThresholds | None = None,
) -> Meta:
    """`Meta` for one result, with staleness measured off the data.

    `as_of` defaults to the last ingested daily bar rather than to today.
    DESIGN §11.2's staleness banner is a statement about the *database*, and
    a `Meta` that read the clock for both ends would report zero staleness on
    a database that stopped ingesting in March.
    """
    thresholds = thresholds or MonitoringThresholds()
    if as_of is None:
        _, as_of = bar_window(engine)
    stale_days = trading_days_since(engine, as_of)
    return Meta(
        config_hash=config_hash or resolve_config_hash(engine),
        as_of=as_of,
        staleness_days=stale_days,
        run_id=run_id,
        split=split,
        stale=stale_days is not None and stale_days > thresholds.stale_after_days,
    )
