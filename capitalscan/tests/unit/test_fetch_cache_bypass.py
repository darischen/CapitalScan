"""A bar window still filling in is never cached (ADR 202).

On 2026-09-28 the 13:15 PT nightly cached daily batches that lacked the
day's bar for 63 of 1,454 tickers. A 14:41 rerun read those files back and
wrote the same 63 gaps, though Yahoo had every bar by then.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from capitalscan.jobs.fetch import yahoo
from capitalscan.jobs.fetch.base import cached


class TestBypassFn:
    def test_bypass_neither_reads_nor_writes(self, tmp_path):
        calls: list[int] = []

        @cached(
            source="t",
            key_fn=lambda x: str(x),
            cache_root=tmp_path,
            bypass_fn=lambda x: True,
        )
        def fetch(x: int) -> pd.DataFrame:
            calls.append(x)
            return pd.DataFrame({"n": [len(calls)]})

        assert fetch(1)["n"].iloc[0] == 1
        assert fetch(1)["n"].iloc[0] == 2
        assert not any(tmp_path.rglob("*.parquet"))

    def test_bypass_ignores_a_file_already_on_disk(self, tmp_path):
        """A file written before the bypass existed must not answer."""

        @cached(source="t", key_fn=lambda x: str(x), cache_root=tmp_path)
        def stale(x: int) -> pd.DataFrame:
            return pd.DataFrame({"v": ["stale"]})

        stale(1)

        @cached(
            source="t",
            key_fn=lambda x: str(x),
            cache_root=tmp_path,
            bypass_fn=lambda x: True,
        )
        def fresh(x: int) -> pd.DataFrame:
            return pd.DataFrame({"v": ["fresh"]})

        assert fresh(1)["v"].iloc[0] == "fresh"

    def test_false_bypass_still_caches(self, tmp_path):
        calls: list[int] = []

        @cached(
            source="t",
            key_fn=lambda x: str(x),
            cache_root=tmp_path,
            bypass_fn=lambda x: False,
        )
        def fetch(x: int) -> pd.DataFrame:
            calls.append(x)
            return pd.DataFrame({"n": [1]})

        fetch(1)
        fetch(1)
        assert calls == [1]


class TestWindowIsRecent:
    def test_a_window_ending_today_is_recent(self):
        today = date.today()
        assert yahoo._window_is_recent(["ADI"], today - timedelta(days=5), today)

    def test_the_edge_of_the_lookback_is_recent(self):
        end = date.today() - timedelta(days=yahoo.RECENT_WINDOW_DAYS)
        assert yahoo._window_is_recent("ADI", end - timedelta(days=5), end)

    def test_an_old_window_caches(self):
        end = date.today() - timedelta(days=yahoo.RECENT_WINDOW_DAYS + 1)
        assert not yahoo._window_is_recent(["ADI"], end - timedelta(days=60), end)
