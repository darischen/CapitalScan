import TickerSearch from "./TickerSearch";

import { fmt, signedPct } from "@/lib/format";
import type { Listing } from "@/lib/listing";
import type { Meta } from "@/lib/screen";
import { INDICATOR_WARMUP_SESSIONS, warmup } from "@/lib/warmup";

/**
 * The ticker page for a name with bars and no indicators yet: a new
 * listing (ANT, 2026-11-19), or any ticker short of
 * `INDICATOR_WARMUP_SESSIONS` daily bars.
 *
 * **Generic by construction.** Nothing here names a ticker. The page takes
 * this path whenever `v_ticker_state` is empty and `v_listing` has bars, so
 * every such name renders the same way and leaves it on its own once its
 * first indicator row lands.
 *
 * DESIGN §11.6's five states: loading is the route's `loading.tsx`; empty
 * (no bars) stays `NoTicker`; stale is the `meta.stale` tag; error is the
 * page's `ErrorState`. Suppressed does not apply: there is no probability
 * on this page to suppress.
 */

export function ListingHeader({
  listing,
  meta,
}: {
  listing: Listing;
  meta: Meta;
}) {
  const sinceListing =
    listing.lastClose !== null &&
    listing.firstClose !== null &&
    listing.firstClose > 0
      ? listing.lastClose / listing.firstClose - 1
      : null;

  return (
    <header className="tk-head">
      <div className="tk-id">
        <a href="/" className="back">
          ‹ screener
        </a>
        <h1 className="num">{listing.ticker}</h1>
        {listing.name && <span className="tk-name">{listing.name}</span>}
        {listing.sector && <span className="tk-sector">{listing.sector}</span>}
      </div>

      <TickerSearch />

      <div className="tk-price">
        <span className="k">close</span>
        <span className="num big">{fmt(listing.lastClose)}</span>
        <span
          className={`num ${sinceListing !== null && sinceListing >= 0 ? "up" : "down"}`}
        >
          {signedPct(sinceListing)} since first close
        </span>
      </div>

      <div className="tk-tags">
        {/* Not "outside both universes": that is a verdict, and no quarter
            has given one yet. */}
        <span className="tag off">not yet evaluated</span>
        {listing.exchange && <span className="tag">{listing.exchange}</span>}
        <span className="tag dim">as of {listing.lastBar}</span>
        {!listing.isActive && <span className="tag flagged">inactive</span>}
        {meta.stale && (
          <span className="tag flagged">
            {meta.stalenessDays} sessions behind {meta.asOf ?? "?"}
          </span>
        )}
      </div>
    </header>
  );
}

/**
 * The listing's facts and its distance from the first signal.
 *
 * **The meter is the page's one signature element.** It stands where the
 * band gauge stands on an established name, and in the same muted
 * treatment: progress toward a computation is not a direction, so it takes
 * no colour (DESIGN §11.7, colour carries meaning only).
 */
export function ListingRail({ listing }: { listing: Listing }) {
  const w = warmup(listing.sessions);
  return (
    <div className="tk-top">
      <div className="rail-strip">
        <div className="stat">
          <span className="k">first bar</span>
          <span className="v num">{listing.firstBar}</span>
        </div>
        <div className="stat">
          <span className="k">sessions</span>
          <span className="v num">{listing.sessions}</span>
        </div>
        <div className="stat">
          <span className="k">first close</span>
          <span className="v num">{fmt(listing.firstClose)}</span>
        </div>
        <div className="stat">
          <span className="k">last close</span>
          <span className="v num">{fmt(listing.lastClose)}</span>
        </div>
      </div>

      <div className="gauge warmup">
        <div className="gauge-head">
          <span className="k">indicator warm-up</span>
          <span className="v num">
            {w.held} / {INDICATOR_WARMUP_SESSIONS}
          </span>
        </div>
        <div
          className="gauge-track"
          role="meter"
          aria-label="Daily sessions held toward the first indicator row"
          aria-valuemin={0}
          aria-valuemax={INDICATOR_WARMUP_SESSIONS}
          aria-valuenow={w.held}
        >
          <span
            className="warmup-fill"
            style={{ width: `${(w.fraction * 100).toFixed(2)}%` }}
          />
        </div>
        <p className="warmup-note">
          {w.remaining > 0
            ? `${w.remaining} more sessions before bands, stochastics and signals are computed.`
            : "Enough history. Indicators arrive with the next nightly run."}
        </p>
      </div>
    </div>
  );
}

/** Where the event history goes on an established name. */
export function ListingNoEvents({ listing }: { listing: Listing }) {
  return (
    <div className="empty">
      <span className="rail" />
      <div className="body">
        <p>
          No signals for <strong className="num">{listing.ticker}</strong> yet.
        </p>
        <p className="last">
          Every signal reads Bollinger Bands and the stochastic, and those need{" "}
          {INDICATOR_WARMUP_SESSIONS} daily sessions. The chart above shows
          price and volume until then.
        </p>
      </div>
    </div>
  );
}
