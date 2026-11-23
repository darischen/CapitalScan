"""The web's warm-up countdown must end where `cscan indicators` starts.

`web/lib/warmup.ts` cannot import Python, so it holds a copy of
`MIN_BARS_FOR_INDICATORS`. A copy that drifts shows a new listing a
countdown that ends on the wrong day, with nothing failing.
"""

from __future__ import annotations

import re
from pathlib import Path

from capitalscan.jobs.compute import MIN_BARS_FOR_INDICATORS

WARMUP_TS = Path(__file__).resolve().parents[3] / "web" / "lib" / "warmup.ts"


def test_web_warmup_matches_the_indicator_minimum() -> None:
    match = re.search(
        r"export const INDICATOR_WARMUP_SESSIONS = (\d+);", WARMUP_TS.read_text(encoding="utf-8")
    )
    assert match, f"INDICATOR_WARMUP_SESSIONS not found in {WARMUP_TS}"
    assert int(match.group(1)) == MIN_BARS_FOR_INDICATORS
