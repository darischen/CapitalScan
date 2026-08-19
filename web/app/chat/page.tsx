import Link from "next/link";

import Chat from "@/components/Chat";

/**
 * `/chat` — the natural language interface ADR 033 calls the strongest
 * version of this project.
 *
 * > The strongest version of this project is a rigorous event-study engine
 * > with a natural language interface and honest confidence intervals.
 * > Built for that outcome, a null result stops being a failure.
 *
 * The honest-intervals half is what makes the first half worth having, so
 * the page states the finding before it takes a question. Phase 5 gate item
 * 8 asks that ADR 112's result appear on every surface reporting a
 * statistic; here it appears *above* the first statistic, because a model
 * answering questions about a null result is the surface where a reader is
 * most likely to hear an edge that was never measured.
 *
 * No `split` parameter and no query parameters, as on `/research`. Holdout
 * is unreachable from this route in three separate ways: nothing here can
 * express it, `mcp/tools.py::SplitArg` has two members, and
 * `handlers/enums.py` raises.
 */

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Chat · CapitalScan",
};

export default function ChatPage() {
  return (
    <main className="wrap chat-page">
      <header className="rs-head">
        <Link href="/" className="back">
          ‹ screener
        </Link>
        <h1>Chat</h1>
        <span className="dim">
          seven tools · every number from a tool result · no arithmetic
        </span>
      </header>

      <p className="kill">
        No cell survived Benjamini-Hochberg correction on either split. The minimum
        q-value was <span className="num">0.849</span> on train and{" "}
        <span className="num">0.706</span> on validate, and every edge interval spans
        zero. A hit rate from these tools describes a past sample. It is not an edge,
        and this page will not present it as one. <Link href="/research">See the
        measurement</Link>.
      </p>

      <Chat />

      <p className="rs-foot dim">
        Advisory only. Answers state what fired and what historically followed, with
        the sourcing attached. Nothing here sources a claim about your financial
        situation, tax position, or suitability, so none is made.
      </p>
    </main>
  );
}
