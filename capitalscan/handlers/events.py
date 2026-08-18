"""`get_events` — one ticker's event history, cluster heads by default.

`cluster_head_only=True` is the default and matches every statistics query
in Phase 4. ADR 054 clusters events within a gap window and counts the head
once; a caller who gets all members by default sees a run of five
consecutive `bb_lower_touch` days as five independent observations, which is
precisely the double counting the clustering exists to remove.

Reads `v_events`, which carries ADR 100's `config_hash` predicate. The view
exposes every split including `holdout`, because it is also the batch
layer's read path - so this handler bounds `split_key` itself and refuses
`holdout` through `enums.parse_split`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Engine

from capitalscan.core.config import SplitParams, StatsParams
from capitalscan.handlers import _db, enums
from capitalscan.handlers.types import EventList, EventRow
from capitalscan.handlers.validate import validated

_EVENT_COLUMNS = """
    id, ticker, signal_date, signal_type, signal_types_all, signal_strength,
    cluster_id, seq_in_cluster, is_cluster_head,
    bb_pctb, k_full, k_fast, dd_52w, dd_bucket, above_sma200,
    entry_kind, entry_date, entry_price,
    exit_date, exit_price, exit_reason, holding_days,
    gross_ret, net_ret, mfe, mae, era, split_key
"""


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _i(value: Any) -> int | None:
    return None if value is None else int(value)


def _to_row(row: dict[str, Any]) -> EventRow:
    return EventRow(
        id=int(row["id"]),
        ticker=row["ticker"],
        signal_date=row["signal_date"],
        signal_type=row["signal_type"],
        signal_types_all=tuple(row.get("signal_types_all") or ()),
        signal_strength=int(row.get("signal_strength") or 0),
        side=enums.side_for_signal_type(row["signal_type"]),
        cluster_id=_i(row.get("cluster_id")),
        seq_in_cluster=_i(row.get("seq_in_cluster")),
        is_cluster_head=row.get("is_cluster_head"),
        bb_pctb=_f(row.get("bb_pctb")),
        k_full=_f(row.get("k_full")),
        k_fast=_f(row.get("k_fast")),
        dd_52w=_f(row.get("dd_52w")),
        dd_bucket=row.get("dd_bucket"),
        above_sma200=row.get("above_sma200"),
        entry_kind=row.get("entry_kind"),
        entry_date=row.get("entry_date"),
        entry_price=_f(row.get("entry_price")),
        exit_date=row.get("exit_date"),
        exit_price=_f(row.get("exit_price")),
        exit_reason=row.get("exit_reason"),
        holding_days=_i(row.get("holding_days")),
        gross_ret=_f(row.get("gross_ret")),
        net_ret=_f(row.get("net_ret")),
        mfe=_f(row.get("mfe")),
        mae=_f(row.get("mae")),
        era=row.get("era"),
        split_key=row.get("split_key"),
    )


def _fetch(
    engine: Engine, predicates: str, params: dict[str, Any], limit: int
) -> tuple[list[dict[str, Any]], int]:
    where = f"FROM v_events WHERE {predicates}"
    total = _db.rows(engine, f"SELECT count(*) AS n {where}", params)
    found = _db.rows(
        engine,
        f"SELECT {_EVENT_COLUMNS} {where} "
        "ORDER BY signal_date DESC, ticker, signal_type "
        f"LIMIT {int(limit)}",
        params,
    )
    return found, int(total[0]["n"]) if total else 0


def get_events(
    ticker: str | None = None,
    start: date | None = None,
    end: date | None = None,
    signal_types: list[str] | None = None,
    cluster_head_only: bool = True,
    entry_kind: str = "next_open",
    split: str | None = None,
    limit: int | None = None,
    engine: Engine | None = None,
    sp: StatsParams | None = None,
    splits: SplitParams | None = None,
) -> EventList:
    """Events matching the filters, newest first.

    `split=None` returns train and validate together and **never** holdout.
    The predicate is written as membership in `enums.SPLITS` rather than as
    `split_key <> 'holdout'`: an inequality admits any value a later
    migration adds to the check constraint, and membership admits only what
    this layer has decided it may serve.

    `entry_kind` defaults to `next_open`, which is the kind every Phase 4
    statistic was measured on. The `events` grain is
    `(config_hash, ticker, signal_date, signal_type, entry_kind)`, so
    omitting the filter would return one signal four times.
    """
    sp = sp or StatsParams()
    splits = splits or SplitParams()
    types = enums.parse_signal_types(signal_types)
    entry_kind = enums.parse_entry_kind(entry_kind)
    limit_n = enums.clamp_limit(limit)

    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    first_bar, last_bar = _db.bar_window(engine)
    enums.check_date_window(start, first_bar, last_bar)
    enums.check_date_window(end, first_bar, last_bar)

    predicates = ["entry_kind = :entry_kind", "split_key = ANY(:splits)"]
    params: dict[str, Any] = {"entry_kind": entry_kind}
    if split is not None:
        chosen = enums.parse_split(split)
        params["splits"] = [chosen]
        # Belt and braces, the same pairing `test_split_leakage.py` applies:
        # the label and the date bounds must agree, so one mislabelled row
        # cannot cross a boundary on its own.
        low, high = enums.split_bounds(chosen, splits)
        predicates.append("signal_date BETWEEN :split_low AND :split_high")
        params["split_low"] = low
        params["split_high"] = high
    else:
        params["splits"] = list(enums.SPLITS)
    if ticker is not None:
        predicates.append("ticker = :ticker")
        params["ticker"] = ticker.upper()
    if start is not None:
        predicates.append("signal_date >= :start")
        params["start"] = start
    if end is not None:
        predicates.append("signal_date <= :end")
        params["end"] = end
    if types is not None:
        predicates.append("signal_types_all && :signal_types")
        params["signal_types"] = list(types)
    if cluster_head_only:
        predicates.append("is_cluster_head")

    found, total = _fetch(engine, " AND ".join(predicates), params, limit_n)
    meta = _db.build_meta(engine, config_hash=config_hash, as_of=last_bar, split=split)
    return validated(
        EventList(
            ticker=ticker.upper() if ticker else None,
            rows=tuple(_to_row(r) for r in found),
            total_matched=total,
            limit=limit_n,
            cluster_head_only=cluster_head_only,
            meta=meta,
        ),
        sp,
    )


def last_fire(
    engine: Engine | None = None,
    before: date | None = None,
    sp: StatsParams | None = None,
) -> EventRow | None:
    """The most recent event across all tickers, or None if there are none.

    DESIGN §11.2's empty state reads `No signals today. Last fire: TSM, 3
    days ago`. That sentence needs one row, and building it from a
    `get_events(limit=1)` call at the route layer would put a query decision
    in a template. It lives here instead.
    """
    result = get_events(end=before, limit=1, engine=engine, sp=sp)
    return result.rows[0] if result.rows else None
