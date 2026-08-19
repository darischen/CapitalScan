import { readFileSync } from "node:fs";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import { runTurn, type ContentBlock, type ModelReply, type WireMessage } from "@/lib/chat";
import { applyEnv } from "@/lib/env";
import { connect, type McpSession } from "@/lib/mcp";

/**
 * The chat path against a real MCP server. **Opt-in.**
 *
 * Everything else in `chat.test.ts` runs against fakes, which is right: a
 * unit test that needs a server running is a unit test that gets deleted.
 * But fakes cannot show that the tool schemas the server generates are ones
 * the model can be given, or that a holdout request actually raises rather
 * than merely being absent from a `Literal`.
 *
 * ```
 * cscan mcp serve                 # 127.0.0.1:8787
 * MCP_LIVE=1 npx vitest run tests/mcp.live.test.ts
 * ```
 *
 * Skipped without `MCP_LIVE=1`, so CI stays hermetic.
 */

const LIVE = process.env.MCP_LIVE === "1";
const describeLive = LIVE ? describe : describe.skip;

describeLive("the chat path against a running MCP server", () => {
  let session: McpSession;

  beforeAll(async () => {
    // Vitest does not run `next.config.ts`, so the root credentials file is
    // loaded here the same way it is loaded there. Same parser, so a
    // duplicate key resolves the same way it does in a request.
    applyEnv(readFileSync(join(__dirname, "..", "..", ".env.local"), "utf8"), process.env);
    session = await connect();
  }, 30_000);

  it("advertises exactly the seven tools", () => {
    expect(session.tools.map((tool) => tool.name).sort()).toEqual([
      "explain_signal",
      "get_events",
      "get_indicators",
      "get_stats",
      "get_universe",
      "predict",
      "screen_signals",
    ]);
  });

  /**
   * The schema is generated from `core.types.SignalType` and
   * `core.config.StatsParams`, and `holdout` is not one of the two members
   * `SplitArg` carries. A model reading this schema cannot compose the
   * request, which is the layer *before* the handler's refusal.
   */
  it("offers two splits, and holdout is not one of them", () => {
    const stats = session.tools.find((tool) => tool.name === "get_stats")!;
    const properties = stats.inputSchema.properties as Record<string, { enum?: string[] }>;
    const splits = properties.split.enum ?? [];
    expect(splits).not.toContain("holdout");
    expect(splits.sort()).toEqual(["train", "validate"]);
  });

  /** Gate item 6, on the server's side of the boundary. */
  it("states ADR 112's result in its own instructions", () => {
    expect(session.instructions ?? "").toMatch(/no cell survived/i);
  });

  /**
   * The real thing the fakes stand in for: a payload from the handlers,
   * with everything Session 15's validator guarantees, reaching the model
   * position in the message list unchanged.
   */
  it("carries n_eff, an interval, and a q-value through to the model", async () => {
    const seen: WireMessage[][] = [];
    let step = 0;

    const createMessage = async ({ messages }: { messages: WireMessage[] }): Promise<ModelReply> => {
      seen.push(messages);
      step += 1;
      return step === 1
        ? {
            content: [
              {
                type: "tool_use",
                id: "t1",
                name: "get_stats",
                input: {
                  signal_type: "confluence_low",
                  target_pct: 0.03,
                  dd_bucket: "10-20",
                  split: "validate",
                },
              },
            ],
            stopReason: "tool_use",
          }
        : { content: [{ type: "text", text: "reported" }], stopReason: "end_turn" };
    };

    await runTurn([{ role: "user", content: "that cell" }], {
      createMessage,
      callTool: (name, input) => session.call(name, input),
      emit: () => {},
    });

    const blocks = seen[1].at(-1)!.content as ContentBlock[];
    const payload = (blocks[0] as { content: string }).content;
    // A cell or a suppression, and either is a valid answer — but never a
    // bare probability. `n_eff` and the interval travel with the rate or
    // the rate is not there at all.
    const parsed = JSON.parse(payload) as Record<string, unknown>;
    if (parsed.kind === "suppressed") {
      expect(parsed.reason).toBeTruthy();
      expect(parsed).not.toHaveProperty("p_hit");
    } else {
      expect(parsed).toHaveProperty("n_eff");
      expect(parsed).toHaveProperty("q_value");
      expect(String(payload)).toMatch(/ci_low|ci_high|interval/);
    }
  }, 30_000);

  /**
   * The refusal Session 18's gate item 5 asks for, taken at the handler
   * rather than at the prompt. A tool call that names holdout anyway — the
   * schema forbids it, a model can still emit it — comes back as an error
   * the model has to explain, not as data.
   */
  it("refuses a holdout request at the handler", async () => {
    const result = await session
      .call("get_stats", {
        signal_type: "confluence_low",
        target_pct: 0.03,
        dd_bucket: "10-20",
        split: "holdout",
      })
      .catch((error: Error) => ({ text: error.message, isError: true }));

    expect(result.isError).toBe(true);
    expect(result.text.toLowerCase()).toMatch(/holdout|split/);
  }, 30_000);

  /** ADR 033: no model exists, and the tool says so rather than guessing. */
  it("predict returns not_found for every input", async () => {
    const result = await session.call("predict", { ticker: "AMD" });
    expect(result.text).toContain("not_found");
  }, 30_000);
});
