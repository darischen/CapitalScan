"""`cscan outcomes exclude`: set forward-log rows aside, with a reason (ADR 210).

The forward log is never rewritten (ADR 029, ADR 195). When a row's inputs
turn out to have been corrupt, the row stays and this module records a
separate statement about it in `forward_log_exclusions`. Every reader that
measures the model filters on that table: `outcomes.score`, the web
reliability table and the recent realised rate.

**Ids, never a rule.** The caller names the prediction ids. A rule such as
"every EMBJ row resolved that week" would be re-evaluated on every run and
could start matching rows nobody looked at. The ids come from an audit that
recomputed each outcome from current bars; ADR 210 lists them.

**Insert-only.** `ON CONFLICT DO NOTHING`: excluding a row twice keeps the
first reason, the same rule `outcomes` follows. Undoing an exclusion is a
`DELETE` by hand, on purpose.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Engine, text

from capitalscan.jobs import db_io
from capitalscan.jobs.ingest import run_job
from capitalscan.jobs.provenance import git_sha

INSERT_SQL = """
INSERT INTO forward_log_exclusions (prediction_id, reason, detail, run_id, git_sha)
SELECT p.id, :reason, :detail, :run_id, :git_sha
  FROM predictions p
 WHERE p.id = ANY(:ids)
ON CONFLICT (prediction_id) DO NOTHING
"""

MISSING_SQL = (
    "SELECT x FROM unnest(CAST(:ids AS bigint[])) x WHERE x NOT IN (SELECT id FROM predictions)"
)

#: The clause every forward-log reader appends. One spelling, so a grep
#: for it finds every reader.
NOT_EXCLUDED = "NOT EXISTS (SELECT 1 FROM forward_log_exclusions x WHERE x.prediction_id = p.id)"


@dataclass
class ExcludeReport:
    requested: int
    written: int
    already: int
    missing: list[int]

    def summary(self) -> str:
        parts = [f"excluded {self.written} of {self.requested}"]
        if self.already:
            parts.append(f"{self.already} already excluded")
        if self.missing:
            parts.append(f"{len(self.missing)} not in predictions: {self.missing}")
        return ", ".join(parts)


def exclude(
    ids: Sequence[int],
    reason: str,
    detail: str | None = None,
    engine: Engine | None = None,
) -> ExcludeReport:
    """Record an exclusion for each named prediction that exists.

    A missing id is reported, never an error: the caller may run the same
    list against both stores, and serving holds a narrower window of rows.
    """
    if not reason.strip():
        raise ValueError("an exclusion needs a reason")
    wanted = sorted({int(i) for i in ids})
    engine = engine or db_io.get_engine()
    with run_job(engine, "outcomes_exclude", {"n": len(wanted), "reason": reason}) as run:
        with engine.begin() as conn:
            missing = [int(r[0]) for r in conn.execute(text(MISSING_SQL), {"ids": wanted})]
            result = conn.execute(
                text(INSERT_SQL),
                {
                    "ids": wanted,
                    "reason": reason,
                    "detail": detail,
                    "run_id": run.run_id,
                    "git_sha": git_sha(),
                },
            )
        written = int(result.rowcount or 0)
        report = ExcludeReport(
            requested=len(wanted),
            written=written,
            already=len(wanted) - len(missing) - written,
            missing=missing,
        )
        run.rows_written = written
        run.notes = report.summary()
    return report
