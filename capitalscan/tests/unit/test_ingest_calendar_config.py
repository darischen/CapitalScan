"""`run_calendar` must read `config.splits`, not a default `SplitParams()`.

**The same bug `compute.run_events` already carries a fix for.** Reading the
default dataclass ignores an override entirely, so a config that moves
`ingest_start` produces a calendar that stops where the default said and
every downstream job is silently bounded by a window nobody asked for.

Found 2026-09-03 building an extended-history store: the override moved
`ingest_start` to 1999 and `trading_days` still began 2009, while
`market_days` -- which does not read this -- correctly reached 1998. The
mismatch between two tables that should share a start is what exposed it.
"""

from __future__ import annotations

import inspect

from capitalscan.core.config import Config, SplitParams
from capitalscan.jobs import ingest
from capitalscan.tests.unit._probe import code_of


class TestRunCalendarHonoursTheConfig:
    def test_it_accepts_a_config(self) -> None:
        assert "config" in inspect.signature(ingest.run_calendar).parameters

    def test_it_does_not_hardcode_split_params(self) -> None:
        """`SplitParams()` with empty parens is the bug, by construction."""
        code = code_of(ingest.run_calendar)
        assert "SplitParams()" not in code
        assert "resolve_config()" in code

    def test_it_reads_ingest_start_from_the_resolved_splits(self) -> None:
        assert "splits.ingest_start" in code_of(ingest.run_calendar)

    def test_an_override_actually_changes_the_start(self) -> None:
        """The property, not the spelling: a moved `ingest_start` must reach
        the schedule call."""
        moved = Config(splits=SplitParams(ingest_start="1999-01-01"))
        assert moved.splits.ingest_start == "1999-01-01"
        assert SplitParams().ingest_start != moved.splits.ingest_start

    def test_the_default_is_unchanged(self) -> None:
        """Production behaviour must not move: with no override the
        resolved value is the one the hardcoded call produced."""
        assert Config().splits.ingest_start == SplitParams().ingest_start
