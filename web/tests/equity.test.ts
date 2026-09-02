import { describe, expect, it } from "vitest";

import { EQUITY_START_CAPITAL, compoundEquity, type EquityTrade } from "@/lib/equity";

function trade(id: number, entryDate: string, exitDate: string, netRet: number): EquityTrade {
  return { id, entryDate, exitDate, netRet };
}

describe("compoundEquity", () => {
  it("compounds five +5% trades to the user's own worked example", () => {
    const trades = [
      trade(1, "2024-01-02", "2024-01-05", 0.05),
      trade(2, "2024-02-01", "2024-02-04", 0.05),
      trade(3, "2024-03-01", "2024-03-04", 0.05),
      trade(4, "2024-04-01", "2024-04-04", 0.05),
      trade(5, "2024-05-01", "2024-05-04", 0.05),
    ];
    const balances = compoundEquity(trades);
    expect(balances.get(1)).toBeCloseTo(10500, 6);
    expect(balances.get(2)).toBeCloseTo(11025, 6);
    expect(balances.get(3)).toBeCloseTo(11576.25, 6);
    expect(balances.get(4)).toBeCloseTo(12155.0625, 6);
    expect(balances.get(5)).toBeCloseTo(12762.815625, 6);
    expect(Math.round((balances.get(5) ?? 0) * 100) / 100).toBe(12762.82);
  });

  it("starts at $10,000 on the first trade", () => {
    const balances = compoundEquity([trade(1, "2024-01-02", "2024-01-05", 0.1)]);
    expect(balances.get(1)).toBeCloseTo(EQUITY_START_CAPITAL * 1.1, 6);
  });

  it("compounds a loss the same way as a gain", () => {
    const balances = compoundEquity([
      trade(1, "2024-01-02", "2024-01-05", 0.05),
      trade(2, "2024-01-08", "2024-01-10", -0.035),
    ]);
    expect(balances.get(1)).toBeCloseTo(10500, 6);
    expect(balances.get(2)).toBeCloseTo(10132.5, 6);
  });

  it("skips a trade that fires while the prior one is still held", () => {
    // Trade 2 enters before trade 1 exits -- all-in, so it cannot be taken.
    const balances = compoundEquity([
      trade(1, "2024-01-02", "2024-01-10", 0.05),
      trade(2, "2024-01-05", "2024-01-12", 0.2),
      trade(3, "2024-01-15", "2024-01-20", 0.02),
    ]);
    expect(balances.has(2)).toBe(false);
    expect(balances.get(1)).toBeCloseTo(10500, 6);
    // Trade 3 compounds on trade 1's balance, not trade 2's -- trade 2 was
    // never held.
    expect(balances.get(3)).toBeCloseTo(10710, 6);
  });

  it("takes a trade that enters exactly on the prior trade's exit date", () => {
    // Same-day exit and re-entry is not an overlap.
    const balances = compoundEquity([
      trade(1, "2024-01-02", "2024-01-10", 0.05),
      trade(2, "2024-01-10", "2024-01-12", 0.02),
    ]);
    expect(balances.get(2)).toBeCloseTo(10710, 6);
  });

  it("is order-independent on input, sorting by entry date itself", () => {
    const reversed = compoundEquity([
      trade(2, "2024-02-01", "2024-02-04", 0.05),
      trade(1, "2024-01-02", "2024-01-05", 0.05),
    ]);
    expect(reversed.get(1)).toBeCloseTo(10500, 6);
    expect(reversed.get(2)).toBeCloseTo(11025, 6);
  });

  it("returns an empty map for no trades", () => {
    expect(compoundEquity([]).size).toBe(0);
  });
});
