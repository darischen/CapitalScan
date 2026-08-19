import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CapitalScan",
  description: "Bollinger Band and Stochastic Oscillator event study. Advisory only.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* The three faces DESIGN §11.7's direction needs: one superfamily
          across mono and display so data and headers look related, and a
          narrower body face so dense rows fit without dropping below 13px.
          Self-hosting them is a later change; the preconnect keeps the
          first paint from waiting on a DNS round trip. */}
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@600&family=Inter+Tight:wght@400;500&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
