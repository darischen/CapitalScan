import type { Metadata } from "next";
import localFont from "next/font/local";

import "./globals.css";

/**
 * The three faces DESIGN §11.7's direction needs: one superfamily across
 * mono and display so data and headers look related, and a narrower body
 * face so dense rows fit without dropping below 13px.
 *
 * **Self-hosted from files in `app/fonts/` (2026-09-27).** This used
 * `next/font/google`, which fetched the fonts from Google at *build* time.
 * A bad fetch fails the build as `TypeError: Cannot read properties of
 * null` inside webpack: it failed CI's `web` job on 2026-09-23, 09-25 and
 * 09-27 on branches that touched no `web/` file, and the same fetch runs in
 * every Pi build. The woff2 files (latin subset, OFL, see
 * `app/fonts/README.md`) are committed, so no build reaches the network.
 * Served from this origin exactly as before. The `<link>` tags
 * this replaced made a request to `fonts.googleapis.com` on every first
 * paint — the only outbound request the app made, and one that told a third
 * party who was reading the page.
 *
 * It also removes the preconnect dance and the flash: `next/font` inlines
 * the `@font-face` rules and adds `font-display: swap` with a size-adjusted
 * fallback, so there is no round trip to wait on.
 *
 * Each face is bound to a CSS variable rather than used directly, because
 * `globals.css` owns the type tokens (`--mono`, `--sans`, `--display`) and
 * every component reads those. The variables are the seam.
 */

const mono = localFont({
  src: [
    { path: "./fonts/IBMPlexMono-400-latin.woff2", weight: "400", style: "normal" },
    { path: "./fonts/IBMPlexMono-500-latin.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-mono",
  display: "swap",
});

// One variable file covers both weights; Google serves Inter Tight that way.
const sans = localFont({
  src: [{ path: "./fonts/InterTight-var-latin.woff2", weight: "400 500", style: "normal" }],
  variable: "--font-sans",
  display: "swap",
});

const display = localFont({
  src: [{ path: "./fonts/IBMPlexSansCondensed-600-latin.woff2", weight: "600", style: "normal" }],
  variable: "--font-display",
  display: "swap",
});

export const metadata: Metadata = {
  // `default` for routes that set no title of their own — the home page
  // stays plain "CapitalScan". `template` is applied to any route that
  // exports one, so `/ticker/TSLA` renders "TSLA | CapitalScan" without
  // each page having to repeat the suffix.
  title: {
    default: "CapitalScan",
    template: "%s | CapitalScan",
  },
  description: "Bollinger Band and Stochastic Oscillator event study. Advisory only.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${mono.variable} ${sans.variable} ${display.variable}`}>
      <body>{children}</body>
    </html>
  );
}
