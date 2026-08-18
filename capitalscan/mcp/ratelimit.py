"""Per-token rate limiting on an injectable clock (ADR 027, session 16.2).

**Per token, not per IP.** ADR 027 says so, and the reason is that one
operator behind one address is the expected deployment: an IP limit would
either be so loose it never fires or would throttle the only legitimate
caller. A token is the unit that can be revoked, so it is the unit that gets
budgeted.

**The clock is a parameter.** 16.2's acceptance says the limit must trigger
and reset "tested against a fake clock rather than by sleeping". A test that
sleeps is slow, flaky on a loaded runner, and cannot exercise the reset
boundary without sleeping through it - so it gets written to assert the
trigger and not the reset, and the reset is the half that breaks.

A token bucket rather than a fixed window. A fixed window lets a caller
spend the whole budget in the last second of one window and the whole budget
in the first second of the next, which is twice the rate the number claims.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# Sized for a person driving a research tool through a chat client, not for
# a service. ADR 074 already caps a single response at 200 rows, so this
# bounds request *count* rather than payload.
DEFAULT_CAPACITY = 60
DEFAULT_REFILL_PER_SECOND = 1.0

TOO_MANY_BODY = {
    "error": "rate_limited",
    "message": "Too many requests. Retry shortly.",
}


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


@dataclass
class RateLimiter:
    """A token bucket per caller, on a caller-supplied clock.

    `now` defaults to `time.monotonic` rather than `time.time`: a wall clock
    can step backwards over an NTP correction or a DST change, and a
    backwards step makes `elapsed` negative, which would *remove* tokens
    from every bucket at once. Monotonic cannot.
    """

    capacity: int = DEFAULT_CAPACITY
    refill_per_second: float = DEFAULT_REFILL_PER_SECOND
    now: Callable[[], float] = time.monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        """Spend one token for `key`. False when the bucket is empty."""
        current = self.now()
        bucket = self._buckets.get(key)
        if bucket is None:
            # A new caller starts full. Starting empty would rate-limit the
            # first request of every session, which reads as an outage.
            bucket = _Bucket(tokens=float(self.capacity), last_refill=current)
            self._buckets[key] = bucket

        elapsed = max(0.0, current - bucket.last_refill)
        bucket.tokens = min(float(self.capacity), bucket.tokens + elapsed * self.refill_per_second)
        bucket.last_refill = current

        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def remaining(self, key: str) -> float:
        bucket = self._buckets.get(key)
        return float(self.capacity) if bucket is None else bucket.tokens


class RateLimitMiddleware:
    """ASGI middleware, downstream of `BearerAuthMiddleware`.

    Reads the token *handle* the auth layer put on the scope, never the
    Authorization header. A limiter that parsed the header itself would be a
    second place the raw secret lives, and a bucket key ends up in
    diagnostics far more often than a header does.

    Raw ASGI for the same reason as the auth middleware: the MCP transport
    streams, and `BaseHTTPMiddleware` would buffer it.
    """

    def __init__(self, app: ASGIApp, limiter: RateLimiter | None = None) -> None:
        self.app = app
        self.limiter = limiter or RateLimiter()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # No handle means the request never passed auth, which cannot happen
        # in the assembled app. Falling back to a shared "anonymous" bucket
        # rather than raising: if the middleware order is ever changed, the
        # failure should be a throttle, not a 500 that reveals the mistake
        # to the caller.
        key = scope.get("state", {}).get("mcp_token_id", "anonymous")
        if not self.limiter.allow(key):
            response = JSONResponse(TOO_MANY_BODY, status_code=429)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
