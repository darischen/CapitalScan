"""Unit tests for the retry / rate-limit / cache contract (DESIGN §4.2)."""

from __future__ import annotations

import pandas as pd
import pytest
import requests

from capitalscan.jobs.fetch.base import NotFoundError, cached, rate_limited, with_retry


def _http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


class TestWithRetry:
    def test_succeeds_without_retry(self):
        calls = []

        @with_retry(sleep=lambda s: None)
        def fn():
            calls.append(1)
            return "ok"

        assert fn() == "ok"
        assert len(calls) == 1

    def test_retries_on_connection_error_then_succeeds(self):
        attempts = {"n": 0}

        @with_retry(attempts=4, sleep=lambda s: None)
        def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise requests.exceptions.ConnectionError("boom")
            return "ok"

        assert fn() == "ok"
        assert attempts["n"] == 3

    def test_retries_on_429_and_5xx(self):
        for status in (429, 500, 503):
            attempts = {"n": 0}

            @with_retry(attempts=2, sleep=lambda s: None)
            def fn(status=status):
                attempts["n"] += 1
                if attempts["n"] < 2:
                    raise _http_error(status)
                return "ok"

            assert fn() == "ok"

    def test_never_retries_404(self):
        attempts = {"n": 0}

        @with_retry(attempts=4, sleep=lambda s: None)
        def fn():
            attempts["n"] += 1
            raise _http_error(404)

        with pytest.raises(requests.exceptions.HTTPError):
            fn()
        assert attempts["n"] == 1

    def test_never_retries_not_found_error(self):
        attempts = {"n": 0}

        @with_retry(attempts=4, sleep=lambda s: None)
        def fn():
            attempts["n"] += 1
            raise NotFoundError("TICKERDOESNOTEXIST")

        with pytest.raises(NotFoundError):
            fn()
        assert attempts["n"] == 1

    def test_raises_after_exhausting_attempts(self):
        attempts = {"n": 0}

        @with_retry(attempts=3, sleep=lambda s: None)
        def fn():
            attempts["n"] += 1
            raise requests.exceptions.Timeout("slow")

        with pytest.raises(requests.exceptions.Timeout):
            fn()
        assert attempts["n"] == 3

    def test_backoff_schedule_is_exponential(self):
        delays = []

        @with_retry(attempts=4, base_delay=2.0, jitter=False, sleep=delays.append)
        def fn():
            raise requests.exceptions.ConnectionError("boom")

        with pytest.raises(requests.exceptions.ConnectionError):
            fn()
        assert delays == [2.0, 4.0, 8.0]


class TestRateLimited:
    def test_throttles_calls_to_the_configured_rate(self):
        clock = {"t": 0.0}
        sleeps: list[float] = []

        def fake_clock():
            return clock["t"]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["t"] += seconds

        @rate_limited(per_sec=2.0, sleep=fake_sleep, clock=fake_clock)
        def fn():
            clock["t"] += 0.01
            return "ok"

        fn()
        fn()
        fn()

        # min_interval = 0.5s; first call pays no wait, later calls do.
        assert sleeps
        assert all(s >= 0 for s in sleeps)
        assert clock["t"] >= 1.0

    def test_state_is_shared_across_calls_not_reset(self):
        clock = {"t": 0.0}

        @rate_limited(
            per_sec=1.0,
            sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
            clock=lambda: clock["t"],
        )
        def fn():
            return clock["t"]

        first = fn()
        second = fn()
        assert second - first >= 1.0 - 1e-9


class TestCached:
    def test_second_call_reads_from_disk_without_invoking_wrapped_fn(self, tmp_path):
        calls = {"n": 0}

        @cached(source="test_source", key_fn=lambda ticker: ticker, cache_root=tmp_path)
        def fetch(ticker: str) -> pd.DataFrame:
            calls["n"] += 1
            return pd.DataFrame({"ticker": [ticker], "value": [1]})

        first = fetch("TSM")
        second = fetch("TSM")

        assert calls["n"] == 1
        pd.testing.assert_frame_equal(first, second)

    def test_different_keys_do_not_collide(self, tmp_path):
        @cached(source="test_source", key_fn=lambda ticker: ticker, cache_root=tmp_path)
        def fetch(ticker: str) -> pd.DataFrame:
            return pd.DataFrame({"ticker": [ticker]})

        tsm = fetch("TSM")
        nvda = fetch("NVDA")
        assert tsm["ticker"].iloc[0] == "TSM"
        assert nvda["ticker"].iloc[0] == "NVDA"

    def test_writes_to_source_scoped_directory(self, tmp_path):
        @cached(source="my_source", key_fn=lambda: "key", cache_root=tmp_path)
        def fetch() -> pd.DataFrame:
            return pd.DataFrame({"a": [1]})

        fetch()
        assert (tmp_path / "my_source" / "key.parquet").exists()
