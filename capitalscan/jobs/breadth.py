"""`cscan breadth` — universe breadth onto `market_days`, and the ranking gate.

ADR 176. `p_touch` is calibrated in every market regime and only *ranks* in
some of them. Breadth is the variable that separates the two, so it has to
be a stored daily number rather than something recomputed inside whichever
query happens to need it.

**What breadth is here.** The fraction of the daily universe whose 20-day
average (`indicators.bb_mid`) sits at or above its 200-day (`sma_200`).
`bb_mid` rather than a close price because `indicators` has no close
column, and the 20-over-200 crossover is the standard trend measure anyway.

**Why it leads.** Measured 2026-09-07, breadth ran 0.679 in November 2021
and 0.584 by December while the index was still making highs and would not
cross its own 200-day average until January. Mega-caps held the index up
while the median name had already turned. `cofire_count` is same-day
*signal* breadth; this is *trend* breadth, and nothing else measures it.

**One statement per run, whole history.** The aggregate is a single pass
over `indicators` and takes about four seconds, so there is no incremental
path and no watermark to get wrong. Re-running is a no-op that produces
identical values.

**Invariant 1.** The threshold lives in `core.config.ServingParams`
(`breadth_rank_floor`) and the classification in `core.breadth`; this
module only moves rows.

**`ServingParams`, not a `Config` section, and that was a real bug first.**
`jobs.config.config_hash` hashes `asdict(config)`, so putting the floor on
`StatsParams` moved the hash off `0523841076f47293` and would have orphaned
every `events`, `predictions` and `cell_stats` row keyed on it. The gate
changes what a surface does with a probability and changes no probability,
label or backtest result, so it belongs with the deployment constants.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

from capitalscan.jobs import db_io, ingest

#: Sessions used for the trend term. Sixty is one quarter, long enough that
#: a single bad week does not flip the sign and short enough to turn inside
#: a topping process -- the state ADR 176 is about.
TREND_LOOKBACK = 60

COMPUTE_SQL = f"""
WITH b AS (
  SELECT ts, avg((bb_mid >= sma_200)::int)::float8 AS breadth
    FROM indicators
   WHERE "interval" = '1d' AND sma_200 IS NOT NULL AND bb_mid IS NOT NULL
   GROUP BY ts
), t AS (
  SELECT ts, breadth,
         breadth - lag(breadth, {TREND_LOOKBACK}) OVER (ORDER BY ts) AS chg
    FROM b
)
UPDATE market_days m
   SET breadth_ma_above = t.breadth,
       breadth_chg_60d  = t.chg
  FROM t
 WHERE m.ts = t.ts
"""


@dataclass
class BreadthReport:
    """What one `cscan breadth` run wrote."""

    rows_written: int = 0
    latest: float | None = None
    latest_chg: float | None = None
    gate_open: bool | None = None

    def summary(self) -> str:
        if self.latest is None:
            return f"{self.rows_written} sessions updated; no current reading"
        state = "OPEN (ranking usable)" if self.gate_open else "CLOSED (ranking unreliable)"
        chg = "n/a" if self.latest_chg is None else f"{self.latest_chg:+.3f}"
        return (
            f"{self.rows_written} sessions updated; latest breadth {self.latest:.3f} "
            f"({chg} over {TREND_LOOKBACK}d) -- gate {state}"
        )


LATEST_SQL = """
SELECT breadth_ma_above, breadth_chg_60d
  FROM market_days
 WHERE breadth_ma_above IS NOT NULL
 ORDER BY ts DESC LIMIT 1
"""


def run_breadth(engine: Engine | None = None) -> BreadthReport:
    """Recompute breadth for every session and report the current gate state.

    Idempotent and total: it rewrites the whole history rather than
    appending, because the aggregate is cheap and a partial update would
    need a watermark that could silently fall behind after a bars backfill.
    """
    from capitalscan.core.breadth import ranking_gate_open
    from capitalscan.core.config import ServingParams

    engine = engine or db_io.get_engine()
    report = BreadthReport()
    # `ServingParams`, not the hashed `Config`: the gate changes what a
    # surface does with a probability, not what any probability is.
    floor = ServingParams().breadth_rank_floor

    with ingest.run_job(engine, "breadth", {"adr": 176, "floor": floor}) as run:
        with engine.begin() as conn:
            result = conn.execute(text(COMPUTE_SQL))
            report.rows_written = int(result.rowcount or 0)
            row = conn.execute(text(LATEST_SQL)).first()
        if row is not None:
            report.latest = None if row[0] is None else float(row[0])
            report.latest_chg = None if row[1] is None else float(row[1])
            report.gate_open = ranking_gate_open(report.latest, floor)
        run.rows_written = report.rows_written
        run.notes = report.summary()

    return report
