"""`cscan predict` -- write calibrated `p_touch` rows for recent events.

ADR 174. The job is deliberately thin: `research.predict` decides what a
probability is and `core.calibration` decides what interval goes with it,
so this module only chooses which events to score, writes the rows, and
records the run.

**Fitting happens every run, and that is a choice rather than an oversight.**
Three seeds over 158k rows is a few minutes, which is small against
`nightly`'s 35-40 minutes, and it removes the entire class of failure where
a serialised model outlives the feature code that built it. The model spec
in `docs/model_spec_adr170.json` exists so a fit is reproducible; a pickle
would make it merely repeatable, which is not the same guarantee.

**The write is an upsert on `event_id`**, which both screener views now
join on (migration `e7b4c92f1a08`). The first attempt keyed on
`(ticker, as_of)` and died on the real data: a ticker can fire twice in one
day and the two can be **opposite sides**, so that pair is not a key and
collapsing it would have let a long row display the short's probability.
One prediction per event has no such ambiguity, and re-running replaces
rows rather than accumulating them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, text

from capitalscan.jobs import db_io, ingest
from capitalscan.jobs.provenance import git_sha
from capitalscan.research import features as feat
from capitalscan.research import predict as rp

#: How far back to score by default. Long enough that a few missed nightly
#: runs self-heal, short enough that the frame stays small. Events older
#: than this have resolved anyway and a forward-looking probability on them
#: is of no use to a reader.
DEFAULT_LOOKBACK_DAYS = 45

#: Below this the calibration buckets are too thin for the intervals to
#: mean anything, and writing rows anyway would put a confident-looking
#: number on screen backed by nothing. Refuse instead.
MIN_CALIBRATION_ROWS = 5_000


@dataclass
class PredictReport:
    """What one `cscan predict` run did. Printed and stored in `runs.notes`."""

    rows_scored: int = 0
    rows_written: int = 0
    rows_dropped: int = 0
    tickers: int = 0
    since: date | None = None
    model_version: str = ""

    def summary(self) -> str:
        return (
            f"{self.rows_written} predictions for {self.tickers} tickers "
            f"since {self.since} ({self.rows_dropped} dropped), "
            f"model {self.model_version}"
        )


def run_predict(
    engine: Engine | None = None,
    config_hash: str | None = None,
    since: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    n_buckets: int | None = None,
) -> PredictReport:
    """Fit, calibrate, and upsert `predictions` for recent events.

    Args:
        since: earliest `signal_date` to score. Defaults to
            `lookback_days` before the newest event, **not** before today:
            anchoring on the data means a stale database produces a small
            correct write rather than an empty one that looks like success.

    Raises:
        ValueError: if validate has too few rows to calibrate against. A
            probability whose interval is fitted on a few hundred events is
            worse than no probability, because it displays identically.
    """
    engine = engine or db_io.get_engine()
    if config_hash is None:
        from capitalscan.jobs.config import config_hash as hash_of
        from capitalscan.jobs.config import resolve_config

        config_hash = hash_of(resolve_config())

    if since is None:
        newest = rp.latest_signal_date(engine, config_hash)
        if newest is None:
            raise ValueError(f"no events exist for config {config_hash}")
        since = newest - timedelta(days=lookback_days)

    sha = git_sha()
    report = PredictReport(since=since)

    with ingest.run_job(
        engine,
        "predict",
        {"config_hash": config_hash, "since": str(since), "adr": 174},
    ) as run:
        predictor = (
            rp.fit_and_calibrate(engine, config_hash, sha, n_buckets=n_buckets)
            if n_buckets
            else rp.fit_and_calibrate(engine, config_hash, sha)
        )
        if predictor.n_calibrate < MIN_CALIBRATION_ROWS:
            raise ValueError(
                f"only {predictor.n_calibrate} validate rows; "
                f"{MIN_CALIBRATION_ROWS} needed before an interval means anything"
            )

        # **The types the fit actually saw, read off the fit itself.**
        # Not a hardcoded list: `build_training_frame` drops rows with NULL
        # labels, and which signal types that removes changes as the
        # backtest prices more events. Passing the fitted population keeps
        # serving and training in step by construction.
        trained_types = sorted(set(predictor.trained_signal_types))
        frame, frame_report = feat.build_serving_frame(
            engine, config_hash, since, trained_types=trained_types
        )
        report.rows_scored = frame_report.rows
        report.model_version = predictor.model_version
        if frame.empty:
            run.notes = f"no events since {since}"
            return report

        rows = rp.build_rows(predictor, frame, config_hash, run.run_id, sha)
        # Set here rather than in `research`: the column has no server
        # default, and one timestamp for the whole batch is the truth --
        # every row in it came from the same fit.
        stamped = datetime.now(UTC)
        for row in rows:
            row["created_at"] = stamped
        report.rows_dropped = len(frame) - len(rows)
        report.tickers = len({r["ticker"] for r in rows})

        if rows:
            # **`update_columns` excludes `id`, and that is not cosmetic.**
            # `db_io.upsert` overwrites every non-key column by default,
            # and `predictions.id` is a `bigserial` -- so a re-run tried to
            # reassign primary keys that `outcomes.prediction_id`
            # references, and Postgres refused with a
            # `ForeignKeyViolation`. The forward log is exactly what makes
            # a re-run worth doing, so the writer must not fight it.
            # `sync.py` carries the same fix for the same reason.
            updatable = [c for c in rows[0] if c not in {"id", "event_id"}]
            report.rows_written = db_io.upsert(
                engine,
                "predictions",
                rows,
                conflict_cols=["event_id"],
                update_columns=updatable,
            )
        run.rows_written = report.rows_written
        run.notes = report.summary()

    return report


def clear_predictions(engine: Engine, config_hash: str, drop_outcomes: bool = False) -> int:
    """Delete predictions for one config generation.

    **Refuses by default once the forward log has scored anything, and that
    is the point.** `outcomes.prediction_id` is a foreign key with no
    `ON DELETE` clause, so a bare delete raises `ForeignKeyViolation` the
    moment a single prediction has been resolved. The first version of this
    function did exactly that and was written when `outcomes` was empty; it
    then failed silently inside `cscan predict --clear`, leaving a table
    holding two models' predictions at once.

    A resolved outcome is the only uncontaminated measurement this project
    has -- a prediction recorded before its result existed. Deleting one to
    make room for a refit trades away the evidence that would validate the
    refit. So the default is to refuse and say how many rows stand in the
    way, and `drop_outcomes=True` is the deliberate, named way to say
    otherwise.

    Returns rows deleted.
    """
    with engine.begin() as conn:
        scored = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM outcomes o JOIN predictions p ON p.id = o.prediction_id "
                    "WHERE p.config_hash = :c"
                ),
                {"c": config_hash},
            ).scalar()
            or 0
        )
        if scored and not drop_outcomes:
            raise ValueError(
                f"{scored} of these predictions have resolved outcomes. Those are the "
                "forward log -- the only measurement here that nothing has iterated "
                "against -- and deleting the predictions would delete them too. Pass "
                "drop_outcomes=True to do it anyway."
            )
        if drop_outcomes:
            conn.execute(
                text(
                    "DELETE FROM outcomes WHERE prediction_id IN "
                    "(SELECT id FROM predictions WHERE config_hash = :c)"
                ),
                {"c": config_hash},
            )
        result = conn.execute(
            text("DELETE FROM predictions WHERE config_hash = :c"), {"c": config_hash}
        )
    return int(result.rowcount or 0)
