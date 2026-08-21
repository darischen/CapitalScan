"""Every column `compute_all` produces is a column `run_indicators` writes.

Found 2026-08-21. `INDICATOR_COLUMNS` is the projection applied immediately
before the upsert:

    cols = ["ticker", "ts", "interval", *INDICATOR_COLUMNS, ...]
    db_io.upsert(engine, "indicators", merged[cols], [...])

ADR 144 added `bull_close_below_lower` to the registry, to `core/indicators.py`
and to the table via migration `c3f91a70b8d4` — but not to this list. So the
flag was computed for all 3.93M rows on every run and dropped at `merged[cols]`,
leaving the column NULL with no error anywhere.

**This is the failure mode that keeps recurring in this system**: a job that
does less than it was asked and exits 0. A 23-minute full-history recompute
whose entire purpose was that backfill would have reported success having
written nothing to the column it was run for. The only tell was querying
`count(bull_close_below_lower)` and finding it still exactly zero while
`INSERT INTO indicators` was actively running.

A registry-vs-list comparison does not work: registry keys are *function*
names (`bollinger`, `stochastic`) and one function emits several columns. The
honest invariant is over the frame that actually reaches the writer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from capitalscan.core.config import IndicatorParams
from capitalscan.core.indicators import compute_all
from capitalscan.jobs.compute import INDICATOR_COLUMNS

# A computed column that deliberately never persists would go here, with the
# reason. Empty today, and an entry should be argued for rather than added to
# make this test pass.
NOT_PERSISTED: dict[str, str] = {}


def _bars(n: int = 400) -> pd.DataFrame:
    """Enough history to clear every warmup in the registry."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "open": close + 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "adj_close": close,
            "volume": rng.integers(1_000_000, 5_000_000, n),
        },
        index=idx,
    )


def _produced() -> set[str]:
    bars = _bars()
    out = compute_all(bars, IndicatorParams())
    return set(out.columns) - set(bars.columns)


def test_every_computed_column_is_written() -> None:
    missing = _produced() - set(INDICATOR_COLUMNS) - set(NOT_PERSISTED)
    assert not missing, (
        f"{sorted(missing)} computed by `compute_all` but absent from "
        "INDICATOR_COLUMNS, so `merged[cols]` drops them before the upsert. "
        "The column stays NULL and the job still exits 0. Add them to "
        "INDICATOR_COLUMNS, or to NOT_PERSISTED with a reason."
    )


def test_nothing_is_written_that_is_not_computed() -> None:
    """The other direction, which fails loudly rather than silently.

    A name in `INDICATOR_COLUMNS` that `compute_all` does not produce makes
    `merged[cols]` raise `KeyError` at write time — after the whole compute
    phase has run. Cheaper to catch here.
    """
    extra = set(INDICATOR_COLUMNS) - _produced()
    assert not extra, f"{sorted(extra)} in INDICATOR_COLUMNS but not produced by compute_all"


def test_both_close_confirmed_flags_persist() -> None:
    """ADR 108's flag and ADR 144's mirror, named explicitly.

    The sweep above would catch either going missing, but naming them makes
    the failure legible: these two are the only registry entries whose
    function name *is* their column name, which is exactly why one could be
    added to the registry and forgotten here.
    """
    for flag in ("bear_close_above_upper", "bull_close_below_lower"):
        assert flag in INDICATOR_COLUMNS
