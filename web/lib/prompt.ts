import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Loads the chat system prompt from `docs/SYSTEM_PROMPT.md`.
 *
 * Session 18.3 requires the prompt to be version-controlled and its changes
 * reviewable. That only holds if there is **one** text: a copy embedded in
 * a module would drift from the document silently, and the document is
 * where the reasoning behind each rule lives.
 *
 * So this reads the file and extracts the fenced block under `## The
 * prompt`. Everything outside that block is commentary for humans — the
 * table of which rules are enforced in code, and why ADR 112's amendments
 * sit in the body rather than appended — and none of it reaches the model.
 */

/** The heading the prompt block follows. Changing it here changes nothing;
 * the document is the source, and this is how the block is found in it. */
const HEADING = "## The prompt";

/**
 * Substrings the loaded prompt must contain.
 *
 * Session 18's gate item 6 is that the prompt names ADR 112's result, and
 * the document itself says the check is "a substring test on the numbers,
 * so paraphrasing them away fails."
 *
 * **This throws rather than warning.** The prompt's whole job after ADR 112
 * is to stop the model presenting a hit rate as an edge; a chat route that
 * came up with that section quietly deleted would be worse than one that
 * did not come up at all.
 */
export const REQUIRED_PHRASES = ["0.849", "0.706", "no cell survived", "not forecasts"];

function promptPath(cwd: string = process.cwd()): string {
  // `next dev` and `vitest` both run with the working directory at `web/`.
  // The second candidate covers being invoked from the repository root.
  const candidates = [
    resolve(cwd, "..", "docs", "SYSTEM_PROMPT.md"),
    resolve(cwd, "docs", "SYSTEM_PROMPT.md"),
  ];
  const found = candidates.find((path) => existsSync(path));
  if (!found) {
    throw new Error(`docs/SYSTEM_PROMPT.md not found. Looked in: ${candidates.join(", ")}`);
  }
  return found;
}

/**
 * Pulls the prompt out of the document's text.
 *
 * Separate from the file read so a test can feed it a document with the
 * numbers removed and watch it refuse, which is the behaviour that matters
 * and the one an on-disk fixture cannot exercise without editing the real
 * prompt.
 */
export function extractPrompt(document: string): string {
  const heading = document.indexOf(HEADING);
  if (heading === -1) {
    throw new Error(`SYSTEM_PROMPT.md has no "${HEADING}" section`);
  }
  const fence = /```[^\n]*\n([\s\S]*?)```/.exec(document.slice(heading));
  if (!fence) {
    throw new Error(`SYSTEM_PROMPT.md has no fenced block under "${HEADING}"`);
  }

  const prompt = fence[1].trim();
  // Whitespace-normalised before the check. The prompt is hard-wrapped
  // prose, so "no cell survived" is split across two lines in the file
  // and a naive substring test reports it missing — which it did, on the
  // first run, against a document that says it right there. A check that
  // fires on a line break is a check that will be relaxed rather than
  // fixed the next time someone reflows a paragraph.
  const flat = prompt.replace(/\s+/g, " ").toLowerCase();
  const missing = REQUIRED_PHRASES.filter((phrase) => !flat.includes(phrase.toLowerCase()));
  if (missing.length > 0) {
    throw new Error(
      `SYSTEM_PROMPT.md no longer names ADR 112's result — missing ${missing.join(", ")}. ` +
        "Session 18 gate item 6. Restore it rather than relaxing this check.",
    );
  }
  return prompt;
}

let cached: string | undefined;

/** The prompt text, read once per process. */
export function systemPrompt(): string {
  if (cached === undefined) cached = extractPrompt(readFileSync(promptPath(), "utf8"));
  return cached;
}

/**
 * The prompt plus whatever the MCP server said about itself.
 *
 * The server's `instructions` field carries ADR 112's result too, in its
 * own words (`mcp/server.py::INSTRUCTIONS`). Passing it through rather than
 * dropping it means the chat route inherits any future amendment to the
 * server's framing without an edit here — the same reason the tool schemas
 * are read from the server rather than written down.
 */
export function withServerInstructions(prompt: string, instructions?: string): string {
  if (!instructions?.trim()) return prompt;
  return `${prompt}\n\nFROM THE SERVER YOU ARE QUERYING\n\n${instructions.trim()}`;
}
