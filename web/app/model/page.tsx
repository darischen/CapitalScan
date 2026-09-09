import type { Metadata } from "next";

import Reliability from "@/components/Reliability";
import { ErrorState } from "@/components/Screener";
import { reliability } from "@/lib/reliability";

export const metadata: Metadata = {
  title: "Calibration — CapitalScan",
  description: "How the model's stated chances compare to what happened.",
};

export const dynamic = "force-dynamic";

/**
 * Phase 6's third gate (ADR 182), as a page.
 *
 * Separate from the screener rather than a panel on it: the screener
 * answers "what fired today" and this answers "should you believe the
 * numbers beside it". Mixing them would put a standing caveat in a place a
 * reader scans for today's signals.
 */
export default async function ModelPage() {
  try {
    const data = await reliability();
    return (
      <main className="wrap">
        <Reliability data={data} />
      </main>
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return (
      <main className="wrap">
        <ErrorState code="reliability" message={message} />
      </main>
    );
  }
}
