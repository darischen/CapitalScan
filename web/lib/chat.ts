/**
 * The tool loop. No SDK imports, no database, no arithmetic.
 *
 * ADR 118 routes every model-facing read through the MCP server, so the
 * shape of this module is fixed by what it is *not* allowed to do:
 *
 * - **It performs no arithmetic.** DESIGN §10.2 forbids it, and Session
 *   18.5 says why: a number the model computed is a number no test covers.
 *   A number this module computed would be worse, because it would look
 *   like a tool result.
 * - **It does not parse tool output.** A result arrives from `lib/mcp.ts`
 *   as the text the server sent and reaches the model as that same text.
 *   Nothing here rounds, reshapes, or summarises it, which is how `n_eff`,
 *   the interval, and the q-value survive the trip.
 * - **It holds no tool schemas.** They are read from the server at
 *   connection time. `split` has two values there and holdout is not one of
 *   them (`mcp/tools.py::SplitArg`), so a schema written down here would be
 *   a second contract to keep in agreement with the first.
 *
 * The Anthropic block shapes below are typed locally rather than imported.
 * They are a documented wire format, and keeping the SDK out means the
 * whole loop is testable against fakes — which is what Session 18.2's
 * acceptance criteria actually ask for.
 */

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export interface WireMessage {
  role: "user" | "assistant";
  content: string | ContentBlock[];
}

export interface ModelReply {
  content: ContentBlock[];
  stopReason: string | null;
}

/** What a tool returned, as text. `isError` is the server's own flag. */
export interface ToolOutcome {
  text: string;
  isError: boolean;
}

export type ChatEvent =
  | { type: "text"; text: string }
  | { type: "tool_call"; id: string; name: string; input: unknown }
  | { type: "tool_result"; id: string; name: string; text: string; isError: boolean }
  | { type: "budget"; used: number; limit: number }
  | { type: "done"; steps: number; toolCalls: number };

export interface TurnDeps {
  createMessage(args: { messages: WireMessage[]; toolChoice: "auto" | "none" }): Promise<ModelReply>;
  callTool(name: string, input: unknown): Promise<ToolOutcome>;
  emit(event: ChatEvent): void;
  maxToolCalls?: number;
  maxSteps?: number;
}

/**
 * Tool calls allowed in one turn.
 *
 * Session 18.2 asks for a limit and "a documented behaviour when hit".
 * Eight covers the widest legitimate question the seven tools support —
 * screen a date, then a cell for each of a handful of rows — and stops a
 * loop that has started fetching cells at random.
 */
export const MAX_TOOL_CALLS = 8;

/**
 * Model round trips allowed in one turn, tool-using or not.
 *
 * A separate ceiling because a model can burn steps without spending
 * budget: a tool-use stop with an empty block list, or a refusal followed
 * by another attempt. The budget bounds database work; this bounds the
 * loop.
 */
export const MAX_STEPS = 12;

/**
 * What the model is told when the budget runs out.
 *
 * It goes in the `tool_result` slot of the call that was refused, because
 * the API requires every `tool_use` block to be answered — and because a
 * refusal in that position is legible to the model as "this specific fetch
 * did not happen" rather than as a system failure. It names the limit so
 * the model can say so, and it says what to do instead, which is the one
 * thing a model must not guess at here.
 */
export const BUDGET_MESSAGE =
  "Tool budget for this turn is exhausted. This call was not made and no data was " +
  "returned for it. Answer from the results you already have, and say plainly that " +
  "you stopped short of the full question. Do not estimate the missing number.";

/**
 * Runs one user turn to completion.
 *
 * Returns the final message list, which the caller may discard — the route
 * does. Conversation state that crosses a request boundary is user and
 * assistant *text* only, so a client cannot post a fabricated tool result
 * and have it read as one. Tool results exist inside a single call to this
 * function and nowhere else.
 */
export async function runTurn(messages: WireMessage[], deps: TurnDeps): Promise<WireMessage[]> {
  const limit = deps.maxToolCalls ?? MAX_TOOL_CALLS;
  const maxSteps = deps.maxSteps ?? MAX_STEPS;
  const conversation = [...messages];
  let used = 0;
  let steps = 0;
  /** Whether the reader has already been told the budget ran out. */
  let announced = false;

  while (steps < maxSteps) {
    steps += 1;
    const exhausted = used >= limit;
    const reply = await deps.createMessage({
      messages: conversation,
      // Once the budget is gone the model gets one turn with the tools
      // withdrawn rather than being cut off mid-sentence. It can answer
      // from what it has or say it cannot; both are honest, and neither
      // requires it to invent the fetch it did not get.
      toolChoice: exhausted ? "none" : "auto",
    });

    for (const block of reply.content) {
      if (block.type === "text" && block.text) deps.emit({ type: "text", text: block.text });
    }

    const calls = reply.content.filter(
      (block): block is Extract<ContentBlock, { type: "tool_use" }> => block.type === "tool_use",
    );
    if (calls.length === 0 || exhausted) break;

    conversation.push({ role: "assistant", content: reply.content });

    const results: ContentBlock[] = [];
    for (const call of calls) {
      if (used >= limit) {
        // Once per turn, not once per refused call. The model emits its
        // remaining calls in one block, so a naive emit here printed the
        // banner six times in a row under the first real question that hit
        // the limit. The model still gets a `tool_result` for every one of
        // them — that is the API's requirement — but the reader is being
        // told a single fact.
        if (!announced) {
          announced = true;
          deps.emit({ type: "budget", used, limit });
        }
        results.push({
          type: "tool_result",
          tool_use_id: call.id,
          content: BUDGET_MESSAGE,
          is_error: true,
        });
        continue;
      }
      used += 1;
      deps.emit({ type: "tool_call", id: call.id, name: call.name, input: call.input });

      // A tool that throws is reported to the model as a failed tool rather
      // than ending the turn. The handlers raise for real reasons — a
      // holdout request, an unknown ticker — and the model is told to
      // explain those, which it cannot do if the connection dies first.
      let outcome: ToolOutcome;
      try {
        outcome = await deps.callTool(call.name, call.input);
      } catch (error) {
        outcome = { text: error instanceof Error ? error.message : String(error), isError: true };
      }

      deps.emit({
        type: "tool_result",
        id: call.id,
        name: call.name,
        text: outcome.text,
        isError: outcome.isError,
      });
      // `outcome.text` verbatim. This is the line Session 18.2's acceptance
      // criterion is about: `n_eff`, the interval, and the q-value reach
      // the model because nothing between the handler and here reads them.
      results.push({
        type: "tool_result",
        tool_use_id: call.id,
        content: outcome.text,
        is_error: outcome.isError,
      });
    }
    conversation.push({ role: "user", content: results });
  }

  deps.emit({ type: "done", steps, toolCalls: used });
  return conversation;
}

/**
 * Trims the client's history to the text turns it is allowed to set.
 *
 * The request body is user input. Anything shaped like a tool result
 * arriving from a browser is either a bug or an attempt to put a number in
 * front of the model with a tool's authority behind it, so this drops every
 * block that is not plain text and every role that is not user or
 * assistant.
 */
export function sanitizeHistory(input: unknown, maxTurns = 20): WireMessage[] {
  if (!Array.isArray(input)) return [];
  const out: WireMessage[] = [];
  for (const item of input.slice(-maxTurns)) {
    if (typeof item !== "object" || item === null) continue;
    const { role, content } = item as { role?: unknown; content?: unknown };
    if (role !== "user" && role !== "assistant") continue;
    if (typeof content !== "string" || content.trim() === "") continue;
    out.push({ role, content });
  }
  return out;
}
