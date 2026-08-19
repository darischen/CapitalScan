import Link from "next/link";

import { ErrorState } from "@/components/Screener";
import {
  ArmsTable,
  CellGrid,
  DrawdownSlice,
  EraGrid,
  KillBanner,
  NullDistribution,
} from "@/components/Research";
import { arms, cells, eraCells, nullDistribution } from "@/lib/research";

/**
 * `/research` — where Phase 4's negative result becomes legible.
 *
 * Four sections, each answering one ADR question: the cell grid (ADR 102),
 * the era breakdown (ADR 103), the three arms against the random-entry null
 * (ADR 013), and the drawdown slice (ADR 015).
 *
 * **No `split` parameter and no query parameters at all.** Gate item 9:
 * holdout is unreachable because nothing on this route can express it. The
 * splits are constants in `lib/research.ts`.
 *
 * Desktop-only per DESIGN §11.9, and it says so rather than reflowing into
 * something unreadable.
 */

export const dynamic = "force-dynamic";

export default async function ResearchPage() {
  try {
    const [grid, eras, armRows, nullPooled, nullBreadth] = await Promise.all([
      cells(),
      eraCells(),
      arms(),
      nullDistribution("pooled"),
      nullDistribution("breadth_high"),
    ]);

    const signalOf = (era: string) =>
      armRows.find((a) => a.arm === "signal" && a.era === era)?.totalRet ?? null;

    return (
      <main className="wrap research-page">
        <header className="rs-head">
          <Link href="/" className="back">
            ‹ screener
          </Link>
          <h1>Research</h1>
          <span className="dim">
            validate split · {5}-session horizon · 3% target · next_open entry
          </span>
        </header>

        <KillBanner cells={grid.filter((c) => c.splitKey === "validate")} />

        <section>
          <h2>Cell grid</h2>
          <p className="dim">
            Every cell, suppressed included. A suppressed cell shows the reason it
            was suppressed and never a rate — <code>v_stats</code> nulls the number
            in SQL, so no code path here can render one.
          </p>
          <h3>Validate</h3>
          <CellGrid cells={grid} split="validate" />
          <h3>Train</h3>
          <CellGrid cells={grid} split="train" />
        </section>

        <section>
          <h2>Era breakdown</h2>
          <p className="dim">
            Descriptive only — no q-values, because ADR 103 makes era a reporting
            split rather than a tested family. Era 2024+ is absent: it is the
            holdout window.
          </p>
          <EraGrid cells={eras} />
        </section>

        <section>
          <h2>Arms</h2>
          <p className="dim">
            The signal arm carries two rows, and the difference is the finding:
            ADR 099&rsquo;s breadth split disagrees in sign on validate.
          </p>
          <ArmsTable arms={armRows} />

          <h3>Random-entry null</h3>
          <NullDistribution values={nullPooled} signal={signalOf("pooled")} era="pooled" />
          <NullDistribution
            values={nullBreadth}
            signal={signalOf("breadth_high")}
            era="breadth_high"
          />
        </section>

        <section>
          <h2>Drawdown slice</h2>
          <p className="dim">
            ADR 015 calls this the project&rsquo;s central claim: signals in deeper
            drawdowns resolve up more often. Session 14 measured every interval
            crossing zero.
          </p>
          <DrawdownSlice cells={grid.filter((c) => c.splitKey === "validate")} />
        </section>

        <p className="rs-foot dim">
          Holdout is not on this page and cannot be requested from it. It is
          evaluated once, at the end, and published whatever it says.
        </p>
      </main>
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const safe = message.replace(/postgres(ql)?:\/\/[^\s]+/gi, "postgresql://[redacted]");
    return (
      <main className="wrap">
        <ErrorState code="RESEARCH_QUERY_FAILED" message={safe} what="the research page" />
      </main>
    );
  }
}
