# CapitalScan

Bollinger Band + Stochastic Oscillator event-study engine for US mega-cap equities.

**Advisory only. No execution path exists or may be added.**

---

## Before writing any code

Read `docs/DECISIONS.md`. It holds 87 ADRs. They are decisions, not suggestions.

If a task appears to require contradicting one, **stop and ask.** Do not work around it.

| Question | Document |
|---|---|
| Why is it this way? | `docs/DECISIONS.md` |
| What is it? | `docs/DESIGN.md` |
| What do I build next? | `docs/BUILD.md` |
| How do I know it works? | `docs/TESTS.md` |
| What happened when we ran it? | `docs/RESULTS.md` |

---

## Non-negotiable invariants

1. **`core/` performs no IO.** No database, no HTTP, no file reads, no clock access. `jobs/` and `research/` own all IO.
2. **One signal implementation.** `jobs/` and `research/` both import `core/signals.py`. Never write a second band comparison anywhere.
3. **Indicators are read at t−1, never t.** Enforced in `core/signals.py` and again in the `events` job. This is the highest-risk silent failure in the system.
4. **Never fill, forward-fill, or interpolate a null.** Drop the row and log it to `bar_rejects` with a reason.
5. **`split_key` is assigned at event creation, never at query time.**
5b. **No view or query may join statistics on an event's own `split_key`.** Live events carry `split_key = 'holdout'`; inheriting it would surface holdout numbers continuously. Serving views hardcode `split_key = 'validate'`. `cell_id` is derived from component columns, never stored on `events`.
6. **Every generated row carries `run_id` and `git_sha`.**
7. **No broker client, no order placement, no brokerage credentials.** The absence is the safety property, not a disabled flag.
8. **Every response carrying a probability carries `n_eff` and a confidence interval.**
9. **No magic numbers outside `core/config.py`.**

---

## Platform

Native Windows. The only Linux is inside the Postgres container, and that is transparent.

`ProcessPoolExecutor` uses **spawn**, not fork. Every job module must be importable with no side effects, every entry point needs `if __name__ == "__main__":`, and workers open their own database connections because connections are not picklable. Getting this wrong causes recursive process creation, which looks like a hang.

Scheduling is Windows Task Scheduler with catch-up enabled, not cron or systemd.

## Conventions

- pandas, `float64` in compute, `numeric(12,4)` / `numeric(12,6)` in Postgres
- One ticker per `core/` function call
- DataFrame column names == SQL column names, no translation layer
- Never mutate in place, always return a new object
- Round prices to 4 decimals before any comparison
- `rich.progress` for anything over 30 seconds; checkpoint anything over 10 minutes

---

## Price series

Two series, different purposes. Getting this wrong corrupts every signal.

| Purpose | Series |
|---|---|
| Indicator computation (bands, stochastic, ATR, SMA, drawdown) | Split-adjusted `close` |
| Return measurement | Total-return `adj_close` |
| Live band comparison | Split-adjusted |

**Exception:** `realized_vol` takes total-return adjusted close, because it measures return dispersion rather than price level. This is the only place the two mix inside one module. It requires a comment in the code.

---

## Testing

Write the test before the implementation for anything in `core/`.

Coverage gate: **90% on `core/` only.** No repo-wide target.

Five tests carry the correctness load. Do not weaken any of them:

1. Look-ahead: the shift ladder plus the **signature probe**. The probe is the real guarantee — `detect` may read only `low`, `high`, `ts`, `ticker` from the bar, and receives one indicator row, never a frame. Never widen that signature.
2. Signal path parity (`detect` vs `breach_live` on a simulated intraday path)
3. Determinism (identical config → identical output)
4. Exit invariants (property-based). `mfe >= realized_return` is the sharp one. MFE is **not** clamped at zero — negative MFE is real and DESIGN §5.6 depends on it.
5. Split leakage (structural date bounds + purged fold check)

See `docs/TESTS.md` §3.

---

## Alembic

**The user has not used Alembic before. Treat every migration task as a teaching moment.**

For every migration:

- Explain what the command does *before* running it
- Show the generated file and walk through each line
- Explain what `upgrade()` and `downgrade()` do in that specific case
- Show how to verify (`alembic current`, `\d tablename`)
- **Never** run `--autogenerate` without reading the output aloud first — it misses index and constraint changes and sometimes produces destructive operations

`cscan db migrate` applies to **both** databases by default. Single-target requires an explicit flag. Forgetting the second database is the main way this goes wrong.

---

## Frontend

**Read the frontend-design skill before writing any component.** It carries this environment's design tokens and styling constraints, which `DESIGN.md` does not cover.

Design direction: dense instrument panel, not a marketing page. Monospace for all numbers. Dark by default. Color carries meaning only. Every data component handles five states: loading, empty, suppressed, stale, error.

`lightweight-charts` for price and stochastic panels. `recharts` for statistical charts.

---

## Chat and tools

The response validator requires **sourcing**, not advice avoidance. This is an advisory system; notifications and reports both state what fired and what historically followed.

Passes:
> TSM fired confluence-low today in the 10-20% drawdown bucket. That cell resolved up 3% within 5 sessions in 51% of 340 effective cases against a 39% baseline, CI 46-56.

Fails:
> TSM looks like a good buy here.

Absolute carve-out, no exceptions: **no claims about the user's financial situation, tax position, or suitability.** Nothing in the database sources those.

---

## What "done" means

A task is done when its acceptance criterion in `docs/BUILD.md` passes, not when the code looks finished.

Phase gates are in `docs/TESTS.md` §10. Do not advance past a gate that has not passed.

**Holdout data is evaluated exactly once, at the end, and published whatever it says.**
