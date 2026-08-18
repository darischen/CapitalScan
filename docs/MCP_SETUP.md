# MCP server — running it, connecting to it, rotating its token

Session 16. The server wraps the seven handlers and adds no query logic
(ADR 027). Everything it can tell you, `cscan` can already tell you; the
point is that a chat client can ask.

Read `DECISIONS.md` ADR 027 and ADR 074 for why the tool count is seven and
why every enum is closed.

---

## 1. Provision the read-only role

Do this first. It is the difference between a server and a database proxy
with extra steps.

```
cscan db grant-readonly --password '<choose-one>'
```

Idempotent, and a re-run **narrows** as well as adds, so a role that was
over-granted by hand comes back to the intended set. Add `--dry-run` to see
the statements first; the password is redacted in that output.

What the role gets: `CONNECT`, `USAGE` on `public`, and `SELECT` on every
table and view, including ones a later migration adds. What it does not get:
`INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, sequence `USAGE`, `CREATEDB`,
`CREATEROLE`. Proven by
`tests/integration/test_mcp_readonly_role.py`, which runs each write verb
through the role's own connection and asserts the refusal.

Then point the server at it:

```
DATABASE_URL_MCP=postgresql://capscan_ro:<password>@localhost:5432/capitalscan
```

**Leaving `DATABASE_URL_MCP` unset works and is a development-only choice.**
The server falls back to `DATABASE_URL_RESEARCH`, prints a warning naming
this file, and runs with only one layer of read-only enforcement — the fact
that no handler writes. That is a fine way to try a tool and not a way to
deploy one.

---

## 2. Generate a bearer token

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put it in `.env.local`:

```
MCP_BEARER_TOKEN=<the value>
```

**The server refuses to start without it.** Not a warning beside a live open
endpoint — a non-zero exit. An unauthenticated MCP endpoint on the public
internet is an open database proxy (ADR 027).

### Rotating it

1. Generate a new value.
2. Update `MCP_BEARER_TOKEN` in `.env.local`.
3. Restart the server.
4. Update every client config in §4.

There is no overlap window, because there is one token. That is a real
limitation of the bearer-token minimum ADR 027 specifies: one secret means
no per-client revocation and no audience separation. `capitalscan/mcp/auth.py`
says so in its own docstring rather than implying a stronger property. If
that becomes a problem, the fix is a second token in the same comparison
loop, not a bigger secret.

**The token is never logged, never in an error, never in a traceback.** The
rate limiter keys on `sha256(token)[:16]` rather than the token, so a bucket
key that ends up in a diagnostic carries a handle.

---

## 3. Start it

```
cscan mcp serve                        # 127.0.0.1:8787
cscan mcp serve --port 9000
cscan mcp serve --host 0.0.0.0         # deliberate; says so on startup
```

`127.0.0.1` by default. Binding `0.0.0.0` on a server whose only
authentication is one shared token is a deliberate act, so it has to be
typed.

Check the tool schemas without starting anything:

```
cscan mcp tools
```

That is also how to confirm a new signal type reached the wire: the schemas
are generated from `handlers.enums`, so adding a `SignalType` member changes
them with no edit anywhere under `mcp/`.

### Behind a domain or a reverse proxy

The SDK compares the `Host` header against an allowlist and answers `421
Misdirected Request` on a miss — a DNS-rebinding guard. The default accepts
`127.0.0.1` and `localhost` only. A deployment behind `mcp.example.com` must
pass it:

```python
from capitalscan.mcp.server import build_app

app = build_app(allowed_hosts=["mcp.example.com"])
```

Miss this and every request returns 421, which reads like a routing fault
rather than a policy one.

---

## 4. Connecting a client

The endpoint is streamable HTTP at `/mcp`, with the token as a bearer
header.

### Claude Code

```
claude mcp add --transport http capitalscan http://127.0.0.1:8787/mcp \
  --header "Authorization: Bearer <token>"
```

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "capitalscan": {
      "type": "http",
      "url": "http://127.0.0.1:8787/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

### Verifying by hand

`tests/integration/test_mcp_server_live.py` drives the whole flow —
`initialize`, `tools/list`, `tools/call` — through an in-process client, and
is the fastest check that a change did not break the protocol:

```
uv run pytest capitalscan/tests/integration/test_mcp_server_live.py
```

**Verified against the live database on 2026-08-18**, config
`86e91448a65aa40b`: unauthenticated `tools/list` refused with 401,
`initialize` returned `capitalscan 0.2.0`, `tools/list` returned all seven,
and `get_stats(confluence_low, 0.03, "0-10", validate)` returned

```json
{"kind": "suppressed",
 "cell_id": "confluence_low|long|0-10|all|next_open|validate|pooled|h5|t0.03",
 "reason": "n_eff 12.8 below min_n_eff 30",
 "n_events": 61, "n_eff": 13, "min_n_eff": 30,
 "meta": {"config_hash": "86e91448a65aa40b", "as_of": "2026-08-17",
          "staleness_days": 1, "stale": false}}
```

That is the expected shape of most answers, not a failure. See §6.

---

## 5. What the seven tools are

| Tool | Returns |
|---|---|
| `screen_signals` | What fired on a date. Event feed by default (ADR 114); statistics behind `with_stats` |
| `get_stats` | One cell, or `{"kind": "suppressed"}` with the stored reason |
| `get_indicators` | A daily price and indicator series, capped at 200 points |
| `get_events` | Event history, cluster heads by default |
| `predict` | `{"kind": "not_found"}`, for every input, until Phase 6 |
| `explain_signal` | The stored state a signal fired on, and optionally its cell |
| `get_universe` | Membership and the five criteria behind it |

Seven, fixed. ADR 074: tool schemas dominate input tokens, so the count is a
cost decision as well as a correctness one. An eighth that combined two
handlers would put query logic in the wrong layer, and
`test_mcp_contract.py` fails the moment a tool makes two handler calls.

### Two shapes a client must distinguish

`{"kind": "suppressed"}` and `{"kind": "not_found"}` are tagged. A
suppressed cell and a cell measuring `p_hit = 0.0` are opposite claims —
"we cannot say" and "it never happened" — and must never arrive as two
objects differing only by which keys are null.

### Holdout

`split` accepts `train` and `validate`. The generated schema has two
members, so a client that reads it never composes a holdout request; the
handler raises as well, which is what guards the web and chat surfaces that
have no schema in front of them. Holdout is evaluated exactly once, at the
end (ADR 019, ADR 033).

---

## 6. What the numbers mean

The server's `instructions` say this to every client on connect, and it is
repeated here because it is the most important thing about the data:

> Across 630,592 events and three signal definitions, **no cell survived
> Benjamini-Hochberg correction on either split** (minimum q-value 0.706),
> and roughly 45% of cells report nothing at all because their effective
> sample is below 30.

So `{"kind": "suppressed"}` is the *common* answer from `get_stats`, and
every `CellStats` that does return carries `survives_fdr: false`. A hit rate
from these tools describes a past sample. It is not an edge, and ADR 112 is
the measurement that says so.

Every probability arrives with `n_eff`, an interval, and a q-value, because
the handler layer refuses to return one without them
(`capitalscan/handlers/validate.py`). Report them together.
