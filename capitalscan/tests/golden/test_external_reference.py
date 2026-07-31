"""External reference agreement test (ADR 086, TESTS.md §4.1).

`cscan verify-indicators` writes tests/golden/external_reference.csv with
computed values and empty external_* columns. The user fills the
external_* columns once by hand from StockCharts or TradingView. This
test then asserts agreement within 0.1% — the check that catches the
ddof and SMA-vs-EMA class of silent divergence.

Skipped until the file exists and has at least one filled row, since
that fill-in is a manual, one-time step this test cannot do itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pandas as pd
import pytest

REFERENCE_CSV = Path(__file__).parent / "external_reference.csv"
COMPUTED_COLUMNS = ["bb_upper", "bb_mid", "bb_lower", "k_full", "d_full"]


def _load_filled_rows() -> pd.DataFrame:
    if not REFERENCE_CSV.exists():
        pytest.skip("external_reference.csv not generated yet — run cscan verify-indicators")
    df = pd.read_csv(REFERENCE_CSV)
    external_cols = [f"external_{c}" for c in COMPUTED_COLUMNS]
    filled = df.dropna(subset=external_cols, how="all")
    non_blank = filled[external_cols].apply(
        lambda r: r.astype(str).str.strip().ne("").all(), axis=1
    )
    filled = filled[non_blank]
    if filled.empty:
        pytest.skip("external_reference.csv has no hand-filled rows yet")
    return cast(pd.DataFrame, filled)


def test_computed_matches_external_within_tolerance():
    df = _load_filled_rows()
    for col in COMPUTED_COLUMNS:
        computed = df[col].astype(float)
        external = df[f"external_{col}"].astype(float)
        rel_diff = ((computed - external).abs() / external.abs()).max()
        assert rel_diff < 0.001, f"{col} disagrees with external reference by more than 0.1%"
