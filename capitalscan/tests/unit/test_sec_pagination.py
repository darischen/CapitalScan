"""Unit tests for SEC submissions pagination (ADR 036, DECISIONS.md #036).

The regression this locks down: `fetch_submissions` only ever read
`filings.recent`, which the SEC submissions API caps at roughly the most
recent 1,000 filings. Older filings live in per-CIK shards listed under
`filings.files[]`, each fetched by `name` from the same host. Skipping
those shards is why most of the universe's 8-K history started in
2014-2016 instead of reaching ADR 036's 2010 target, and why
`_merge_days_to_earnings` (jobs/compute.py) was landing a bogus
next-future-filing date on bars a decade before the ticker's earliest
known 8-K.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
import requests

from capitalscan.jobs.fetch import sec

CIK = 320193


def _response(payload: dict, status: int = 200) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(payload).encode("utf-8")
    resp.encoding = "utf-8"
    return resp


def _recent(forms: list[str], dates: list[str], accns: list[str]) -> dict:
    return {"form": forms, "filingDate": dates, "accessionNumber": accns}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the rate limiter and retry backoff from slowing the suite down.

    `base.rate_limited` and `base.with_retry` resolve `time.sleep` at call
    time, so patching the stdlib module here is enough even though `sec.py`
    applies both decorators at import time.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def sec_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_user_agent` raises `SecConfigError` unless this is set (DESIGN §4.2)."""
    monkeypatch.setenv("SEC_USER_AGENT", "CapitalScan tests test@example.com")


