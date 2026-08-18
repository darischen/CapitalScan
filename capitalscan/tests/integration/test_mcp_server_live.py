"""The assembled server, driven over the real protocol (session 16 gate 3, 9, 10).

`TestClient` as a **context manager**, which is the whole reason this file
exists rather than more unit tests. The transport's session manager is
started by the app's lifespan; entering the context runs it, and everything
about `initialize` fails without it. That is not a hypothetical:
`build_app` originally mounted the transport inside another `Starlette`,
which never forwards lifespan to a sub-app, and the result authenticated
correctly, accepted the request, and failed every `initialize` with
`RuntimeError: Task group is not initialized`. No unit test in this
repository would have caught it.

Needs a database because the tools reach one. On CI the container is
migrated and empty, so the assertions here are about *shape and refusal*
rather than about numbers - a populated result is asserted in the
handler tests, and a real measured one was verified by hand on 2026-08-18
against `86e91448a65aa40b`.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient

from capitalscan.jobs import db_io
from capitalscan.mcp.server import build_app

TOKEN = "integration-token-not-a-secret"
URL = "/mcp"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


def _parse(response) -> dict:
    """The body, whether it arrived as JSON or as one SSE frame.

    Streamable HTTP answers a single request either way depending on what
    the client accepted, and a helper that assumed one of them would pass
    locally and fail on a client that negotiated the other.
    """
    body = response.text
    if "data:" in body:
        for line in body.splitlines():
            if line.startswith("data:"):
                return dict(json.loads(line[5:].strip()))
    return dict(response.json())


@pytest.fixture
def client():
    """`allowed_hosts` includes `testserver` because that is what
    `TestClient` sends as its `Host`.

    The SDK guards against DNS rebinding by comparing the Host header and
    answering `421 Misdirected Request` on a miss. Left at the default this
    fixture produced a wall of 421s reading `Invalid Host header:
    testserver`, which looks like a routing fault. The same setting is what
    a deployment behind a domain name has to pass, so exercising it here is
    not only a test convenience.
    """
    with TestClient(build_app(token=TOKEN, allowed_hosts=["testserver"])) as c:
        yield c


@pytest.fixture
def session(client):
    """An initialized session, which every method past `initialize` needs."""
    response = client.post(
        URL,
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "capitalscan-tests", "version": "0"},
            },
        },
    )
    assert response.status_code == 200, response.text
    headers = dict(HEADERS)
    sid = response.headers.get("mcp-session-id")
    if sid:
        headers["mcp-session-id"] = sid
    client.post(
        URL, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    return headers


def _call(client, headers, name, arguments, request_id=99):
    return _parse(
        client.post(
            URL,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
    )


# ---------------------------------------------------------------------------
# Gate item 3: nothing without a token, discovery included
# ---------------------------------------------------------------------------


def test_initialize_is_refused_without_a_token(client):
    response = client.post(
        URL, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert response.status_code == 401


def test_discovery_is_refused_without_a_token(client):
    """The tool list describes the database's shape. That is information."""
    response = client.post(URL, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert response.status_code == 401


def test_health_is_refused_without_a_token(client):
    """16.4: a health endpoint that requires auth and returns no data.

    An unauthenticated liveness probe is a way to learn the server exists
    and is holding a database open.
    """
    assert client.get("/health").status_code == 401


def test_health_returns_only_liveness(client):
    response = client.get("/health", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Gate item 9: the protocol works end to end
# ---------------------------------------------------------------------------


def test_initialize_returns_the_server_identity(client):
    response = client.post(
        URL,
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert _parse(response)["result"]["serverInfo"]["name"] == "capitalscan"


def test_tools_list_returns_the_seven(client, session):
    listed = _parse(
        client.post(URL, headers=session, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    )
    names = sorted(t["name"] for t in listed["result"]["tools"])
    assert names == [
        "explain_signal",
        "get_events",
        "get_indicators",
        "get_stats",
        "get_universe",
        "predict",
        "screen_signals",
    ]


def test_a_live_tool_call_returns_a_structured_result(client, session):
    result = _call(
        client,
        session,
        "get_stats",
        {
            "signal_type": "confluence_low",
            "target_pct": 0.03,
            "dd_bucket": "0-10",
            "split": "validate",
        },
    )["result"]
    assert result["isError"] is False
    payload = result["structuredContent"]
    # Either a measured cell or a suppression - both are correct answers and
    # which one depends on the data behind the database under test. What is
    # asserted is that the union is *distinguishable*, which is gate item 7.
    assert payload.get("kind") in (None, "suppressed")
    assert "meta" in payload and "config_hash" in payload["meta"]


def test_meta_survives_the_wire(client, session):
    """Gate item 8. A client cannot render a staleness banner it never
    receives."""
    payload = _call(client, session, "predict", {"ticker": "TSM"})["result"]["structuredContent"]
    assert payload["kind"] == "not_found"
    assert "staleness_days" in payload["meta"]


def test_predict_is_not_found_over_the_wire(client, session):
    payload = _call(client, session, "predict", {"ticker": "TSM"})["result"]["structuredContent"]
    assert payload["kind"] == "not_found"
    assert "No model exists" in payload["reason"]


# ---------------------------------------------------------------------------
# Holdout, refused twice
# ---------------------------------------------------------------------------


def test_holdout_is_refused_at_the_schema(client, session):
    """Earlier than the handler, and that is the intended order.

    The generated schema types `split` as a two-member literal, so a request
    for holdout fails validation before any handler runs. The handler's
    raise is still there and still carries ADR 019's reasoning - it guards
    the web and chat surfaces, which have no schema in front of them.

    The consequence is that an MCP client sees a validation error rather
    than the ADR text. That is why the tool description explains the refusal
    in prose: a model reading the schema learns holdout is not an option,
    and a model reading the description learns why.
    """
    result = _call(
        client,
        session,
        "get_stats",
        {
            "signal_type": "confluence_low",
            "target_pct": 0.03,
            "dd_bucket": "0-10",
            "split": "holdout",
        },
    )["result"]
    assert result["isError"] is True
    text_out = json.dumps(result)
    assert "train" in text_out and "validate" in text_out


def test_the_tool_description_says_why_holdout_is_refused(client, session):
    listed = _parse(
        client.post(URL, headers=session, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    )
    stats = next(t for t in listed["result"]["tools"] if t["name"] == "get_stats")
    assert "holdout is refused" in stats["description"]
    assert "exactly once" in stats["description"]


def test_no_response_leaks_a_table_name_or_a_path(client, session):
    """16.3: no serialized error contains SQL, a table name, or a file path."""
    bad = _call(
        client,
        session,
        "get_stats",
        {
            "signal_type": "confluence_low",
            "target_pct": 0.04,  # not in StatsParams.reach_targets
            "dd_bucket": "0-10",
            "split": "train",
        },
    )
    body = json.dumps(bad)
    for leak in ("SELECT ", "cell_stats", "postgresql://", "C:\\\\", "/capitalscan/handlers"):
        assert leak not in body, f"{leak!r} leaked over the wire"


# ---------------------------------------------------------------------------
# Gate item 10: determinism
# ---------------------------------------------------------------------------


def test_identical_requests_return_identical_responses(client, session):
    args = {"ticker": "TSM", "as_of": "2026-08-14"}
    first = _call(client, session, "predict", args, request_id=1)["result"]
    second = _call(client, session, "predict", args, request_id=2)["result"]
    assert first == second
