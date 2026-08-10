"""`peak_label_sql` executed by Postgres, not transcribed into pandas.

`tests/unit/test_peak_labels.py::TestSqlMatchesPythonReference` replays the
generated SQL's arithmetic in pandas and diffs it against the Python
reference. That catches a reference/oracle divergence but structurally
cannot catch a divergence between the oracle and the statement Postgres
runs, because the oracle is a hand transcription of that statement. It
copied an unbounded peak filter faithfully and passed while every
`next_open` label in production was overstated.

This tier closes that gap: build a real event and a real path, run
`backfill_peak_labels`, read the columns back, and compare against
`derive_labels_from_path` on the same numbers.

**Writes only its own rows and deletes them.** No `TRUNCATE` anywhere in
this file, deliberately: `events` and `path` hold 622k+ production rows
that `tests/integration/test_ingest.py`-style truncation would destroy.
Scoping is by a unique `config_hash`, which is also what
`backfill_peak_labels` filters on, so the UPDATE cannot reach a production
row even if cleanup fails.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.core.types import Side
from capitalscan.jobs import db_io
from capitalscan.research.path_labels import derive_labels_from_path
from capitalscan.research.peak_labels import backfill_peak_labels

HORIZONS = (1, 2, 3, 5, 10)
TARGETS = (0.02, 0.03, 0.05, 0.10)
ENTRY_PRICE = 100.0


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture()
def scoped():
    """A throwaway `(engine, run_id, config_hash)` triple, cleaned up after.

    `config_hash` is a fresh UUID per test, so a concurrent production
    backfill and this test cannot touch each other's rows.
    """
    engine = db_io.get_engine()
    token = uuid.uuid4().hex
    run_id = f"test-peak-{token}"
    config_hash = f"test-peak-{token}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runs (run_id, job, git_sha, params, started_at) "
                "VALUES (:run_id, 'test', 'test', '{}'::jsonb, :now)"
            ),
            {"run_id": run_id, "now": datetime.now(timezone.utc)},
        )
    yield engine, run_id, config_hash
    with engine.begin() as conn:
        # `path` cascades from `events`; `events` must go before `runs`.
        conn.execute(
            text("DELETE FROM events WHERE config_hash = :config_hash"),
            {"config_hash": config_hash},
        )
        conn.execute(text("DELETE FROM runs WHERE run_id = :run_id"), {"run_id": run_id})


def _insert_event(engine, run_id: str, config_hash: str, entry_kind: str) -> int:
    with engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "INSERT INTO events (run_id, config_hash, ticker, signal_date, "
                    "signal_type, side, entry_kind, split_key, entry_price) "
                    "VALUES (:run_id, :config_hash, 'TEST', :signal_date, "
                    "'confluence_low', 'long', :entry_kind, 'train', :entry_price) "
                    "RETURNING id"
                ),
                {
                    "run_id": run_id,
                    "config_hash": config_hash,
                    "signal_date": date(2024, 1, 2),
                    "entry_kind": entry_kind,
                    "entry_price": ENTRY_PRICE,
                },
            ).scalar_one()
        )


def _insert_path(engine, event_id: int, favorable: list[float]) -> pd.DataFrame:
    """Path rows numbered from `day_offset = 1`, exactly as production writes
    them (`core.returns.path_for_event`), independent of entry kind."""
    frame = pd.DataFrame(
        {
            "event_id": event_id,
            "day_offset": range(1, len(favorable) + 1),
            "favorable": favorable,
            "adverse": [-abs(f) / 2 for f in favorable],
            "terminal": favorable,
        }
    )
    with engine.begin() as conn:
        for row in frame.to_dict("records"):
            conn.execute(
                text(
                    "INSERT INTO path (event_id, day_offset, favorable, adverse, terminal) "
                    "VALUES (:event_id, :day_offset, :favorable, :adverse, :terminal)"
                ),
                row,
            )
    return frame


def _read_peaks(engine, event_id: int) -> dict:
    cols = ", ".join(f"peak_ret_{h}d" for h in HORIZONS)
    with engine.connect() as conn:
        row = (
            conn.execute(text(f"SELECT {cols} FROM events WHERE id = :id"), {"id": event_id})
            .mappings()
            .one()
        )
    return {k: (None if v is None else float(v)) for k, v in row.items()}


def _reference(frame: pd.DataFrame, entry_offset: int) -> dict:
    return derive_labels_from_path(
        path=frame[["day_offset", "favorable", "adverse", "terminal"]],
        entry_offset=entry_offset,
        holding_days=None,
        entry_price=ENTRY_PRICE,
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=TARGETS,
        horizons=HORIZONS,
        capture_ratio_cap=100.0,
    )


@pytest.mark.parametrize(
    ("entry_kind", "entry_offset"),
    [("touch", 0), ("next_open", 1)],
)
def test_postgres_agrees_with_the_python_reference(scoped, entry_kind, entry_offset):
    """The whole point of this file. `next_open` is the case the pandas
    replay could not reach: `day_offset = 1` sits before entry, and an
    unbounded peak filter sweeps it in.
    """
    engine, run_id, config_hash = scoped
    # Day 1 (pre-entry for next_open) is a large spike; the in-window days
    # top out far below it. The two windows give different answers here and
    # nowhere else, which is the property being tested.
    favorable = [0.20, 0.01, 0.02, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03]
    event_id = _insert_event(engine, run_id, config_hash, entry_kind)
    frame = _insert_path(engine, event_id, favorable)

    backfill_peak_labels(engine, config_hash, HORIZONS)

    written = _read_peaks(engine, event_id)
    expected = _reference(frame, entry_offset)
    for h in HORIZONS:
        key = f"peak_ret_{h}d"
        want = expected[key]
        if want != want:  # NaN
            assert written[key] is None, f"{key}: Postgres {written[key]}, reference NULL"
        else:
            assert written[key] == pytest.approx(want, abs=1e-6), (
                f"{key}: Postgres {written[key]}, reference {want}"
            )


def test_next_open_peak_excludes_the_pre_entry_day(scoped):
    """The regression, stated as a value rather than as agreement.

    Pinned literally so a future change to `derive_labels_from_path` cannot
    quietly move both sides at once — the failure mode a pure
    agreement test has.
    """
    engine, run_id, config_hash = scoped
    favorable = [0.20, 0.01, 0.02, 0.03] + [0.03] * 7
    event_id = _insert_event(engine, run_id, config_hash, "next_open")
    _insert_path(engine, event_id, favorable)

    backfill_peak_labels(engine, config_hash, HORIZONS)

    written = _read_peaks(engine, event_id)
    assert written["peak_ret_1d"] == pytest.approx(0.01)
    assert written["peak_ret_2d"] == pytest.approx(0.02)
    assert written["peak_ret_3d"] == pytest.approx(0.03)
    assert written["peak_ret_5d"] == pytest.approx(0.03)
    # Window [2, 11] needs 10 offsets and the path supplies exactly 11 rows
    # numbered 1..11, so the 10-day horizon is complete.
    assert written["peak_ret_10d"] == pytest.approx(0.03)


def test_incomplete_window_stays_null_in_postgres(scoped):
    """The completeness gate, executed rather than transcribed."""
    engine, run_id, config_hash = scoped
    event_id = _insert_event(engine, run_id, config_hash, "next_open")
    # Offsets 1..4 -> in-window offsets 2,3,4 = 3 days of a next_open event.
    _insert_path(engine, event_id, [0.20, 0.01, 0.02, 0.03])

    backfill_peak_labels(engine, config_hash, HORIZONS)

    written = _read_peaks(engine, event_id)
    assert written["peak_ret_3d"] == pytest.approx(0.03)
    assert written["peak_ret_5d"] is None
    assert written["peak_ret_10d"] is None
