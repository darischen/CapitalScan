"""Bearer auth and rate limiting (ADR 027, session 16.2).

Session 16's gate calls items 3 and 5 the ones that matter and the rest
plumbing: "those two are the difference between a server and an open
database."

The auth tests run against a stub ASGI app rather than the real MCP
transport. The middleware is the unit under test, and standing up a
streaming transport to check a 401 would make the test slower, flakier, and
no more convincing.
"""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from capitalscan.mcp import auth
from capitalscan.mcp.auth import (
    UNAUTHORIZED_BODY,
    BearerAuthMiddleware,
    MissingToken,
    configured_token,
    token_id,
    token_is_valid,
)
from capitalscan.mcp.ratelimit import RateLimiter, RateLimitMiddleware

TOKEN = "s3cr3t-token-value-do-not-log"


def _inner_app():
    """A stand-in for the MCP transport that reports what reached it.

    Echoes the token handle so a test can prove the auth layer passed one
    down without the limiter having to be involved.
    """

    async def endpoint(request):
        return JSONResponse(
            {"reached": True, "token_id": request.scope.get("state", {}).get("mcp_token_id")}
        )

    return Starlette(routes=[Route("/mcp", endpoint, methods=["GET", "POST"])])


@pytest.fixture
def client():
    app = BearerAuthMiddleware(_inner_app(), token=TOKEN)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Gate item 3: unauthenticated requests rejected, identically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"Authorization": "Bearer"}, id="scheme_only"),
        pytest.param({"Authorization": TOKEN}, id="no_scheme"),
        pytest.param({"Authorization": "Basic " + TOKEN}, id="wrong_scheme"),
        pytest.param({"Authorization": "Bearer "}, id="empty_value"),
        pytest.param({"Authorization": "Bearer wrong"}, id="wrong_token"),
        pytest.param({"Authorization": "Bearer " + TOKEN[:-1]}, id="nearly_right"),
        pytest.param({"Authorization": "Bearer " + TOKEN.upper()}, id="wrong_case"),
    ],
)
def test_every_failure_mode_gets_the_same_response(client, headers):
    """Missing, malformed, and wrong are one response.

    A client that can tell "no token" from "wrong token" can enumerate; one
    that can tell "wrong" from "nearly right" can do worse. Status, body,
    and the `WWW-Authenticate` header are compared, not just the status.
    """
    response = client.get("/mcp", headers=headers)
    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY
    assert response.headers["www-authenticate"] == "Bearer"


def test_the_responses_are_byte_identical_to_each_other(client):
    """Not merely equal field by field - the same bytes.

    A difference anywhere in the body is a signal, including one nobody
    meant to put there.
    """
    bodies = {
        client.get("/mcp", headers=h).content
        for h in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"})
    }
    assert len(bodies) == 1


def test_a_rejected_request_never_reaches_the_inner_app(client):
    assert client.get("/mcp").json() != {"reached": True}


def test_discovery_needs_a_token_too(client):
    """The tool list describes the database's shape. That is information."""
    assert client.post("/mcp", json={"method": "tools/list"}).status_code == 401


def test_a_valid_token_passes_through(client):
    response = client.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json()["reached"] is True


def test_the_inner_app_receives_a_handle_not_the_token(client):
    """The limiter keys on a hash prefix, so a bucket key dumped into a log
    or an exception repr carries a handle rather than the secret."""
    body = client.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert body["token_id"] == token_id(TOKEN)
    assert TOKEN not in body["token_id"]


# ---------------------------------------------------------------------------
# The token is never written anywhere
# ---------------------------------------------------------------------------


def test_the_token_is_not_in_any_rejection_response(client):
    for headers in ({}, {"Authorization": f"Bearer {TOKEN}x"}, {"Authorization": "Basic y"}):
        text = client.get("/mcp", headers=headers).text
        assert TOKEN not in text
        assert TOKEN[:8] not in text


def test_the_token_is_not_in_the_unauthorized_constant():
    assert "token" not in json.dumps(UNAUTHORIZED_BODY).replace("bearer token", "")


def test_a_missing_token_is_not_named_in_a_traceback():
    """`MissingToken` explains what to set, never what the value would be."""
    with pytest.raises(MissingToken) as exc:
        configured_token(env={})
    message = str(exc.value)
    assert auth.TOKEN_ENV in message
    assert "open database proxy" in message


def test_the_server_refuses_to_start_without_a_token():
    """16.4: refuse, rather than start unauthenticated.

    A warning would scroll past while the endpoint stayed open.
    """
    with pytest.raises(MissingToken):
        configured_token(env={"MCP_BEARER_TOKEN": ""})


