import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import type { ToolOutcome } from "@/lib/chat";

/**
 * The chat route's only way to reach data.
 *
 * ADR 118 splits the system in two: `/`, `/ticker`, and `/research` select
 * from the views through `lib/db.ts`, and anything carrying a model goes
 * through the MCP server, which calls the seven handlers, which validate.
 *
 * ```
 * /chat ──MCP──► handlers/ ──► views
 * ```
 *
 * **This module must never import `pg` or `@/lib/db`**, and
 * `tests/chat.test.ts` asserts it. A direct query here would put a number
 * in front of the model that `handlers/validate.py` never saw — no `n_eff`,
 * no interval, no q-value, and no refusal on a holdout request.
 */

/** Where the server listens. `cscan mcp serve` binds 127.0.0.1:8787. */
export const DEFAULT_MCP_URL = "http://127.0.0.1:8787/mcp";

/** A tool as the server describes it. The schema is the server's, not ours. */
export interface McpTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface McpSession {
  tools: McpTool[];
  /** The server's `instructions` field. It names ADR 112's result. */
  instructions?: string;
  call(name: string, input: unknown): Promise<ToolOutcome>;
  close(): Promise<void>;
}

type Env = Record<string, string | undefined>;

export function mcpUrl(env: Env = process.env): string {
  return env.MCP_URL || DEFAULT_MCP_URL;
}

/**
 * The bearer token, or a thrown error naming the variable.
 *
 * The server refuses to start without it (ADR 027) and answers 401 without
 * it, so a client that omitted it would surface as an authentication
 * failure several layers down. Failing here says which variable is unset.
 */
export function bearerToken(env: Env = process.env): string {
  const token = env.MCP_BEARER_TOKEN;
  if (!token) {
    throw new Error(
      "MCP_BEARER_TOKEN is not set. The chat route reaches data only through the " +
        "MCP server (ADR 118), and the server requires a token (ADR 027). " +
        "See docs/MCP_SETUP.md.",
    );
  }
  return token;
}

/**
 * Connects, lists the tools, and returns a session.
 *
 * A connection per request rather than a shared one. The server is on
 * loopback, the handshake is two round trips, and a pooled MCP session
 * carries per-session state on the server that a stale client would keep
 * alive across a restart — the failure mode being a chat route that answers
 * every question with a transport error until the process is bounced.
 */
export async function connect(env: Env = process.env): Promise<McpSession> {
  const token = bearerToken(env);
  const transport = new StreamableHTTPClientTransport(new URL(mcpUrl(env)), {
    requestInit: { headers: { Authorization: `Bearer ${token}` } },
  });
  const client = new Client({ name: "capitalscan-web", version: "0.2.0" });
  await client.connect(transport);

  const listed = await client.listTools();
  const tools: McpTool[] = listed.tools.map((tool) => ({
    name: tool.name,
    description: tool.description ?? "",
    inputSchema: tool.inputSchema as Record<string, unknown>,
  }));
  const names = new Set(tools.map((tool) => tool.name));

  return {
    tools,
    instructions: client.getInstructions(),
    async call(name: string, input: unknown): Promise<ToolOutcome> {
      // A name the server did not advertise never reaches the wire. The
      // model can only see what `listTools` returned, so this is a bug
      // check rather than a threat model — but it is the difference
      // between a clear message and an SDK protocol error.
      if (!names.has(name)) {
        throw new Error(`No such tool: ${name}. Available: ${[...names].sort().join(", ")}`);
      }
      const result = await client.callTool({
        name,
        arguments: (input ?? {}) as Record<string, unknown>,
      });
      return { text: contentToText(result.content), isError: Boolean(result.isError) };
    },
    async close() {
      await client.close();
    },
  };
}

/**
 * The tool's content blocks as one string.
 *
 * **Joining is the only thing that happens to a tool result in this
 * codebase.** `mcp/serialize.py` produced the JSON, the handler produced
 * the numbers, and neither is re-read here: no parse, no round-trip through
 * `JSON.parse`, no re-serialization that could reorder a key or narrow a
 * `numeric` to a double. Session 18.2 asks that `n_eff`, the interval, and
 * the q-value reach the model intact, and the way to guarantee that is to
 * never look at them.
 */
export function contentToText(content: unknown): string {
  if (!Array.isArray(content)) return typeof content === "string" ? content : "";
  return content
    .map((block) => {
      if (typeof block === "object" && block !== null && "text" in block) {
        const { text } = block as { text?: unknown };
        return typeof text === "string" ? text : "";
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}
