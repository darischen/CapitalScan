"""The MCP server: seven tools, bearer auth, per-token rate limiting.

ADR 027 in one sentence: the MCP server wraps **the same tools**, and an
unauthenticated MCP endpoint on the public internet is an open database
proxy.

**Assembly order matters and is asserted.** Auth is outermost, so an
unauthenticated request is rejected before the rate limiter allocates a
bucket for it - otherwise an anonymous caller could exhaust memory one
forged token at a time. The rate limiter sits between auth and the
transport, so it keys on the handle auth put on the scope rather than
re-parsing the header.

    BearerAuthMiddleware -> RateLimitMiddleware -> MCP streamable HTTP app

**Read-only is enforced twice.** Once because no handler writes, and once
because the connection should not be able to. `cscan db grant-readonly`
creates the role and `DATABASE_URL_MCP` points at it; the server logs which
of the two URLs it resolved so an operator can see, on startup, whether the
second layer is actually in place. Defense in depth is the point: a future
handler bug should not be able to write.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from capitalscan.mcp.auth import BearerAuthMiddleware, configured_token
from capitalscan.mcp.errors import to_tool_error
from capitalscan.mcp.ratelimit import RateLimiter, RateLimitMiddleware
from capitalscan.mcp.tools import TOOLS

SERVER_NAME = "capitalscan"

# The env var pointing at the read-only role. Falls back to the research URL
# with a visible warning rather than refusing: a developer running the
# server against a local database should not have to provision a second
# role to try a tool, and a deployment that skipped it should not be able
# to do so quietly.
READONLY_URL_ENV = "DATABASE_URL_MCP"
RESEARCH_URL_ENV = "DATABASE_URL_RESEARCH"

INSTRUCTIONS = """\
CapitalScan is an event-study database over US mega-cap equities, 2010 to
present. It reports historical frequencies conditional on past indicator
states. It does not forecast, and it holds no model.

Read this before quoting a number from it. Across 630,592 events and three
signal definitions, **no cell survived Benjamini-Hochberg correction on
either split** (minimum q-value 0.706), and roughly 45% of cells report
nothing at all because their effective sample is below 30. A hit rate from
these tools is a description of a past sample, not an edge.

Every probability arrives with `n_eff`, a confidence interval, and a
q-value. Report them together or not at all. A `"kind": "suppressed"`
result means insufficient data; do not substitute a broader cell.

The holdout split is not readable through any tool.\
"""


def build_mcp_server(tools: dict[str, Any] | None = None) -> MCPServer:
    """The bare `MCPServer` with the seven tools registered.

    Separate from `build_app` so a test can list tools and read schemas
    without standing up a transport or an auth token.
    """
    server = MCPServer(
        name=SERVER_NAME,
        version="0.2.0",
        instructions=INSTRUCTIONS,
        # Duplicates are a programming error here, not a runtime condition:
        # `TOOLS` is a dict, so a duplicate name is impossible to express.
        # Warning on it would be noise.
        warn_on_duplicate_tools=False,
    )
    for name, fn in (tools or TOOLS).items():
        server.add_tool(fn, name=name)

    # Registered on the MCP app rather than beside it, so it sits *inside*
    # the auth wrapper. 16.4 asks for a health endpoint that "requires auth
    # and returns no data": an unauthenticated liveness probe is a way to
    # learn the server exists and is holding a database open.
    @server.custom_route("/health", methods=["GET"])
    async def _health(request: Request) -> JSONResponse:
        return JSONResponse(health_payload())

    return server


def resolve_database_url(env: dict[str, str] | None = None) -> tuple[str, bool]:
    """The URL the server will read through, and whether it is the read-only one.

    Returns the flag rather than logging inside, so the caller decides how
    loudly to say it and a test can assert the decision without capturing
    output.
    """
    source = os.environ if env is None else env
    url = source.get(READONLY_URL_ENV, "")
    if url:
        return url, True
    return source.get(RESEARCH_URL_ENV, ""), False


def build_app(
    token: str | None = None,
    limiter: RateLimiter | None = None,
    server: MCPServer | None = None,
    streamable_http_path: str = "/mcp",
    allowed_hosts: list[str] | None = None,
) -> ASGIApp:
    """The full ASGI app: transport, wrapped in rate limiting, wrapped in auth.

    `token=None` reads `MCP_BEARER_TOKEN` and raises `MissingToken` if it is
    unset. The server refuses to start rather than starting unauthenticated;
    a warning would scroll past while the endpoint stayed open.

    `allowed_hosts` is the SDK's DNS-rebinding guard: it compares the `Host`
    header against a list and answers `421 Misdirected Request` on a miss.
    Default `None` keeps the SDK's own default, which accepts `127.0.0.1`
    and `localhost`. **A deployment behind a domain name or a reverse proxy
    must pass its hostname here**, or every request arrives as a 421 that
    looks like a routing fault rather than a policy one - which is exactly
    how it presented the first time, as `Invalid Host header: testserver`
    from an in-process test client.
    """
    mcp_server = server or build_mcp_server()

    # **Not mounted inside another Starlette app.** The transport's session
    # manager is started by its app's *lifespan*, and a sub-app mounted with
    # `Starlette.mount` never receives lifespan events - the parent keeps
    # them. Wrapping it that way produced a server that authenticated
    # correctly, accepted the request, and then failed every `initialize`
    # with `RuntimeError: Task group is not initialized`, which reads like
    # an SDK bug rather than an assembly mistake. Found by running it.
    #
    # The middleware below are plain ASGI callables that pass every
    # non-HTTP scope straight through, so `lifespan` reaches the transport
    # app unchanged.
    security = (
        TransportSecuritySettings(allowed_hosts=list(allowed_hosts))
        if allowed_hosts is not None
        else None
    )
    inner: ASGIApp = mcp_server.streamable_http_app(
        streamable_http_path=streamable_http_path, transport_security=security
    )
    limited: ASGIApp = RateLimitMiddleware(inner, limiter=limiter)
    return BearerAuthMiddleware(limited, token=token if token is not None else configured_token())


def health_payload() -> dict[str, str]:
    """What `/health` returns: that the server is up, and nothing else.

    No row counts, no config hash, no last-bar date, no ticker list. 16.4
    asks for a health endpoint that "requires auth and returns no data", and
    every one of those would be data - a liveness probe should not double as
    an unauthenticated way to learn when the database was last updated.
    """
    return {"status": "ok"}


def run(host: str = "127.0.0.1", port: int = 8787) -> None:  # pragma: no cover - process entry
    """Serve over streamable HTTP. Blocks.

    `127.0.0.1` by default. Binding `0.0.0.0` is a deliberate act on a
    server whose only authentication is one shared bearer token, so it is
    not the default and the CLI asks for it explicitly.
    """
    import uvicorn

    uvicorn.run(build_app(), host=host, port=port, log_level="info")


__all__ = [
    "INSTRUCTIONS",
    "SERVER_NAME",
    "build_app",
    "build_mcp_server",
    "health_payload",
    "resolve_database_url",
    "run",
    "to_tool_error",
]
