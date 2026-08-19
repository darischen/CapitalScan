import Anthropic from "@anthropic-ai/sdk";

import {
  runTurn,
  sanitizeHistory,
  type ChatEvent,
  type ContentBlock,
} from "@/lib/chat";
import { connect } from "@/lib/mcp";
import { systemPrompt, withServerInstructions } from "@/lib/prompt";

/**
 * `POST /api/chat` — one user turn, streamed back as server-sent events.
 *
 * The only route in this app that reaches data through the MCP server
 * rather than through `lib/db.ts`, and the only one that carries a model.
 * ADR 118 makes that the same statement twice: MCP is for LLM callers.
 *
 * ```
 * browser ──► this route ──► MCP ──► handlers/ ──► views
 * ```
 *
 * **The route holds no tool schemas and no prompt text.** Both are read at
 * request time — schemas from the server, prompt from
 * `docs/SYSTEM_PROMPT.md`. There is one contract and one text, and neither
 * is restated here where it could drift.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Long enough for a model that fetches a few cells and writes them up. */
const MAX_TOKENS = 2048;

const DEFAULT_MODEL = "claude-sonnet-4-6";

function encodeEvent(event: Record<string, unknown>): Uint8Array {
  return new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`);
}

/** Connection strings never reach a browser, on any path. */
function redact(message: string): string {
  return message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]");
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "body must be JSON" }), { status: 400 });
  }

  const { messages: raw, message } = (body ?? {}) as { messages?: unknown; message?: unknown };
  const history = sanitizeHistory(raw);
  const question = typeof message === "string" ? message.trim() : "";
  if (!question) {
    return new Response(JSON.stringify({ error: "message must be a non-empty string" }), {
      status: 400,
    });
  }

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: Record<string, unknown>) => {
        try {
          controller.enqueue(encodeEvent(event));
        } catch {
          // The client went away mid-turn. Nothing to do; the loop's own
          // abort signal ends it.
        }
      };

      let session: Awaited<ReturnType<typeof connect>> | undefined;
      try {
        const apiKey = process.env.ANTHROPIC_API_KEY;
        if (!apiKey) throw new Error("ANTHROPIC_API_KEY is not set.");

        session = await connect();
        send({ type: "ready", tools: session.tools.map((tool) => tool.name) });

        const anthropic = new Anthropic({ apiKey });
        const system = withServerInstructions(systemPrompt(), session.instructions);
        const tools = session.tools.map((tool) => ({
          name: tool.name,
          description: tool.description,
          input_schema: tool.inputSchema as Anthropic.Tool.InputSchema,
        }));

        await runTurn([...history, { role: "user", content: question }], {
          async createMessage({ messages, toolChoice }) {
            const turn = anthropic.messages.stream(
              {
                model: process.env.ANTHROPIC_MODEL || DEFAULT_MODEL,
                max_tokens: MAX_TOKENS,
                system,
                tools,
                tool_choice: toolChoice === "none" ? { type: "none" } : { type: "auto" },
                messages: messages as Anthropic.MessageParam[],
              },
              { signal: request.signal },
            );
            // Deltas go out as they arrive; the loop's own `text` event is
            // dropped below so the same words do not arrive twice.
            turn.on("text", (delta) => send({ type: "delta", text: delta }));
            const final = await turn.finalMessage();
            return {
              content: final.content as ContentBlock[],
              stopReason: final.stop_reason,
            };
          },
          callTool: (name, input) => session!.call(name, input),
          emit(event: ChatEvent) {
            if (event.type === "text") return;
            send(event as unknown as Record<string, unknown>);
          },
        });
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        send({ type: "error", message: redact(detail), hint: hintFor(detail) });
      } finally {
        await session?.close().catch(() => undefined);
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}

/**
 * The one thing to do about a failure, when there is one.
 *
 * Every error here is operational rather than a bad question, and each has
 * a single fix. Saying it beside the message is the difference between a
 * page that looks broken and one that says which process is not running.
 */
function hintFor(message: string): string | undefined {
  if (/ECONNREFUSED|fetch failed/i.test(message)) {
    return "The MCP server is not answering. Start it with `cscan mcp serve`.";
  }
  if (/MCP_BEARER_TOKEN/.test(message)) return "See docs/MCP_SETUP.md.";
  if (/ANTHROPIC_API_KEY/.test(message)) return "Set it in .env.local at the repository root.";
  if (/401|unauthor/i.test(message)) {
    return "The token in MCP_BEARER_TOKEN is not the one the server started with.";
  }
  return undefined;
}
