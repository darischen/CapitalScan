"""`cscan outcomes` — score written predictions against what actually happened.

DESIGN §7.8's forward log, which has been a table with no writer since Phase
5. It is the only path left to an honest measurement of this model.

**Why it matters more than any other measurement in the project.** Every
number currently quoted about the model comes from a split that was reused:
validate has been scored across many architectures, and the holdout was
spent on 2026-09-04 (ADR 172). Neither can answer "is this calibrated" any
more, because both have been iterated against. A prediction written today
for an event whose window has not closed is the one kind of evidence
nothing can contaminate — it was recorded before the outcome existed.

So this job is deliberately dumb: it joins `predictions` to `events` on
`event_id`, copies the realised values, and computes two scores. It fits
nothing, chooses nothing, and has no parameters that could be tuned toward
a nicer answer.

**Only closed windows are resolved.** `peak_ret_5d IS NULL` means the
forward window is still filling, and a prediction resolved against a
partial window would score against a smaller-magnitude number wearing a
complete label — the staleness class ADR 094 exists to prevent. Those rows
are skipped and picked up by a later run, which is why this is idempotent
and safe to put in `nightly`.

**Resolved rows are never rewritten.** `ON CONFLICT DO NOTHING`, not
`DO UPDATE`. A resolved outcome is a historical fact; if a later run
produced a different value for one, that is a defect to investigate rather
than a row to silently correct. Re-resolving would also let a changed
label quietly improve the model's recorded track record, which is the exact
failure this table exists to make impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from capitalscan.jobs import db_io, ingest

#: The threshold whose Brier score is stored per row. Matches
#: `research.predict.HEADLINE`: it is the field with the best combination
#: of skill and base rate, so it is the one worth a permanent column.
BRIER_THRESHOLD = 3

#: Nominal levels of the stored fan, for the pinball loss. Mirrors
#: `research.predict.FAN_TAUS`; the columns are fixed by migration 005 and
#: a mismatch would score a quantile against the wrong tau.
FAN: tuple[tuple[str, float], ...] = (
    ("q05", 0.05),
    ("q25", 0.25),
    ("q50", 0.50),
    ("q75", 0.75),
    ("q95", 0.95),
)


@dataclass
class OutcomeReport:
    """What one `cscan outcomes` run resolved."""

    resolved: int = 0
    already: int = 0
    pending: int = 0

    def summary(self) -> str:
        return (
            f"resolved {self.resolved} predictions "
            f"({self.already} already scored, {self.pending} windows still open)"
        )


def _pinball_sql() -> str:
    """The pinball loss over the stored fan, as SQL.

    `tau * (y - q)` when the outcome is above the quantile and
    `(1 - tau) * (q - y)` when below, averaged over the five levels. Written
    out rather than looped in Python because the whole point of this job is
    one set-based pass — pulling several thousand rows into pandas to
    compute an average of five terms would be slower and no clearer.

    A NULL quantile makes the whole expression NULL rather than scoring a
    partial fan, which would be a smaller loss for a worse prediction.
    """
    terms = [
        f"CASE WHEN e.fwd_ret_5d >= p.{col} "
        f"THEN {tau} * (e.fwd_ret_5d - p.{col}) "
        f"ELSE {1 - tau} * (p.{col} - e.fwd_ret_5d) END"
        for col, tau in FAN
    ]
    return "(" + " + ".join(terms) + f") / {len(FAN)}.0"


RESOLVE_SQL = """
INSERT INTO outcomes (
    prediction_id, realized_ret_5d, realized_mfe, realized_mae,
    touched_2, touched_3, touched_5, touched_10,
    pinball_loss, brier_3pct, resolved_at
)
SELECT p.id,
       e.fwd_ret_5d,
       e.peak_ret_5d,
       -- ADR 175's fixed-window trough, not `events.mae`. `mae` runs until
       -- the trade exits, so it carries `ExitParams` and is not comparable
       -- across config generations; the column here is a five-day window
       -- like everything else scored on this row.
       e.trough_ret_5d,
       e.peak_ret_5d >= 0.02,
       e.peak_ret_5d >= 0.03,
       e.peak_ret_5d >= 0.05,
       e.peak_ret_10d >= 0.10,
       {pinball},
       CASE WHEN p.p_touch_3 IS NULL THEN NULL
            ELSE power(p.p_touch_3 - (e.peak_ret_5d >= 0.03)::int, 2) END,
       :now
  FROM predictions p
  JOIN events e ON e.id = p.event_id
 WHERE e.peak_ret_5d IS NOT NULL
   AND e.fwd_ret_5d IS NOT NULL
ON CONFLICT (prediction_id) DO NOTHING
"""

COUNTS_SQL = """
SELECT count(*) FILTER (WHERE o.prediction_id IS NOT NULL)                  AS already,
       count(*) FILTER (WHERE o.prediction_id IS NULL
                          AND e.peak_ret_5d IS NULL)                        AS pending
  FROM predictions p
  JOIN events e ON e.id = p.event_id
  LEFT JOIN outcomes o ON o.prediction_id = p.id
"""


def run_outcomes(engine: Engine | None = None) -> OutcomeReport:
    """Resolve every prediction whose forward window has closed.

    Idempotent: already-resolved rows are left alone and still-open windows
    are skipped, so running this nightly converges without supervision and
    without ever rewriting a recorded outcome.
    """
    engine = engine or db_io.get_engine()
    report = OutcomeReport()

    with ingest.run_job(engine, "outcomes", {"adr": 174, "design": "7.8"}) as run:
        with engine.begin() as conn:
            before = conn.execute(text(COUNTS_SQL)).one()
            report.already = int(before.already)
            result = conn.execute(
                text(RESOLVE_SQL.format(pinball=_pinball_sql())),
                {"now": datetime.now(UTC)},
            )
            report.resolved = int(result.rowcount or 0)
            report.pending = int(conn.execute(text(COUNTS_SQL)).one().pending)

        run.rows_written = report.resolved
        run.notes = report.summary()

    return report


SCORE_SQL = """
SELECT count(*)                                   AS n,
       avg(o.brier_3pct)                          AS brier,
       avg((o.touched_3)::int)                    AS base_rate,
       avg(o.pinball_loss)                        AS pinball,
       min(p.as_of)                               AS first_pred,
       max(p.as_of)                               AS last_pred
  FROM outcomes o JOIN predictions p ON p.id = o.prediction_id
"""


def score(engine: Engine | None = None) -> dict[str, float | None]:
    """The forward log's headline numbers, with no interpretation attached.

    Returns raw aggregates. **Brier skill is deliberately not computed
    here**, because the reference it must be measured against is the base
    rate *of this sample*, and quoting a skill score over a handful of
    resolved rows invites reading noise as evidence. The caller decides
    when there is enough to say anything.
    """
    engine = engine or db_io.get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(SCORE_SQL)).one()
    return {
        "n": row.n,
        "brier": None if row.brier is None else float(row.brier),
        "base_rate": None if row.base_rate is None else float(row.base_rate),
        "pinball": None if row.pinball is None else float(row.pinball),
        "first_pred": row.first_pred,
        "last_pred": row.last_pred,
    }