class TestFetchSubmissionsNoShards:
    """Current behaviour: a CIK with a short filing history has no `files[]`."""

    def test_returns_recent_only(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        payload = {
            "filings": {
                "recent": _recent(["8-K", "10-Q"], ["2024-01-05", "2024-02-01"], ["a1", "a2"]),
                "files": [],
            }
        }

        def fake_get(url: str, **kwargs: Any) -> requests.Response:
            assert url == sec.SUBMISSIONS_URL.format(cik=CIK)
            return _response(payload)

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

        df = sec.fetch_submissions(CIK)

        assert list(df["form"]) == ["8-K", "10-Q"]
        assert list(df["filed_on"]) == ["2024-01-05", "2024-02-01"]
        assert (df["cik"] == CIK).all()


class TestFetchSubmissionsWithShards:
    def test_one_shard_is_merged_with_recent(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        shard_name = "CIK0000320193-submissions-001.json"
        payload = {
            "filings": {
                "recent": _recent(["8-K"], ["2020-01-05"], ["a-recent"]),
                "files": [{"name": shard_name}],
            }
        }
        shard_payload = _recent(["8-K", "10-K"], ["2010-03-01", "2010-04-01"], ["a-old", "a-old2"])

        def fake_get(url: str, **kwargs: Any) -> requests.Response:
            if url == sec.SUBMISSIONS_URL.format(cik=CIK):
                return _response(payload)
            if url == sec.SUBMISSIONS_HOST + shard_name:
                return _response(shard_payload)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

        df = sec.fetch_submissions(CIK)

        # Both the capped recent window and the older shard must survive,
        # in particular the pre-2014 date that `recent` alone would never see.
        assert set(df["filed_on"]) == {"2020-01-05", "2010-03-01", "2010-04-01"}
        assert set(df["accn"]) == {"a-recent", "a-old", "a-old2"}
        assert (df["cik"] == CIK).all()

    def test_multiple_shards_are_all_merged(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        names = [
            "CIK0000320193-submissions-001.json",
            "CIK0000320193-submissions-002.json",
        ]
        payload = {
            "filings": {
                "recent": _recent(["8-K"], ["2022-01-01"], ["a0"]),
                "files": [{"name": n} for n in names],
            }
        }
        shard_payloads = {
            names[0]: _recent(["8-K"], ["2016-01-01"], ["a1"]),
            names[1]: _recent(["8-K"], ["2011-01-01"], ["a2"]),
        }

        def fake_get(url: str, **kwargs: Any) -> requests.Response:
            if url == sec.SUBMISSIONS_URL.format(cik=CIK):
                return _response(payload)
            for name, shard in shard_payloads.items():
                if url == sec.SUBMISSIONS_HOST + name:
                    return _response(shard)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

        df = sec.fetch_submissions(CIK)

        assert set(df["filed_on"]) == {"2022-01-01", "2016-01-01", "2011-01-01"}
        assert len(df) == 3

    def test_a_failing_shard_raises_and_names_the_cik_and_shard(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        """One bad shard must raise, not be logged and skipped.

        `fetch_submissions` is wrapped in `@cached`: on a miss, whatever it
        returns is written to `data/cache/sec_submissions/<cik>.parquet` and
        served to every future call, including the repair run meant to fix
        this exact fetch. Swallowing a shard failure would persist a
        partial filing history that looks complete and is not — the same
        class of bug this whole pagination fix exists to eliminate.
        `with_retry` retries connection errors up to 4 attempts; this shard
        fails every time, so raising here is the only way the next run
        actually retries instead of reading a poisoned cache forever.
        """
        good_name = "CIK0000320193-submissions-001.json"
        bad_name = "CIK0000320193-submissions-002.json"
        payload = {
            "filings": {
                "recent": _recent(["8-K"], ["2022-01-01"], ["a0"]),
                "files": [{"name": good_name}, {"name": bad_name}],
            }
        }
        good_payload = _recent(["8-K"], ["2015-01-01"], ["a1"])

        def fake_get(url: str, **kwargs: Any) -> requests.Response:
            if url == sec.SUBMISSIONS_URL.format(cik=CIK):
                return _response(payload)
            if url == sec.SUBMISSIONS_HOST + good_name:
                return _response(good_payload)
            if url == sec.SUBMISSIONS_HOST + bad_name:
                raise requests.exceptions.ConnectionError("boom")
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

        with pytest.raises(RuntimeError) as exc_info:
            sec.fetch_submissions(CIK)

        assert str(CIK) in str(exc_info.value)
        assert bad_name in str(exc_info.value)

    def test_a_failing_shard_writes_nothing_to_the_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        """A shard failure must not poison `data/cache/sec_submissions/<cik>.parquet`.

        `cached` (base.py) only writes to disk after the wrapped function
        returns; if the exception propagates instead, `to_parquet` never
        runs. This is the behaviour the coordinator's fix depends on —
        assert it directly rather than trusting the raise alone.
        """
        bad_name = "CIK0000320193-submissions-001.json"
        payload = {
            "filings": {
                "recent": _recent(["8-K"], ["2022-01-01"], ["a0"]),
                "files": [{"name": bad_name}],
            }
        }

        def fake_get(url: str, **kwargs: Any) -> requests.Response:
            if url == sec.SUBMISSIONS_URL.format(cik=CIK):
                return _response(payload)
            if url == sec.SUBMISSIONS_HOST + bad_name:
                raise requests.exceptions.ConnectionError("boom")
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

        with pytest.raises(RuntimeError):
            sec.fetch_submissions(CIK)

        assert not (tmp_path / "sec_submissions" / f"{CIK}.parquet").exists()


class TestFetch8kDatesStillFiltersToEightKs:
    """`fetch_8k_dates` must keep working once `fetch_submissions` spans shards."""

    def test_filters_across_recent_and_shard_rows(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        shard_name = "CIK0000320193-submissions-001.json"
        payload = {
            "filings": {
                "recent": _recent(["8-K", "10-Q"], ["2022-01-01", "2022-02-01"], ["a0", "a1"]),
                "files": [{"name": shard_name}],
            }
        }
        shard_payload = _recent(["8-K", "10-K"], ["2011-01-01", "2011-02-01"], ["a2", "a3"])

        def fake_get(url: str, **kwargs: Any) -> requests.Response:
            if url == sec.SUBMISSIONS_URL.format(cik=CIK):
                return _response(payload)
            if url == sec.SUBMISSIONS_HOST + shard_name:
                return _response(shard_payload)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)

        df = sec.fetch_8k_dates(CIK)

        assert set(df["filed_on"]) == {"2022-01-01", "2011-01-01"}
        assert (df["source"] == "sec_8k").all()
