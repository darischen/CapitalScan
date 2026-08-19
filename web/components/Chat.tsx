"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The chat surface.
 *
 * **Every tool call is shown, and every raw result is one click away.** That
 * is the design decision the rest follows from. A model summarising a
 * database is only as trustworthy as the reader's ability to check it, and
 * the thing to check against is the payload the handler returned — with
 * `n_eff`, the interval, and the q-value in it. Hiding that behind the
 * prose would make the page a claim rather than a record.
 *
 * So the transcript is a ledger: turns separated by the rail the screener
 * uses for side, tool calls inline as monospace rows in call order, results
 * expandable underneath. The model's prose sits between them as commentary
 * on visible evidence rather than as the evidence.
 */

interface ToolPart {
  kind: "tool";
  id: string;
  name: string;
  input: unknown;
  text?: string;
  isError?: boolean;
}

type Part = { kind: "text"; text: string } | ToolPart | { kind: "budget"; limit: number };

type Turn =
  | { role: "user"; text: string }
  | { role: "assistant"; parts: Part[]; error?: { message: string; hint?: string } };

/** Questions the tools can actually answer, phrased the way the data is shaped. */
const SUGGESTIONS = [
  "What fired today?",
  "What does the data say about confluence-low signals in the 10-20% drawdown bucket?",
  "Show me NVDA's indicator state and any recent events.",
  "Did any cell survive multiple-testing correction?",
];

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy]);

  /**
   * Appends to the assistant turn in progress.
   *
   * Written as a reducer over the last turn rather than as mutation: React
   * 19 batches these and a mutated array would render the previous state
   * for a frame, which on a stream arriving in 20ms deltas is visible as
   * text that flickers backwards.
   */
  function patchLast(update: (turn: Extract<Turn, { role: "assistant" }>) => void) {
    setTurns((current) => {
      const next = [...current];
      const last = next[next.length - 1];
      if (!last || last.role !== "assistant") return current;
      const copy = { ...last, parts: [...last.parts] };
      update(copy);
      next[next.length - 1] = copy;
      return next;
    });
  }

  async function send(question: string) {
    const text = question.trim();
    if (!text || busy) return;

    // Text only. The route re-derives everything else, and a tool result
    // posted from a browser is never read as one (`lib/chat.ts`).
    type HistoryTurn = { role: "user" | "assistant"; content: string };
    const history = turns.flatMap((turn): HistoryTurn[] =>
      turn.role === "user"
        ? [{ role: "user" as const, content: turn.text }]
        : [
            {
              role: "assistant" as const,
              content: turn.parts
                .filter((part): part is { kind: "text"; text: string } => part.kind === "text")
                .map((part) => part.text)
                .join(""),
            },
          ],
    );

    setDraft("");
    setBusy(true);
    setTurns((current) => [
      ...current,
      { role: "user", text },
      { role: "assistant", parts: [] },
    ]);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message: text, messages: history.filter((m) => m.content) }),
      });
      if (!response.body) throw new Error(`No response body (HTTP ${response.status}).`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line. A partial frame stays
        // in the buffer; splitting eagerly would parse half a JSON object
        // the moment a delta straddles a TCP segment.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          apply(JSON.parse(line.slice(6)));
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      patchLast((turn) => {
        turn.error = { message };
      });
    } finally {
      setBusy(false);
    }
  }

  function apply(event: Record<string, unknown>) {
    switch (event.type) {
      case "delta":
        patchLast((turn) => {
          const last = turn.parts[turn.parts.length - 1];
          if (last?.kind === "text") {
            turn.parts[turn.parts.length - 1] = { kind: "text", text: last.text + event.text };
          } else {
            turn.parts.push({ kind: "text", text: String(event.text) });
          }
        });
        break;
      case "tool_call":
        patchLast((turn) => {
          turn.parts.push({
            kind: "tool",
            id: String(event.id),
            name: String(event.name),
            input: event.input,
          });
        });
        break;
      case "tool_result":
        patchLast((turn) => {
          const index = turn.parts.findIndex(
            (part) => part.kind === "tool" && part.id === event.id,
          );
          if (index === -1) return;
          turn.parts[index] = {
            ...(turn.parts[index] as ToolPart),
            text: String(event.text),
            isError: Boolean(event.isError),
          };
        });
        break;
      case "budget":
        patchLast((turn) => {
          turn.parts.push({ kind: "budget", limit: Number(event.limit) });
        });
        break;
      case "error":
        patchLast((turn) => {
          turn.error = {
            message: String(event.message),
            hint: typeof event.hint === "string" ? event.hint : undefined,
          };
        });
        break;
      default:
        break;
    }
  }

  return (
    <div className="chat">
      <div className="chat-log">
        {turns.length === 0 && !busy && (
          <div className="chat-empty">
            <p>
              Ask about what fired, what an indicator read, or what a cell of the grid
              measured. Every number comes back from a tool, and every tool call is shown
              with the payload it returned.
            </p>
            <ul>
              {SUGGESTIONS.map((suggestion) => (
                <li key={suggestion}>
                  <button type="button" onClick={() => void send(suggestion)}>
                    {suggestion}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {turns.map((turn, index) =>
          turn.role === "user" ? (
            <div className="turn user" key={index}>
              <span className="rail" />
              <div className="body">{turn.text}</div>
            </div>
          ) : (
            <div className="turn assistant" key={index}>
              <span className="rail" />
              <div className="body">
                {turn.parts.map((part, partIndex) =>
                  part.kind === "text" ? (
                    <p key={partIndex}>{part.text}</p>
                  ) : part.kind === "budget" ? (
                    <p className="chat-budget" key={partIndex}>
                      Tool budget reached at <span className="num">{part.limit}</span> calls.
                      The answer below stops where the data stopped.
                    </p>
                  ) : (
                    <ToolRow key={part.id} part={part} />
                  ),
                )}
                {turn.error && (
                  <div className="chat-error">
                    <div className="code">CHAT_FAILED</div>
                    <div>{turn.error.message}</div>
                    {turn.error.hint && <div className="dim">{turn.error.hint}</div>}
                  </div>
                )}
                {busy && index === turns.length - 1 && turn.parts.length === 0 && !turn.error && (
                  <p className="dim chat-wait">working…</p>
                )}
              </div>
            </div>
          ),
        )}
        <div ref={endRef} />
      </div>

      <form
        className="chat-compose"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <textarea
          value={draft}
          rows={1}
          placeholder="Ask about a ticker, a date, or a cell"
          aria-label="Your question"
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send(draft);
            }
          }}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          {busy ? "…" : "ask"}
        </button>
      </form>
    </div>
  );
}

/**
 * One tool call, collapsed to its name and arguments.
 *
 * Collapsed by default because the payloads are long, and expandable rather
 * than linked because the check has to be possible without leaving the
 * answer. `<details>` does this natively, keeps the disclosure state on the
 * element, and is keyboard-reachable without any code here.
 */
function ToolRow({ part }: { part: ToolPart }) {
  const args = summarizeArgs(part.input);
  return (
    <details className={`tool${part.isError ? " err" : ""}`}>
      <summary>
        <span className="tool-name">{part.name}</span>
        {args && <span className="tool-args">{args}</span>}
        <span className="tool-state">
          {part.text === undefined ? "…" : part.isError ? "refused" : "ok"}
        </span>
      </summary>
      <pre>{part.text ?? "waiting"}</pre>
    </details>
  );
}

/**
 * The arguments on one line, in call order.
 *
 * Values only, no keys: the tool name already says what the positions mean,
 * and `confluence_low · 3 · 10-20 · validate` reads at a glance where the
 * same thing with keys wraps to two lines and stops being scannable.
 */
function summarizeArgs(input: unknown): string {
  if (typeof input !== "object" || input === null) return "";
  return Object.values(input as Record<string, unknown>)
    .filter((value) => value !== null && value !== undefined && value !== "")
    .map((value) => (Array.isArray(value) ? value.join("+") : String(value)))
    .join(" · ");
}
