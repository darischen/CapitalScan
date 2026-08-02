"""Fetcher contract: retry, rate limiting, and disk caching (DESIGN §4.2).

All external IO routes through this module so retry, rate limiting, and
caching exist once rather than being reinvented per source. Individual
fetchers in this package (`yahoo`, `sec`, `finnhub`, `wikipedia`)
compose `with_retry`, `rate_limited`, and `cached` around plain functions
that return a `pd.DataFrame`.
"""

from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar

import pandas as pd
import requests

T = TypeVar("T")

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_ROOT = REPO_ROOT / "data" / "cache"


class Fetcher(Protocol):
    name: str
    rate_limit_per_sec: float

    def fetch(self, **kwargs: Any) -> pd.DataFrame: ...


class NotFoundError(Exception):
    """The requested entity does not exist (HTTP 404 or source-specific 404).

    Never retried — a missing ticker or CIK is a `bar_rejects` row, not a
    network problem (DESIGN §4.2).
    """


def _status_code(exc: BaseException) -> int | None:
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return getattr(exc, "status_code", None)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, NotFoundError):
        return False
    status = _status_code(exc)
    if status is not None:
        if status == 404:
            return False
        return status == 429 or status >= 500
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


def with_retry(
    fn: Callable[..., T] | None = None,
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    jitter: bool = True,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """Retry with exponential backoff at 2/4/8/16 s (DESIGN §4.2).

    Retries connection errors, timeouts, HTTP 429, and HTTP 5xx. Never
    retries HTTP 404 or `NotFoundError` — that is not a transient failure,
    it means the ticker does not exist, and it belongs in `bar_rejects`.

    `sleep` defaults to `time.sleep`, resolved **at call time** (via the
    module-level `time` import) rather than bound as a default argument —
    fetcher modules apply this decorator at import time, before a test's
    monkeypatch of `time.sleep` would exist to bind.
    """

    def decorator(inner: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(inner)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            _sleep = sleep if sleep is not None else time.sleep
            for attempt in range(attempts):
                try:
                    return inner(*args, **kwargs)
                except Exception as exc:
                    if not _is_retryable(exc) or attempt == attempts - 1:
                        raise
                    delay = base_delay * (2**attempt)
                    if jitter:
                        delay *= 1 + random.random()
                    _sleep(delay)
            raise AssertionError("unreachable")  # pragma: no cover

        return wrapper

    return decorator(fn) if fn is not None else decorator


def rate_limited(
    fn: Callable[..., T] | None = None,
    *,
    per_sec: float,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> Any:
    """Throttle calls to at most `per_sec` per second.

    State (last-call timestamp) lives on the wrapper closure, so it is
    shared across all calls made through the decorated function — the
    unit a rate limit in DESIGN §4.2's table actually applies to. `sleep`
    and `clock` default to `time.sleep` / `time.monotonic`, resolved at
    call time for the same reason as in `with_retry`.
    """

    def decorator(inner: Callable[..., T]) -> Callable[..., T]:
        min_interval = 1.0 / per_sec
        last_call: list[float] = [float("-inf")]

        @functools.wraps(inner)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            _sleep = sleep if sleep is not None else time.sleep
            _clock = clock if clock is not None else time.monotonic
            wait = last_call[0] + min_interval - _clock()
            if wait > 0:
                _sleep(wait)
            try:
                return inner(*args, **kwargs)
            finally:
                last_call[0] = _clock()

        return wrapper

    return decorator(fn) if fn is not None else decorator


def cache_path(source: str, key: str, cache_root: Path = CACHE_ROOT) -> Path:
    return cache_root / source / f"{key}.parquet"


def cached(
    fn: Callable[..., pd.DataFrame] | None = None,
    *,
    source: str,
    key_fn: Callable[..., str],
    cache_root: Path | None = None,
) -> Any:
    """Cache a fetcher's `DataFrame` result to `data/cache/{source}/{key}.parquet`.

    Written before any downstream parsing depends on it, so a backfill
    re-run costs zero network calls (DESIGN §4.2). `key_fn` receives the
    same arguments as the wrapped function and must return a
    filesystem-safe cache key.

    `cache_root` defaults to this module's `CACHE_ROOT` **looked up at call
    time**, not decoration time — that is what lets tests monkeypatch
    `capitalscan.jobs.fetch.base.CACHE_ROOT` to a tmp directory even though
    fetcher modules apply this decorator at import time.
    """

    def decorator(inner: Callable[..., pd.DataFrame]) -> Callable[..., pd.DataFrame]:
        @functools.wraps(inner)
        def wrapper(*args: Any, **kwargs: Any) -> pd.DataFrame:
            root = cache_root if cache_root is not None else CACHE_ROOT
            key = key_fn(*args, **kwargs)
            path = cache_path(source, key, root)
            if path.exists():
                return pd.read_parquet(path)
            result = inner(*args, **kwargs)
            path.parent.mkdir(parents=True, exist_ok=True)
            result.to_parquet(path)
            return result

        return wrapper

    return decorator(fn) if fn is not None else decorator
