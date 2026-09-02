"""An "as of now" fetcher's cache key must carry the date (2026-09-01).

**The defect this pins, measured rather than reasoned about.**
`fetch_current_constituents` keyed its cache on the constant string
`"current_constituents"`, so the first fetch answered every later one
forever. On 2026-09-01 the cached snapshot was **32 days old**
(2026-07-31), still listed `FISV` — retired in 2023 — and did not contain
`FI`. Every `cscan tickers --refresh` since July had been replaying it in
under a second, and replaying it flipped `FISV` back to `is_active = true`,
reactivating a dead ticker.

`sec.fetch_cik_lookup` had the same key shape, so new companies could never
resolve a CIK, and therefore never a market cap.

**This is the third instance of one class.** `fetch_actions` was the first
(fixed 2026-08-26, `yahoo_actions` -> `_v2`), `yahoo_daily` -> `_v2` the
one before that. CLAUDE.md's cache section states the rule; nothing
enforced it, so each sibling had to be found by hand. This test is the
enforcement.

**Why a source-code assertion rather than a behavioural one.** A cache hit
is indistinguishable from a fetch except by duration, so a behavioural test
would have to either reach the network or fake a clock across a decorator
applied at import time. The defect is visible in the key itself, which is
where it should be caught.
"""

from __future__ import annotations

import inspect
import re

from capitalscan.jobs.fetch import sec, wikipedia

# Fetchers whose answer means "as of now". A constant key freezes them.
POINT_IN_TIME_FETCHERS = [
    (wikipedia, "fetch_current_constituents"),
    (wikipedia, "fetch_membership_changes"),
    (sec, "fetch_cik_lookup"),
]

# `key_fn=lambda: "literal"` with no interpolation -- the shape that froze.
_CONSTANT_KEY = re.compile(r"key_fn\s*=\s*lambda\s*:\s*[\"'][^\"'{]*[\"']")


def _decorator_source(module, name: str) -> str:
    """The `@cached(...)` call above a function, from the module source.

    Read from the module rather than the function: `functools.wraps`
    preserves the wrapped function's source, which does not include the
    decorator line.
    """
    src = inspect.getsource(module)
    idx = src.index(f"def {name}(")
    # Walk back to the decorator that opens this function's stack.
    head = src[:idx]
    at = head.rindex("@cached")
    return head[at:]


class TestPointInTimeKeysCarryTheDate:
    def test_no_constant_key(self) -> None:
        for module, name in POINT_IN_TIME_FETCHERS:
            deco = _decorator_source(module, name)
            assert not _CONSTANT_KEY.search(deco), (
                f"{module.__name__}.{name} keys its cache on a constant string, so the "
                f"first fetch answers every later one forever. The S&P 500 list froze "
                f"for 32 days this way. Put the date in the key."
            )

    def test_the_key_interpolates_the_date(self) -> None:
        for module, name in POINT_IN_TIME_FETCHERS:
            deco = _decorator_source(module, name)
            assert "date.today()" in deco, (
                f"{module.__name__}.{name}'s cache key must include date.today() so the "
                f"entry expires; a key that cannot expire is the defect, not a slow one"
            )

    def test_the_source_string_was_bumped(self) -> None:
        """Dating the key changes what a hit means, so the namespace moves.

        Strictly, a dated key cannot collide with an undated one. The bump
        is belt-and-braces and matches the `yahoo_actions` -> `_v2`
        precedent: `CLAUDE.md` records a correct fix that merged, passed
        CI, and still produced stale data because pre-existing entries
        answered the post-fix request.
        """
        for module, name in POINT_IN_TIME_FETCHERS:
            deco = _decorator_source(module, name)
            match = re.search(r"source\s*=\s*[\"']([^\"']+)[\"']", deco)
            assert match, f"{module.__name__}.{name} has no source= in its @cached"
            assert match.group(1).endswith("_v2"), (
                f"{module.__name__}.{name} dated its key but left source= at "
                f"{match.group(1)!r}; bump it so no pre-existing entry can answer"
            )


class TestTheRuleIsStated:
    def test_wikipedia_module_explains_the_freeze(self) -> None:
        """The module docstring called this 'a once-and-frozen job', which
        is true of ADR 055's committed union file and false of the
        current-constituents fetcher. That conflation is what let the
        constant key look intentional for months."""
        doc = wikipedia.__doc__ or ""
        assert "run_tickers_refresh" in doc
        assert "constant string" in doc
