# Session 9 standing constraints — read this before writing or reviewing code

## HARD SAFETY RULES — real production data is live

- **Never run bare `pytest` or bare `uv run pytest`.** `pyproject.toml` sets
  `testpaths = ["capitalscan/tests"]`, so a bare invocation collects the
  integration suite, which runs `TRUNCATE TABLE bars CASCADE` against 4.5M rows
  of real data. The ONLY safe form is:
  `uv run pytest capitalscan/tests/unit capitalscan/tests/property`
- **Never run anything under `capitalscan/tests/integration/`.** `test_ingest.py`
  and `test_compute.py` truncate `bars`; `test_poll.py` truncates `tickers`,
  which CASCADEs to `bars`.
- **No `cscan db migrate`, no `uv sync`, no `uv add`.** DDL takes ACCESS
  EXCLUSIVE; Windows locks `.venv` files.
- Do not run any `cscan` command that writes to the database.
- `docker` is not on PATH in agent shells. If you genuinely need the database:
  `PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan`
  Prefix `SET max_parallel_workers_per_gather=0;` on a shared-memory error.

## Project invariants (CLAUDE.md — read it too)

1. **`core/` performs no IO.** `jobs/` and `research/` own all IO.
2. **One implementation.** Never a second band comparison, exit rule, or config
   hash anywhere. Everything routes through `core/`.
3. **Indicators are read at t-1, never t.** The highest-risk silent failure in
   the system. Guarded by `core.signals.detect`'s signature, which may read only
   `low`, `high`, `ts`, `ticker` from the bar and takes ONE indicator row, never
   a frame. **Never widen that signature.**
4. **Never fill, forward-fill, or interpolate a null.** Drop the row and log it
   with a reason. NaN is the honest answer where coverage is absent.
5. **`split_key` assigned at event creation, never at query time.**
5b. No view or query may join statistics on an event's own `split_key`.
6. Every generated row carries `run_id` and `git_sha`.
8. Every response carrying a probability carries `n_eff` and a CI.
9. **No magic numbers outside `core/config.py`** — including thresholds that
   happen to match a default elsewhere. A literal `80.0` in the exit path is a
   review rejection.
10. `core/config.py` holds dataclasses only; its sole import is `dataclasses`.

## Conventions

- pandas, float64 in compute; `numeric(12,4)` / `numeric(12,6)` in Postgres
- One ticker per `core/` function call
- DataFrame column names == SQL column names, no translation layer
- Never mutate in place, always return a new object
- Round prices to 4 decimals before any comparison (`core.signals._breach` does)
- Determinism (ADR 060): sort tickers before dispatch; sort the collected frame
  by `(ticker, signal_date, entry_kind)` before writing; **no wall-clock reads
  inside the engine** — `run_id` and timestamps are injected by the caller
- Windows spawn: every module importable with no side effects; entry points
  guarded by `if __name__ == "__main__":`; workers open their own connections
  (engines are not picklable — pass a URL string, and use
  `engine.url.render_as_string(hide_password=False)`, since `str(engine.url)`
  masks the password)
- Grain: one `events` row per
  `(config_hash, ticker, signal_date, signal_type, entry_kind)`
- Follow the existing code's comment density and idiom. This codebase writes
  substantial comments on *why* a choice was made and what breaks otherwise.
- Write the test before the implementation.

## Controller rulings already made this session

- **C1** `BacktestConfig = core.config.Config` (alias); `config_hash` re-exported
  from `jobs/config.py`. No second config hash.
- **C2** `split_key_for` canonical in `jobs/config.py`, raises below
  `sp.event_start`; `compute._split_key` delegates.
- **C3** t-1 pairing is **date-based** (latest indicator strictly before the bar
  date, per `compute.py:731-738`), never positional `iloc[i-1]`.
- **C4** `db_io.upsert` gains an optional column-scoped update list; `run_events`
  and `run_backtest` each declare the columns they own, so neither nulls the
  other's by omission. (Implemented in Task 9.)
- **C5** `research.candidates.tag_clusters` counts **trading bars**;
  `jobs.compute._tag_clusters` counts calendar days. Deliberate divergence. The
  backtest owns the cluster columns.

