import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans_Condensed, Inter_Tight } from "next/font/google";

import "./globals.css";

/**
 * The three faces DESIGN §11.7's direction needs: one superfamily across
 * mono and display so data and headers look related, and a narrower body
 * face so dense rows fit without dropping below 13px.
 *
 * **Self-hosted via `next/font/google`, which is not the same as loading
 * from Google.** The fonts are fetched once at build time, emitted into the
 * app's own static output, and served from this origin. The `<link>` tags
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

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

const sans = Inter_Tight({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-sans",
  display: "swap",
});

const display = IBM_Plex_Sans_Condensed({
  subsets: ["latin"],
  weight: ["600"],
  variable: "--font-display",
  display: "swap",
});

export const metadata: Metadata = {
  title: "CapitalScan",
  description: "Bollinger Band and Stochastic Oscillator event study. Advisory only.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${mono.variable} ${sans.variable} ${display.variable}`}>
      <body>{children}</body>
    </html>
  );
}