def test_a_configured_token_is_returned_unchanged():
    assert configured_token(env={"MCP_BEARER_TOKEN": TOKEN}) == TOKEN


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_comparison_accepts_only_the_exact_token():
    assert token_is_valid(TOKEN, TOKEN)
    assert not token_is_valid(TOKEN + "x", TOKEN)
    assert not token_is_valid(TOKEN[:-1], TOKEN)
    assert not token_is_valid("", TOKEN)


def test_comparison_uses_compare_digest():
    """Read from the source, because the property is not observable.

    A `==` here would pass every behavioural test in this file and leak the
    common prefix length through timing. Nothing but reading the call can
    tell the two apart.
    """
    import inspect

    assert "compare_digest" in inspect.getsource(token_is_valid)


def test_token_handles_differ_for_different_tokens():
    assert token_id("a") != token_id("b")
    assert len(token_id("a")) == 16


# ---------------------------------------------------------------------------
# Gate item 4: rate limiting on a fake clock
# ---------------------------------------------------------------------------


class FakeClock:
    """A clock a test moves by hand.

    16.2's acceptance is explicit that the limit must be tested "against a
    fake clock rather than by sleeping". A sleeping test is slow, flaky on a
    loaded runner, and cannot reach the reset boundary without sleeping
    through it - so it gets written to assert the trigger and skip the
    reset, and the reset is the half that breaks.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_the_limit_triggers_at_the_configured_threshold():
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_second=1.0, now=clock)
    assert [limiter.allow("t") for _ in range(3)] == [True, True, True]
    assert limiter.allow("t") is False


def test_the_limit_resets_as_the_clock_advances():
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_second=1.0, now=clock)
    for _ in range(3):
        limiter.allow("t")
    assert limiter.allow("t") is False

    clock.advance(1.0)
    assert limiter.allow("t") is True
    assert limiter.allow("t") is False

    clock.advance(60.0)
    assert limiter.remaining("t") == 0.0  # not refilled until the next call
    assert limiter.allow("t") is True


def test_the_bucket_does_not_refill_past_its_capacity():
    """A token bucket, not a fixed window.

    A fixed window lets a caller spend the whole budget in the last second
    of one window and the whole budget in the first second of the next,
    which is twice the rate the number claims.
    """
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_second=1.0, now=clock)
    clock.advance(10_000.0)
    assert limiter.allow("t") is True
    assert limiter.remaining("t") == 2.0


def test_limits_are_per_token_not_shared():
    """ADR 027 says per token. One operator behind one address means an IP
    limit is either so loose it never fires or throttles the only
    legitimate caller."""
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_per_second=1.0, now=clock)
    assert limiter.allow("a") and limiter.allow("a")
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True


def test_a_new_caller_starts_with_a_full_bucket():
    """Starting empty would rate-limit the first request of every session,
    which reads as an outage."""
    limiter = RateLimiter(capacity=5, now=FakeClock())
    assert limiter.remaining("fresh") == 5.0


def test_a_clock_that_steps_backwards_does_not_drain_every_bucket():
    """`time.monotonic` cannot step back; a caller-supplied clock can.

    A negative `elapsed` would subtract tokens from whatever bucket was
    touched next, so the guard is clamped rather than assumed.
    """
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_second=1.0, now=clock)
    limiter.allow("t")
    clock.advance(-500.0)
    assert limiter.allow("t") is True
    assert limiter.remaining("t") == 1.0


def test_the_middleware_returns_429_when_the_bucket_is_empty():
    clock = FakeClock()
    app = BearerAuthMiddleware(
        RateLimitMiddleware(_inner_app(), RateLimiter(capacity=1, now=clock)), token=TOKEN
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/mcp", headers=headers).status_code == 200
    second = client.get("/mcp", headers=headers)
    assert second.status_code == 429
    assert "rate" in second.json()["error"]


def test_an_unauthenticated_request_never_allocates_a_bucket():
    """Auth is outermost on purpose.

    If the limiter ran first, an anonymous caller could allocate one bucket
    per forged token and exhaust memory without ever authenticating.
    """
    clock = FakeClock()
    limiter = RateLimiter(capacity=1, now=clock)
    app = BearerAuthMiddleware(RateLimitMiddleware(_inner_app(), limiter), token=TOKEN)
    client = TestClient(app)
    for _ in range(50):
        client.get("/mcp", headers={"Authorization": "Bearer wrong"})
    assert limiter._buckets == {}
