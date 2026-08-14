"""Equity-curve export (Session 14, task 14.1).

`research.benchmarks.run_benchmarks(collect_curves=True)` already builds the
pooled buy-hold, signal, and 200-replication null equity curves as part of
its normal simulation; this module only **shapes** what it is handed into a
tidy `(date, arm, value)` frame plus a CSV. It performs no simulation of any
kind, and it never calls `load_window`, `build_positions`, or any
`simulate_*` function itself — doing so would be a second simulation of the
same thing, which is precisely the invariant 2 violation task 14.1 warns
against. `research.benchmarks.ArmCurves` is the only input type this module
accepts, and every function here is a pure transform of it.

**Row convention.** Each `ArmSimulation.equity` series has `n_days + 1`
points: index 0 is the pre-window baseline of 1.0
(`core.arms.simulate_portfolio`'s docstring), and `equity[i + 1]` is the
value after day `i`'s return. So `equity[i + 1]` is paired with
`dates[i]`, index 0 is dropped, and the exported curve has exactly one row
per trading day in the window (14.1 acceptance) with no gaps.

**Endpoint equivalence.** `equity[-1]` for the buy-hold and signal curves is
exactly `benchmarks.total_ret + 1` for that arm
(`ArmSimulation.total_ret = equity[-1] / equity[0] - 1` and `equity[0] ==
1.0`), which is what lets the acceptance test compare the curve's last row
to the stored `benchmarks` row to `1e-9`.

**The null band.** Per-day 2.5th / 50th / 97.5th percentile across the
`ArmCurves.random` replications, written as three named series
(`null_p2_5`, `null_p50`, `null_p97_5`) rather than as a single band column,
so the tidy frame stays one row per `(date, arm)` with no wide columns.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from capitalscan.core.arms import ARM_BUY_HOLD, ARM_SIGNAL
from capitalscan.research.benchmarks import ArmCurves

# Null-band series names, written into the same `arm` column as the other
# two curves so the export stays one tidy (date, arm, value) frame.
ARM_NULL_P2_5 = "null_p2_5"
ARM_NULL_P50 = "null_p50"
ARM_NULL_P97_5 = "null_p97_5"

# The task 14.1 brief: "per-day 2.5th and 97.5th percentile across the 200
# replications, plus the median."
NULL_LOW_PCT = 2.5
NULL_MID_PCT = 50.0
NULL_HIGH_PCT = 97.5

CURVE_COLUMNS = ["date", "arm", "value"]


def _series_to_rows(dates: Sequence[date], equity: pd.Series, arm: str) -> list[dict[str, object]]:
    """`equity[i + 1]` paired with `dates[i]`, for one arm's curve.

    Raises rather than silently truncating on a length mismatch: a curve
    that does not have exactly `len(dates) + 1` points means the caller
    handed this function something other than a `run_benchmarks` equity
    series, which is a construction bug this module has no way to fix.
    """
    values = np.asarray(equity, dtype="float64")
    if values.shape[0] != len(dates) + 1:
        raise ValueError(
            f"{arm}: equity has {values.shape[0]} points for {len(dates)} dates "
            f"(expected {len(dates) + 1})"
        )
    return [
        {"date": d.isoformat(), "arm": arm, "value": float(v)}
        for d, v in zip(dates, values[1:], strict=True)
    ]


def null_band_rows(
    dates: Sequence[date], replications: Sequence[pd.Series]
) -> list[dict[str, object]]:
    """The per-day 2.5th / 50th / 97.5th percentile across the null replications.

    Every replication's equity is expected at `len(dates) + 1` points, same
    convention as `_series_to_rows`. `np.percentile` over the replication
    axis produces one value per day for each of the three bands.
    """
    if not replications:
        raise ValueError("no null replications to band")
    matrix = np.stack(
        [np.asarray(r, dtype="float64")[1:] for r in replications],
        axis=0,
    )
    if matrix.shape[1] != len(dates):
        raise ValueError(f"null replications have {matrix.shape[1]} days for {len(dates)} dates")
    p2_5 = np.percentile(matrix, NULL_LOW_PCT, axis=0)
    p50 = np.percentile(matrix, NULL_MID_PCT, axis=0)
    p97_5 = np.percentile(matrix, NULL_HIGH_PCT, axis=0)

    rows: list[dict[str, object]] = []
    for i, d in enumerate(dates):
        iso = d.isoformat()
        rows.append({"date": iso, "arm": ARM_NULL_P2_5, "value": float(p2_5[i])})
        rows.append({"date": iso, "arm": ARM_NULL_P50, "value": float(p50[i])})
        rows.append({"date": iso, "arm": ARM_NULL_P97_5, "value": float(p97_5[i])})
    return rows


def curve_frame(curves: ArmCurves) -> pd.DataFrame:
    """The tidy `(date, arm, value)` frame: buy-hold, signal, and the null band.

    Pure shaping of an already-built `ArmCurves` — no simulation. Row order
    is buy-hold then signal then the null band (each band series in
    ascending date order), which is what makes two calls on the same
    `ArmCurves` byte-identical.
    """
    rows = _series_to_rows(curves.dates, curves.buy_hold, ARM_BUY_HOLD)
    rows += _series_to_rows(curves.dates, curves.signal, ARM_SIGNAL)
    rows += null_band_rows(curves.dates, curves.random)
    return pd.DataFrame(rows, columns=CURVE_COLUMNS)


def curve_csv_path(
    config_hash: str, split_key: str, output_dir: Path = Path("reports/phase4")
) -> Path:
    """`reports/phase4/equity_curves_<config_hash>_<split>.csv` (14.1 output rule)."""
    return output_dir / f"equity_curves_{config_hash}_{split_key}.csv"


def write_curve_csv(frame: pd.DataFrame, path: Path) -> Path:
    """Write the tidy frame to `path`, with a leading provenance comment.

    The comment line carries the generation timestamp and is the only part
    of the file two runs may disagree on; the acceptance test strips it
    before comparing (14.1: "two runs produce byte-identical CSVs ignoring
    the header's timestamp").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# generated_at={generated_at}\n")
        # The base is implicit: row i is equity at the END of trading day i,
        # and the 1.0 starting value sits before the first row rather than in
        # it. So `total_ret = last - 1.0`, never `last / first - 1`.
        #
        # Stated in the file because the wrong form is easy to reach and
        # `buy_hold` hides it. `simulate_buy_hold` sets `equity[1] = equity[0]`
        # on day 0 (nothing is held yet), so its first row really is 1.0 and
        # both formulas agree; `simulate_portfolio` can earn a day-0 return, so
        # the signal arm's first row is not 1.0 and they diverge. Checking one
        # arm, seeing it agree, and trusting the other is exactly how this goes
        # wrong — it did, during this task's own verification.
        fh.write("# convention: value = equity at END of that date; base 1.0 precedes row 1\n")
        fh.write("# total_ret = last_value - 1.0  (NOT last/first - 1)\n")
        frame.to_csv(fh, index=False, columns=CURVE_COLUMNS, lineterminator="\n")
    return path


def export_curves(
    curves: ArmCurves,
    config_hash: str,
    split_key: str,
    output_dir: Path = Path("reports/phase4"),
) -> tuple[pd.DataFrame, Path]:
    """Shape `curves` and write the CSV; the one entry point 14.6's CLI hook calls.

    Takes an already-populated `ArmCurves` (from
    `run_benchmarks(collect_curves=True)`) rather than a config/split pair —
    this module never calls `run_benchmarks` or any simulation function
    itself.
    """
    frame = curve_frame(curves)
    path = write_curve_csv(frame, curve_csv_path(config_hash, split_key, output_dir))
    return frame, path
