import { describe, expect, it } from "vitest";

import { applyEnv, duplicateKeys } from "@/lib/env";

/**
 * One credentials file, two runtimes, one value.
 *
 * `.env.local` is read by `jobs/db.py::_load_env` through `python-dotenv`
 * and by `next.config.ts` through `lib/env.ts`. These assert the two agree,
 * including on the case that made it matter: the file assigns
 * `MCP_BEARER_TOKEN` twice, `python-dotenv` takes the last, and this loader
 * used to take the first — so `cscan mcp serve` and `/chat` held different
 * secrets and every chat request 401'd.
 */

describe("applyEnv", () => {
  it("reads plain assignments", () => {
    const env: Record<string, string | undefined> = {};
    applyEnv("A=1\nB=two\n", env);
    expect(env).toEqual({ A: "1", B: "two" });
  });

  it("takes the last assignment when a key repeats, as python-dotenv does", () => {
    const env: Record<string, string | undefined> = {};
    applyEnv("TOKEN=stale\nOTHER=x\nTOKEN=current\n", env);
    expect(env.TOKEN).toBe("current");
  });

  it("still lets the process environment win over the whole file", () => {
    const env: Record<string, string | undefined> = { TOKEN: "injected" };
    applyEnv("TOKEN=stale\nTOKEN=current\n", env);
    // Both file lines are skipped. A deployment injecting its own secret
    // must not be overwritten by a checked-in default, and a duplicate in
    // the file does not change that.
    expect(env.TOKEN).toBe("injected");
  });

  it("ignores comments, blanks, and lines with no key", () => {
    const env: Record<string, string | undefined> = {};
    applyEnv("# comment\n\n=novalue\nA=1\n", env);
    expect(env).toEqual({ A: "1" });
  });

  it("strips one layer of matching quotes and nothing else", () => {
    const env: Record<string, string | undefined> = {};
    applyEnv(`A="quoted"\nB='single'\nC=un"even\nD="a=b"\n`, env);
    expect(env.A).toBe("quoted");
    expect(env.B).toBe("single");
    expect(env.C).toBe('un"even');
    // The value may contain `=`; only the first one separates.
    expect(env.D).toBe("a=b");
  });

  it("handles CRLF, which is what this file has on Windows", () => {
    const env: Record<string, string | undefined> = {};
    applyEnv("A=1\r\nB=2\r\n", env);
    expect(env).toEqual({ A: "1", B: "2" });
  });
});

describe("duplicateKeys", () => {
  it("names every key assigned more than once", () => {
    expect(duplicateKeys("A=1\nB=2\nA=3\nC=4\nC=5\n").sort()).toEqual(["A", "C"]);
  });

  it("is empty for a clean file", () => {
    expect(duplicateKeys("A=1\n# A=2\nB=3\n")).toEqual([]);
  });
});
