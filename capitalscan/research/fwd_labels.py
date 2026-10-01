"""Fill `events.fwd_ret_{h}d` nightly, for events the backtest has not reached.

    fwd_ret_h = adj_close[entry bar + h] / adj_close[entry bar] - 1

**Why this exists (2026-09-30).** `fwd_ret_*d` is written only by
`run_backtest`, through `research.enrich`. `nightly`'s backtest step is
scoped to tickers holding an open `next_open` position, a handful a night,
so every other ticker's value waited for Saturday's `weekly`. `cscan
outcomes` requires `fwd_ret_5d`, and so the forward log resolved in one
weekly batch: 0 predictions on eight consecutive runs to 2026-09-30, with
432 sitting on events whose `peak_ret_5d` was already written.

**The backtest stays the authority; this only fills NULLs.** `COALESCE`
keeps any value already stored, and the arithmetic is `core.returns.
forward_returns` restated in SQL: total-return `adj_close` (DESIGN §2.2),
anchored on the entry bar, unconditional on side and on whether the trade
exited. Measured on `wivie` before this was written, over 833k events from
2025-09-01: 99.97% of stored values reproduce to six decimals, and the
rest are rows whose `adj_close` was revised after the backtest wrote them.

**Completeness, not partial windows.** A horizon is written only when the
bar `h` sessions after the entry bar exists. Nothing is filled or
interpolated (invariant 4): a missing bar leaves NULL for a later run.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

__all__ = ["backfill_fwd_returns", "fwd_ret_fill_sql"]


def fwd_ret_fill_sql(horizons: tuple[int, ...]) -> str:
    """The UPDATE statement, built from `StatsParams.fwd_ret_horizons`."""
    if not horizons:
        raise ValueError("fwd_ret_fill_sql needs at least one horizon")
    any_null = " OR ".join(f"fwd_ret_{h}d IS NULL" for h in horizons)
    calcs = ",\n               ".join(
        f"max(round(bh.adj_close / b0.adj_close - 1, 6)) FILTER (WHERE bh.rn = b0.rn + {h}) AS f{h}"
        for h in horizons
    )
    sets = ",\n        ".join(
        f"fwd_ret_{h}d = COALESCE(e.fwd_ret_{h}d, calc.f{h})" for h in horizons
    )
    changes = " OR ".join(f"(e.fwd_ret_{h}d IS NULL AND calc.f{h} IS NOT NULL)" for h in horizons)
    return f"""
    WITH pending AS (
        SELECT id, ticker, entry_date
          FROM events
         WHERE config_hash = :config_hash
           AND entry_date IS NOT NULL
           -- The population `predict` scores and `peak_labels` labels
           -- (ADR 200). Rows in neither universe wait for `weekly`.
           AND (in_trade OR in_watch)
           AND ({any_null})
    ),
    b AS (
        -- Daily bars are stamped in UTC; the date is the session.
        SELECT ticker, (ts AT TIME ZONE 'UTC')::date AS d, adj_close,
               row_number() OVER (PARTITION BY ticker ORDER BY ts) AS rn
          FROM bars
         WHERE "interval" = '1d'
           AND ticker IN (SELECT DISTINCT ticker FROM pending)
           AND ts >= (SELECT min(entry_date) FROM pending)
    ),
    calc AS (
        SELECT p.id,
               {calcs}
          FROM pending p
          JOIN b b0 ON b0.ticker = p.ticker AND b0.d = p.entry_date AND b0.adj_close <> 0
          JOIN b bh ON bh.ticker = p.ticker
                   AND bh.rn BETWEEN b0.rn + 1 AND b0.rn + {max(horizons)}
         GROUP BY p.id
    )
    UPDATE events e SET
        {sets}
      FROM calc
     WHERE e.id = calc.id
       AND ({changes})
    """


def backfill_fwd_returns(engine: Engine, config_hash: str, horizons: tuple[int, ...]) -> int:
    """Fill missing `fwd_ret_{h}d` for one `config_hash`. Returns rows updated.

    Idempotent: a second run finds nothing to change until another window
    closes.
    """
    with engine.begin() as conn:
        result = conn.execute(text(fwd_ret_fill_sql(horizons)), {"config_hash": config_hash})
    return int(result.rowcount)
