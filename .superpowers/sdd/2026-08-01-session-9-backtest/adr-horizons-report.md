# ADR 093 report

## Number and status chosen

ADR 093, "Terminal quantiles expand to five horizons."

The file's index runs to 092 ("Exit thresholds are ExitParams fields, never literals") with no gaps, so 093 was the next available number. Note the file body is not in strict numeric order (092's full entry is followed by 035-040 before the file ends), so I confirmed the true last entry by grepping all `## <number>` headers rather than trusting position in the file, then appended after the literal end of file (end of ADR 040's body).

Status: **Provisional. Authorizes a Phase 6 rewrite of DESIGN §7.4** (not "Pinned"). Two reasons:

1. This ADR constrains a schema decision (Session 10's path-table window) that is being locked in now, but the model surface itself is not built until Phase 6, several sessions away.
2. ADR 033's kill criteria sit between here and Phase 6. If no cell in Phase 4 survives FDR correction, the two-indicator hypothesis is dead and there is no model to expand at all. Pinning a five-horizon model spec now would state something as settled that a still-open statistical gate could invalidate. "Pinned" is used elsewhere in the file for decisions the team commits to now regardless of downstream results (e.g. ADR 064, ADR 092); this one is explicitly conditional on Phase 4 and is not that.

## What I verified against source docs, not the brief

- **DESIGN §7.4's existing philosophy quote** ("Timeout return, not exit-rule return...") is real, confirmed at DESIGN.md lines 1494 ("Timeout return, not exit-rule return" is the header text is actually "Timeout return, not exit-rule return." appears as bolded lead-in at line 1494, immediately followed by the "model describes the underlying path" sentence). Quoted correctly in the brief.
- **`events.fwd_ret_1d/2d/3d/5d/10d` already exist**, confirmed both in `DESIGN.md` (line 1151-1153, `research schema` DDL) and in code (`capitalscan/research/backtest.py`, `capitalscan/research/enrich.py`, `capitalscan/tests/unit/test_returns.py`, `capitalscan/tests/unit/test_backtest_path.py` all reference these columns). Session 9 attribution matches `docs/session10.md` line 195-202, which states these are "populated by Session 9."
- **ADR 064** ("Eleven independent heads, timeout-return targets") is the correct citation for the current head count and the `Q̂_τ(R_5)` formula, and DESIGN §7.4/§7.1 mirror it exactly.
- **ADR 067** ("Four-check promotion gate") is the correct citation for calibration/coverage checks, not ADR 072. **The brief was wrong here**: ADR 072 is "Shared handler layer, two transports," unrelated to calibration. ADR 030 ("Model promotion gated on calibration") is Superseded by 067, and 067's check 3 ("Quantile coverage within 5 points of nominal for every tau") is the mechanism that scales from 5 to 25 checks. I cited 067 in the ADR text, not 072.
- **ADR 033**'s kill criteria (no cell beats baseline after FDR correction -> two-indicator hypothesis dead) verified at DECISIONS.md line 586-598, used correctly in the brief and in my ADR to justify Provisional status and the "does not rewrite §7.4" boundary.
- **Session 10 window rationale** (`docs/session10.md` §0 and task 10.5) confirms the ten-trading-day path table window is driven by `max(StatsParams.fwd_ret_horizons)`, independent of `ExitParams.max_hold_days` (5 days), and that task 10.5 explicitly flags the head-count expansion as something needing "its own ADR before Phase 6 touches it" and states "This document does not write that ADR" (session10.md line 245-249). This ADR is that ADR.
- Quantile crossing fixed by post-fit sorting: confirmed at DESIGN §7.4 line 1496 and ADR 064.

## Corrections to the brief

- **ADR 072 does not govern calibration.** The correct citation is ADR 067 (calibration/promotion gate) and DESIGN §7.6 (isotonic regression detail, no dedicated ADR number of its own — ADR 030 held that role and was superseded by 067). Fixed in the ADR text.
- The brief's suggested head-count math ("roughly triple") holds for the terminal-quantile family alone (5 -> 25) but only holds "roughly triple" for the *total* head count if you're loose with rounding: 11 heads -> 6 (reachability+adverse, unchanged) + 25 (quantiles) = 31, which is closer to 2.8x than a clean 3x. I used "roughly thirty-one" and "roughly triple the terminal-quantile heads" rather than repeating an imprecise "roughly triple" for the whole model, to keep the number defensible.
- Everything else in the brief (rationale, what's in/out of scope, "this document does not rewrite §7.4") checked out against the source docs without correction needed.

## Files touched

- `docs/DECISIONS.md`: added index row for ADR 093, appended the full ADR 093 entry after ADR 040 (the file's actual last entry), updated "Last updated" date to 2026-08-02. No existing ADR text was modified.
