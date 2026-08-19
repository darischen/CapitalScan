"""`screen_signals` — what fired, and only on request what it historically meant.

Reads `v_screen` or `v_screen_live`, chosen by `grain`. It does not rebuild
what those views already decide:

- ADR 100's `config_hash` predicate, taken from the `capitalscan.default_
  config_hash` GUC rather than guessed.
- ADR 105's `arm = 'signal'` predicate, so the control and benchmark arms
  cannot leak into a screener row.
- ADR 107's pooled-over-`signal_strength` cell selection.
- `is_cluster_head AND entry_kind = 'next_open'`, which is the same grain
  every Phase 4 statistics query used. `v_screen_live` is the `touch`
  counterpart -- see `enums.GRAINS` for why the caller picks.

**The default is the event feed (ADR 114).** `with_stats=False` returns
rows with `stats=None`. ADR 112 measured zero cells surviving FDR
correction and 100 of 224 train cells suppressed, so a default view with
four statistical columns would be blank or near-blank on nearly every row,
every day. Four always-empty columns teach a reader to skip the row, and
the row is the part that carries information. The statistics are one
argument away, and arrive whole when they arrive.

**Whole, or not at all.** When `with_stats=True`, a row's `stats` is a
`CellStats` carrying `p_hit`, `baseline`, `edge`, `n_eff`, the interval, and
the q-value together - or a `Suppressed` carrying the stored reason. There
is no partial state, and `v_screen`'s own nulled-on-suppression columns are
deliberately not used for this: they cannot express the difference between
"suppressed" and "not measured", and the union can.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Engine

from capitalscan.core.config import StatsParams
from capitalscan.handlers import _db, enums
from capitalscan.handlers.stats import _CELL_COLUMNS, to_cell_stats
from capitalscan.handlers.types import CellStats, ScreenResult, ScreenRow, Suppressed
from capitalscan.handlers.validate import validated

_FEED_COLUMNS = """
    s.ticker, s.signal_date, s.signal_type, s.signal_types_all, s.signal_strength,
    s.bb_pctb, s.k_full, s.k_fast, s.k_cross_up,
    s.dd_52w, s.dd_bucket, s.above_sma200, s.cofire_count, s.sector, s.cell_id
