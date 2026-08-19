import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

/**
 * Two things Next supplies at build time and Vitest does not.
 *
 * `jsx: "automatic"` because `tsconfig.json` says `"jsx": "preserve"` — the
 * right answer for Next, which does the transform itself, and an error
 * under esbuild, which would emit the JSX unchanged and then fail to parse
 * it. Setting it here rather than changing the tsconfig keeps the build
 * pipeline as Next expects it.
 *
 * The `@/` alias mirrors `tsconfig.json`'s `paths`.
 */
export default defineConfig({
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: { "@": resolve(__dirname, ".") },
  },
  test: {
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    environment: "node",
  },
});
