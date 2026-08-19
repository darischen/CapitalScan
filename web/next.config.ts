import type { NextConfig } from "next";

const config: NextConfig = {
  // `pg` is a Node driver and must not be bundled into a client chunk. Every
  // query in this app runs in a server component or a route handler; this
  // makes a stray `'use client'` import a build error rather than a runtime
  // one in a browser.
  serverExternalPackages: ["pg"],
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: true },
};

export default config;
