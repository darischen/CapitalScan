import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The pool's `error` listener (2026-11-02).
 *
 * node-postgres emits `error` on the pool when an idle connection's server
 * goes away, and an `error` event with no listener is thrown. Against a
 * container restart, `next start` caught it and logged `⨯ uncaughtException`
 * while the site stayed up; anything else running `lib/db.ts` would have
 * exited. `pg` is replaced here because the question is only whether the
 * listener exists and what it does, not whether a server can be reached.
 */

const { FakePool } = vi.hoisted(() => {
  class FakePool {
    static last: FakePool | undefined;
    handlers: Record<string, ((err: Error) => void)[]> = {};
    constructor(public options: unknown) {
      FakePool.last = this;
    }
    on(event: string, fn: (err: Error) => void) {
      (this.handlers[event] ??= []).push(fn);
      return this;
    }
  }
  return { FakePool };
});
vi.mock("pg", () => ({ Pool: FakePool }));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("the connection pool", () => {
  it("listens for errors from idle connections and logs them", async () => {
    vi.stubEnv("DATABASE_URL_RESEARCH", "postgresql://rw@h/db");
    const { getPool } = await import("@/lib/db");
    getPool();

    const handlers = FakePool.last?.handlers.error ?? [];
    expect(handlers).toHaveLength(1);

    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const restart = new Error("terminating connection due to administrator command");
    expect(() => handlers[0](restart)).not.toThrow();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("terminating connection"));
  });
});