## Verified public signatures (research/) as of HEAD 6e05887

The PLAN DOCUMENT IS STALE on several of these. Tasks 5-8 widened them for reasons
the reviews accepted. Use these, not the plan's sketches.

```python
# research/candidates.py
scan_candidates(bars, indicators, sp: SignalParams)
    -> tuple[pd.DataFrame, list[dict]]        # candidates AND null rejects

apply_eligibility(candidates, universe_flags, sp_splits: SplitParams,
                  today: date | None = None)   # <-- INJECT today, never let the
    -> tuple[pd.DataFrame, list[dict]]         #     default date.today() fire

debounce(candidates) -> pd.DataFrame

tag_clusters(candidates, max_hold_days: int,
             trading_dates: dict[str, list[date]]) -> pd.DataFrame
    # RAISES if a ticker is missing from trading_dates, if its list is empty,
    # or if a signal_date is absent from that ticker's list.

# research/enrich.py
resolve_entries(candidate: pd.Series, bars, hourly: pd.DataFrame | None,
                cp: CostParams) -> list[dict]
    # one dict per EntryKind: entry_kind, entry_date, entry_price, entry_gapped

resolve_exit_for_entry(entry: dict, entry_idx: int, side: Side, bars,
                       indicators, ep: ExitParams) -> dict
    # RAISES if entry_idx does not address the bar the position opened on.
    # NEXT_OPEN fills at t+1, so its entry_idx is one bar after the signal bar.
    # Returns exit_idx, exit_date, exit_price, exit_reason, holding_days, ambiguous

path_metrics(entry_price, side: Side, fwd_bars, exit_idx: int | None,
             exit_price, targets: tuple, adj_close_fwd: pd.Series | None,
             horizons: tuple) -> dict
    # adj_close_fwd is TOTAL-RETURN adjusted close (adj_close) — it measures
    # return. fwd_bars is SPLIT-ADJUSTED OHLC. DESIGN §2.2. Reversing them
    # corrupts every downstream number.
    # MFE/MAE window is [t+1, exit_idx]; reachability spans the FULL [t+1, t+5]
    # regardless of exit timing, so fwd_bars must be the full window.

enrich_context(event: dict, ind_row: pd.Series, market_row: pd.Series | None,
               sp: StatsParams, splits: SplitParams, cp: CostParams,
               ep: ExitParams) -> dict
    # SEVEN args. cp and ep both required, no defaults.

# research/backtest.py
BacktestConfig = Config          # alias, ruling C1
config_hash(config) -> str       # re-exported from jobs/config.py
split_key_for(signal_date, sp: SplitParams) -> str   # raises below event_start
```

Constants: `candidates._REQUIRED_INDICATOR_FIELDS = ("bb_lower","bb_upper","k_full")`

## Task 9a landed (HEAD 32d74fb) — signatures Task 9b must call

```python
# jobs/db_io.py
upsert(engine, table_name, data, conflict_cols,
       update_columns: list[str] | None = None) -> int
    # update_columns=None -> unchanged legacy behavior (every non-key column).
    # A list narrows DO UPDATE SET to exactly that list (ruling C4).
    # RAISES ValueError on: an empty list, a name not on the table, or a name
    # that is also a conflict column.

# jobs/compute.py
_RUN_EVENTS_UPDATE_COLUMNS   # signal-side columns run_events owns.
                             # The four cluster columns are DELIBERATELY absent
                             # — the backtest owns them (ruling C5).

# core/universe.py  (consolidated home; takes a loaded frame, performs no IO)
in_trade(universe_flags: pd.DataFrame, ticker: str, signal_date: date) -> bool
    # True when no evaluation exists on or before signal_date (v1 fail-open).
```

**Task 9b must declare its own `_RUN_BACKTEST_UPDATE_COLUMNS` covering the
exit/return/path/context columns it computes PLUS the four cluster columns
(`cluster_id`, `seq_in_cluster`, `is_cluster_head`, `days_since_head`), and
pass it as `update_columns`.**
