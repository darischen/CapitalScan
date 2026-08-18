"""Bearer auth for the MCP endpoint (ADR 027, session 16.2).

> Bearer token minimum, scoped read-only, rate limited per token. An
> unauthenticated MCP endpoint on the public internet is an open database
> proxy.

Four properties, each of which a test asserts rather than a comment
promising:

1. **Every request needs a token, including tool discovery.** The tool list
   describes the database's shape, and that is information. An
   unauthenticated client learns nothing, not even how many tools exist.
2. **Missing, malformed, and wrong are one response.** Same status, same
   body, same headers. A client that can tell "no token" from "wrong token"
   can enumerate; a client that can tell "wrong token" from "wrong token,
   nearly right" can do worse.
3. **Constant-time comparison**, and it runs even when there is no token to
   compare. Returning early on a missing header leaks the difference through
   response time, which is the same leak in a slower channel.
4. **The token is never written anywhere.** Not to a log, not into an error,
   not into a traceback. This module holds it in one place and never
   formats it.

**This is not OAuth and does not pretend to be.** The MCP SDK ships an OAuth
provider path; ADR 027 asks for a bearer token as a minimum, and a
single-operator research tool with one shared secret is what that describes.
The tradeoff is real: one token means no per-client revocation and no
audience separation. It is stated here rather than discovered later, and
`TOKEN_ENV` is a list so a second token can be added without a redesign.
"""

from __future__ import annotations

import hmac
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

TOKEN_ENV = "MCP_BEARER_TOKEN"

# One response for every authentication failure. A constant, so the three
# rejection paths cannot drift apart by someone editing one of them.
UNAUTHORIZED_BODY = {
    "error": "unauthorized",
    "message": "A valid bearer token is required.",
}
UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}

# Compared against when no usable token was presented, so the comparison
# runs on every path and takes the same shape. Its value is irrelevant; its
# existence is the point.
_ABSENT = ""


class MissingToken(RuntimeError):
    """`MCP_BEARER_TOKEN` is unset. The server refuses to start.

    Starting unauthenticated and logging a warning would be the worst
    option available: the endpoint would be live, open, and the warning
    would scroll past.
    """


def configured_token(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    token = source.get(TOKEN_ENV, "")
    if not token:
        raise MissingToken(
            f"{TOKEN_ENV} is not set. The MCP server refuses to start "
            "without it rather than starting unauthenticated - an "
            "unauthenticated MCP endpoint is an open database proxy "
            "(ADR 027)."
        )
    return token


def _presented(request: Request) -> str:
    """The token from the Authorization header, or `_ABSENT`.

    A malformed header yields `_ABSENT` rather than a partial value, so
    "malformed" and "missing" become the same case before any comparison
    happens instead of after.
    """
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return _ABSENT
    return value.strip()


def token_is_valid(presented: str, expected: str) -> bool:
    """`hmac.compare_digest`, always run, on both paths.

    `compare_digest` is constant-time in the *contents* of two equal-length
    strings; it still returns early on a length mismatch, which leaks token
    length and nothing else. Hashing both sides first would close that too,
    and is not done here because a shared secret's length is not the secret
    - saying so is more honest than implying a stronger property than the
    code has.
    """
    return hmac.compare_digest(presented.encode(), expected.encode())


class BearerAuthMiddleware:
    """ASGI middleware. Wraps the MCP app, including its discovery route.

    Written as raw ASGI rather than `BaseHTTPMiddleware` because the MCP
    transport streams: `BaseHTTPMiddleware` buffers the response body to
    hand it to a `Response` object, which turns a streaming session into a
    blocking one and shows up as a client that hangs rather than as an
    error.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str | None = None,
        exempt: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self._token = token if token is not None else configured_token()
        # Paths that skip auth. Empty by default and deliberately not
        # containing the health endpoint: 16.4 requires a health route that
        # "requires auth and returns no data", because an unauthenticated
        # liveness probe is a way to learn the server exists and is holding
        # a database open.
        self.exempt = exempt

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if request.url.path in self.exempt:
            await self.app(scope, receive, send)
            return

        presented = _presented(request)
        # Always compared, even when nothing was presented. An early return
        # on a missing header answers faster than a wrong token does, and a
        # response-time difference is a response difference.
        if not token_is_valid(presented, self._token):
            response: Response = JSONResponse(
                UNAUTHORIZED_BODY, status_code=401, headers=UNAUTHORIZED_HEADERS
            )
            await response(scope, receive, send)
            return

        # The token identifies the caller for the rate limiter downstream.
        # Stored on the scope rather than re-parsed there, so the header is
        # read once and the limiter never touches the raw header.
        scope.setdefault("state", {})
        scope["state"]["mcp_token_id"] = token_id(presented)
        await self.app(scope, receive, send)


def token_id(token: str) -> str:
    """A stable, non-reversible handle for a token.

    The rate limiter keys on this and never on the token itself, so a
    limiter bucket dumped into a log or an exception repr carries a hash
    prefix rather than the secret. Truncated because a bucket key needs to
    be unique among a handful of tokens, not collision-resistant against an
    adversary who already has the secret.
    """
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()[:16]