"""

# `v_universe` is `DISTINCT ON (ticker) ... ORDER BY as_of DESC`, so this
# joins each ticker's *most recent* membership row rather than the one in
# force on the signal date. That is the right choice for a screener, which
# answers "what should I look at now": a name that left the trade universe
# last quarter should stop appearing today even though its historical
# events remain valid. `get_events` makes no such filter, which is why a
# ticker can be absent here and present there.
# The view is chosen by `grain`, never interpolated from caller input:
# `enums.parse_grain` maps to a key here, so the only two strings that can
# reach this format are the two written below.
_VIEWS = {"next_open": "v_screen", "touch": "v_screen_live"}

_BASE = """
FROM {view} s
JOIN v_universe u ON u.ticker = s.ticker
WHERE {predicates}
"""


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _fetch_feed(
    engine: Engine, predicates: str, params: dict[str, Any], limit: int, grain: str
) -> tuple[list[dict[str, Any]], int]:
    base = _BASE.format(view=_VIEWS[grain], predicates=predicates)
    total = _db.rows(engine, f"SELECT count(*) AS n {base}", params)
    found = _db.rows(
        engine,
        # Ordered by cofire first: on a day when thirty names fire, the ones
        # firing alongside the most other names are the market-wide move, and
        # a screener truncated at `limit` should not cut those first. Ticker
        # breaks the tie so the order is total and the determinism gate holds.
        f"SELECT {_FEED_COLUMNS} {base} "
        "ORDER BY s.signal_date DESC, s.cofire_count DESC NULLS LAST, s.ticker "
        f"LIMIT {int(limit)}",
        params,
    )
    return found, int(total[0]["n"]) if total else 0


def _fetch_cells(engine: Engine, config_hash: str, cell_ids: list[str]) -> list[dict[str, Any]]:
    if not cell_ids:
        return []
    return _db.rows(
        engine,
        f"SELECT {_CELL_COLUMNS} FROM cell_stats "
        "WHERE config_hash = :config_hash AND arm = 'signal' "
        "AND cell_id = ANY(:cell_ids)",
        {"config_hash": config_hash, "cell_ids": cell_ids},
    )


def _to_row(row: dict[str, Any], stats: CellStats | Suppressed | None) -> ScreenRow:
    signal_type = row["signal_type"]
    return ScreenRow(
        ticker=row["ticker"],
        signal_date=row["signal_date"],
        signal_type=signal_type,
        signal_types_all=tuple(row.get("signal_types_all") or ()),
        signal_strength=int(row.get("signal_strength") or 0),
        # `v_screen` carries no `side` column. Deriving it from the signal
        # type is not a shortcut: `core.signals.detect` emits one hit per
        # side and never mixes long and short types on one row, so the type
        # determines the side exactly (see `test_scan_actionable.py`).
        side=enums.side_for_signal_type(signal_type),
        sector=row.get("sector"),
        bb_pctb=_f(row.get("bb_pctb")),
        k_full=_f(row.get("k_full")),
        k_fast=_f(row.get("k_fast")),
        k_cross_up=row.get("k_cross_up"),
        dd_52w=_f(row.get("dd_52w")),
        dd_bucket=row.get("dd_bucket"),
        above_sma200=row.get("above_sma200"),
        cofire_count=None if row.get("cofire_count") is None else int(row["cofire_count"]),
        cell_id=row.get("cell_id"),
        stats=stats,
    )


def screen_signals(
    date_: date | None = None,
    signal_types: list[str] | None = None,
    universe: str = "trade",
    dd_bucket: str | None = None,
    min_strength: int | None = None,
    limit: int | None = None,
    with_stats: bool = False,
    grain: str = "next_open",
    engine: Engine | None = None,
    sp: StatsParams | None = None,
) -> ScreenResult:
    """Events on one date, or the most recent ones when `date_` is None.

    A date with nothing on it returns an empty `ScreenResult` with populated
    `meta`, never an error and never a bare empty list. DESIGN §11.2: "empty
    state matters more than usual, because most days nothing fires." An
    empty result still has to say which config it queried and how stale the
    database is, or the reader cannot tell "nothing fired" from "nothing
    was ingested".

    `grain` selects the feed. `next_open` (the default) reads the
    backtested `v_screen` and stops at the last `cscan backtest`; `touch`
    reads the poller's `v_screen_live` and reaches today. `meta.as_of` and
    the staleness it carries are the same either way -- they describe the
    *database*, not the feed -- so a caller on the default grain must read
    `signal_date` to know how far back the newest row is.
    """
    sp = sp or StatsParams()
    universe = enums.parse_universe(universe)
    grain = enums.parse_grain(grain)
    types = enums.parse_signal_types(signal_types)
    limit_n = enums.clamp_limit(limit)

    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    first_bar, last_bar = _db.bar_window(engine)
    enums.check_date_window(date_, first_bar, last_bar)

    predicates = ["u.in_trade" if universe == "trade" else "u.in_train"]
    params: dict[str, Any] = {}
    if date_ is not None:
        predicates.append("s.signal_date = :signal_date")
        params["signal_date"] = date_
    if types is not None:
        # `signal_types_all`, not `signal_type`. The latter carries only the
        # most specific type per ADR 057's ranking, so a filter on it drops
        # every `confluence_high` bar that also closed above the band - the
        # exact class of row the caller asking for `confluence_high` wants.
        predicates.append("s.signal_types_all && :signal_types")
        params["signal_types"] = list(types)
    if dd_bucket is not None:
        predicates.append("s.dd_bucket = :dd_bucket")
        params["dd_bucket"] = enums.parse_dd_bucket(dd_bucket, sp)
    if min_strength is not None:
        predicates.append("s.signal_strength >= :min_strength")
        params["min_strength"] = int(min_strength)

    found, total = _fetch_feed(engine, " AND ".join(predicates), params, limit_n, grain)
    meta = _db.build_meta(engine, config_hash=config_hash, as_of=last_bar)

    by_cell: dict[str, CellStats | Suppressed] = {}
    if with_stats:
        cell_ids = sorted({r["cell_id"] for r in found if r.get("cell_id")})
        for cell_row in _fetch_cells(engine, config_hash, cell_ids):
            by_cell[cell_row["cell_id"]] = to_cell_stats(cell_row, meta, sp)

    rows = tuple(
        _to_row(r, by_cell.get(r["cell_id"]) if with_stats and r.get("cell_id") else None)
        for r in found
    )
    return validated(
        ScreenResult(
            rows=rows,
            total_matched=total,
            limit=limit_n,
            with_stats=with_stats,
            meta=meta,
        ),
        sp,
    )
