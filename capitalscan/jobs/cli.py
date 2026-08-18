"""CapitalScan command-line interface."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

console = Console()

app = typer.Typer(help="CapitalScan event-study engine")

# `jobs.config.resolve_config`'s own default (`config_file="config.toml"`)
# reads the process CWD, which would make a command's resolved config depend
# on the directory the user happened to be standing in when they typed
# `cscan ...` (see `test_jobs_config.py`'s module docstring — the whole
# reason every test there pins an explicit path). The CLI pins the repo
# root instead: `cli.py` lives at `<repo_root>/capitalscan/jobs/cli.py`, so
# two `.parent` calls land on `<repo_root>`, matching the `REPO_ROOT`
# pattern already used in `jobs/db.py`, `jobs/ingest.py`, and `jobs/verify.py`.
_CONFIG_FILE = str(Path(__file__).resolve().parent.parent.parent / "config.toml")


def _resolve_config_or_exit(cli_overrides: Optional[dict] = None):
    """The one call site for `jobs.config.resolve_config` (ADR 091, BUILD
    §0.5). Every command that consumes a config dataclass routes through
    here instead of constructing `Config()` / `IndicatorParams()` / etc.
    directly, so a `config.toml` or `CAPSCAN_*` env var the user set is
    actually honored.

    A `ConfigError` is deliberate, not a bug (see `jobs/config.py`'s module
    docstring): malformed input must raise rather than default, because
    `config_hash` is stamped on every generated row, and a silently
    defaulted config produces a hash asserting parameters that never ran.
    Caught here and turned into the same clean-error idiom every other
    command uses, so it reaches the terminal as a message and a non-zero
    exit, never a traceback.
    """
    from capitalscan.jobs.config import ConfigError, resolve_config

    try:
        return resolve_config(cli_overrides=cli_overrides, config_file=_CONFIG_FILE)
    except ConfigError as exc:
        console.print(f"[red]error[/red]: config resolution failed — {exc}")
        raise typer.Exit(code=1) from None


def _resolve_tickers(tickers: Optional[str]) -> list[str]:
    """`--tickers TSM,NVDA` if given, else every active ticker already on file.

    Falling back to the `tickers` table (not `data/universe_union.csv`)
    keeps this in sync with whatever `cscan tickers --refresh` or an
    earlier `ensure_tickers()` call actually wrote — the CSV is a frozen,
    manually-reviewed artifact (ADR 055), not a live query source.
    """
    from capitalscan.jobs import db_io

    if tickers:
        return [t.strip().upper() for t in tickers.split(",") if t.strip()]

    from sqlalchemy import text

    engine = db_io.get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ticker FROM tickers WHERE is_active")).fetchall()
    if not rows:
        console.print(
            "[red]error[/red]: no --tickers given and no active tickers on file. "
            "Run `cscan tickers --refresh` first, or pass --tickers explicitly."
        )
        raise typer.Exit(code=1)
    return [r[0] for r in rows]


@app.command()
def calendar(
    through: int = typer.Option(2027, help="Last year to include"),
) -> None:
    """Populate NYSE trading calendar."""
    from capitalscan.jobs import ingest

    report = ingest.run_calendar(through)
    console.print(f"calendar: wrote {report.rows_written} trading_days rows through {through}")


@app.command()
def tickers(
    refresh: bool = typer.Option(False, help="Refresh ticker list from data source"),
) -> None:
    """Sync ticker reference data."""
    from capitalscan.jobs import ingest

    if not refresh:
        console.print("[yellow]nothing to do[/yellow]: pass --refresh")
        raise typer.Exit(code=1)
    report = ingest.run_tickers_refresh()
    console.print(f"tickers: upserted {report.rows_written} rows ({len(report.tickers)} tickers)")


@app.command()
def membership(
    backfill: bool = typer.Option(False, help="Backfill membership history"),
    force: bool = typer.Option(False, help="Regenerate even if the CSV is reviewed and frozen"),
) -> None:
    """Build universe membership from S&P 500 history."""
    from capitalscan.jobs import ingest

    if not backfill:
        console.print("[yellow]nothing to do[/yellow]: pass --backfill")
        raise typer.Exit(code=1)
    try:
        report = ingest.run_membership(force=force)
    except ingest.UniverseFrozenError as exc:
        # A frozen file is the expected end state, not a crash: report it as
        # a refusal rather than letting a traceback reach the terminal.
        console.print(f"[yellow]membership: nothing to do[/yellow] — {exc}")
        raise typer.Exit(code=0) from None
    console.print(f"membership: {report.notes}")


@app.command()
def bars(
    daily: bool = typer.Option(False, help="Fetch daily bars"),
    hourly: bool = typer.Option(False, help="Fetch hourly bars"),
    backfill: bool = typer.Option(False, help="Full hourly backfill (ignores --lookback)"),
    lookback: int = typer.Option(5, help="Days to look back"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Fetch OHLCV data."""
    from capitalscan.jobs import ingest

    if not daily and not hourly:
        console.print("[red]error[/red]: pass --daily and/or --hourly")
        raise typer.Exit(code=1)

    resolved = _resolve_tickers(tickers)
    end = date.today()
    if daily:
        start = end - timedelta(days=lookback)
        report = ingest.run_bars_daily(resolved, start, end)
        console.print(
            f"bars --daily: {report.rows_written} written, "
            f"{report.rows_rejected} rejected, {report.rows_flagged} flagged"
        )
    if hourly:
        # ADR/DESIGN §4.4: Yahoo caps hourly history at 730 days regardless
        # of --backfill; `run_bars_hourly` windows internally either way.
        start = end - timedelta(days=730 if backfill else lookback)
        console.print(f"[dim]hourly: fetching from {start} to {end}[/dim]")
        report = ingest.run_bars_hourly(resolved, start, end)
        console.print(
            f"bars --hourly: {report.rows_written} written, "
            f"{report.rows_rejected} rejected, {report.rows_flagged} flagged"
        )


@app.command()
def actions(
    lookback: int = typer.Option(30, help="Days to look back"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Fetch corporate actions (splits, dividends)."""
    from capitalscan.jobs import ingest

    resolved = _resolve_tickers(tickers)
    report = ingest.run_actions(resolved)
    console.print(f"actions: upserted {report.rows_written} rows for {len(report.tickers)} tickers")


@app.command()
def market(
    lookback: int = typer.Option(5, help="Days to look back"),
) -> None:
    """Fetch market indices (SPX, VIX)."""
    from capitalscan.jobs import ingest

    report = ingest.run_market(lookback_days=lookback)
    console.print(f"market: upserted {report.rows_written} market_days rows")


@app.command()
def shares(
    since_last: bool = typer.Option(False, help="Only fetch new filings"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Fetch shares outstanding from SEC XBRL."""
    from capitalscan.jobs import ingest

    resolved = _resolve_tickers(tickers)
    report = ingest.run_shares(resolved)
    console.print(f"shares: upserted {report.rows_written} rows for {len(report.tickers)} tickers")


@app.command()
def earnings(
    historical: bool = typer.Option(False, help="Backfill historical earnings from SEC"),
    forward: int = typer.Option(0, help="Days forward to fetch from Finnhub"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Fetch earnings dates."""
    from capitalscan.jobs import ingest

    if not historical and forward <= 0:
        console.print("[red]error[/red]: pass --historical and/or --forward N")
        raise typer.Exit(code=1)
    resolved = _resolve_tickers(tickers)
    report = ingest.run_earnings(resolved, historical=historical, forward_days=forward)
    console.print(
        f"earnings: upserted {report.rows_written} rows for {len(report.tickers)} tickers"
    )


@app.command()
def indicators(
    lookback: int = typer.Option(5, help="Days to look back"),
    only: Optional[str] = typer.Option(
        None, help="Not yet supported: compute_all runs the full registry"
    ),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
    workers: int = typer.Option(1, help="ProcessPoolExecutor workers; 1 runs serially"),
) -> None:
    """Compute technical indicators."""
    from capitalscan.jobs import compute

    if only:
        console.print("[yellow]note[/yellow]: --only is not implemented; computing every indicator")
    config = _resolve_config_or_exit()
    resolved = _resolve_tickers(tickers)
    end = date.today()
    start = end - timedelta(days=lookback)
    report = compute.run_indicators(
        resolved, start, end, params=config.indicators, max_workers=workers
    )
    console.print(
        f"indicators: {report.rows_written} written, {report.rows_flagged} tickers skipped "
        f"(insufficient history)"
    )


@app.command()
def universe(
    quarter: Optional[str] = typer.Option(None, help="Quarter to evaluate (e.g. 2026Q3)"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Evaluate universe membership criteria."""
    from capitalscan.jobs import compute

    if not quarter:
        console.print("[red]error[/red]: --quarter is required (e.g. 2026Q3)")
        raise typer.Exit(code=1)
    config = _resolve_config_or_exit()
    resolved = [t.strip().upper() for t in tickers.split(",")] if tickers else None
    report = compute.run_universe(quarter, tickers=resolved, up=config.universe)
    console.print(f"universe: evaluated {len(report.tickers)} tickers as of {quarter}")


@app.command()
def events(
    lookback: int = typer.Option(5, help="Days to look back"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Detect signal events."""
    from capitalscan.jobs import compute

    config = _resolve_config_or_exit()
    resolved = _resolve_tickers(tickers)
    end = date.today()
    start = end - timedelta(days=lookback)
    # `--lookback` has a hard floor here that no other command has, and
    # overshooting it costs a full scan before anything raises.
    # `jobs.config.split_key_for` refuses any `signal_date` before
    # `event_start` — deliberately, since labelling a pre-2010 row `train`
    # is a leakage bug — but it fires per row inside `run_events`'s build
    # loop, minutes in. `--lookback 6100` on 2026-08-14 asked for
    # 2009-12-01 and died there with 0 rows written.
    #
    # Clamping the requested *window* is a different thing from
    # mislabelling a *row*, so the guard downstream stays exactly as
    # strict. Printed rather than silent: a run that quietly covers less
    # history than asked for is the kind of thing that gets discovered
    # much later, in a population that is short at one end.
    event_start = date.fromisoformat(config.splits.event_start)
    if start < event_start:
        console.print(
            f"[yellow]note[/yellow]: --lookback {lookback} reaches {start.isoformat()}, "
            f"before SplitParams.event_start {event_start.isoformat()}; "
            f"clamping start to {event_start.isoformat()}"
        )
        start = event_start
    report = compute.run_events(resolved, start, end, config=config)
    console.print(
        f"events: {report.rows_written} written, "
        f"{report.rows_flagged} bars skipped (null indicator)"
    )


# Columns `_load_bars_by_ticker` selects off `indicators` for the harness's
# `bars_by_ticker` shape (research/harness.py module docstring: "bars *and*
# indicators merged, one frame per ticker"). Named here, not inlined into
# the SQL string, so the list is legible and `interval`/`computed_at`/
# `run_id` (present on the table but not indicator *values*) are never
# accidentally swept into the no-look-ahead check's shift ladder — that
# check treats every column outside `harness._BAR_COLUMNS` as an indicator
# column to shift, so an unfiltered `SELECT *` would corrupt it.
_BACKTEST_INDICATOR_COLUMNS = [
    "bb_mid",
    "bb_upper",
    "bb_lower",
    "bb_pctb",
    "bb_width",
    "bb_width_pct",
    "k_fast",
    "d_fast",
    "k_full",
    "d_full",
    "k_cross_up",
    "k_cross_down",
    "sma_200",
    "sma200_slope_60",
    "atr_14",
    "rv_20d",
    "rv_pct_252d",
    "vol_z_20d",
    "dd_52w",
    "days_to_earnings",
]


def _prior_clean_default_run_exists(engine, config_hash: str) -> bool:
    """ADR 059's `--sweep` ordering gate: "the first backtest run uses a
    single default config... before any sweep executes."

    "Clean" here means all three of:
      - `status = 'ok'` — the run completed without `BacktestRunFailed`
        (a config-level fault would leave `status = 'failed'`).
      - `notes IS NULL` — `backtest()` below writes a non-null note the
        moment even one ticker fails (`BacktestReport.failed_tickers`), so
        a run that "succeeded" but silently dropped 50 of 600 tickers does
        not count as clean either. ADR 059 exists precisely to catch a
        buggy engine before a sweep; a run with unexplained per-ticker
        failures is exactly the "buggy engine" case it is guarding against.
      - `params->>'full_universe' = 'true'` — a `--tickers` debug run is a
        deliberate subset (see `run_backtest`'s own `full_universe`
        docstring) and never asserts coverage of the whole trade universe,
        so it cannot stand in for the full-universe validation ADR 059
        requires before an 18-config sweep.

    `config_hash` matching is the fourth leg: this checks for a clean run
    of *the current default config specifically*, not any prior backtest
    run — a clean run of some other config says nothing about whether this
    one behaves.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM runs WHERE job = 'backtest' AND status = 'ok' "
                "AND notes IS NULL AND params->>'config_hash' = :chash "
                "AND params->>'full_universe' = 'true' LIMIT 1"
            ),
            {"chash": config_hash},
        ).fetchone()
    return row is not None


def _sweep_config_already_done(engine, config_hash: str) -> bool:
    """Task 12's per-config checkpoint/resume check for `--sweep`.

    Mirrors `_prior_clean_default_run_exists` exactly, but scoped to
    `job = 'backtest_sweep'` rather than `job = 'backtest'` — a sweep
    member and the Task 11 default-config run are different jobs in the
    `runs` table on purpose, so a clean default run is never mistaken for a
    completed sweep member (which would wrongly skip every one of the 18
    configs) and a completed sweep member is never mistaken for the
    default-config run the ADR 059 gate above looks for.

    A full-universe sweep run of ~20 minutes' write phase per config makes
    18 configs ~6 hours (BUILD §9.10, CONSTRAINTS.md), well past CLAUDE.md's
    10-minute checkpoint threshold. `run_backtest`'s single `db_io.upsert`
    at the end of each config's dispatch is that config's checkpoint unit:
    it either lands whole or not at all. This function is what lets a
    rerun after an interrupt recognize "config 7 already has a clean,
    full-universe `runs` row" and skip straight to resuming at 8, instead
    of either re-running (correct but ~20 minutes wasted per already-done
    config, since `events` writes are idempotent on `(config_hash, ticker,
    signal_date, signal_type, entry_kind)`) or — worse — silently skipping
    a config that never actually finished.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM runs WHERE job = 'backtest_sweep' AND status = 'ok' "
                "AND notes IS NULL AND params->>'config_hash' = :chash "
                "AND params->>'full_universe' = 'true' LIMIT 1"
            ),
            {"chash": config_hash},
        ).fetchone()
    return row is not None


def _load_bars_by_ticker(engine, tickers: list[str], config) -> dict:
    """One bars+indicators frame per ticker, left-joined on `ts`, in the
    shape `research.harness.run_harness` requires (see module docstring on
    `_BACKTEST_INDICATOR_COLUMNS` above). `research.backtest._read_bars` /
    `_read_indicators` read the same two tables but keep them as two
    separate frames per ticker — this is a distinct read for the harness
    specifically, not a second copy of the backtest engine's own IO
    (invariant 2 covers detection/exit/config logic, not "which columns a
    caller selects off an already-written table").

    Starts from `config.splits.ingest_start`, same lower bound
    `_backtest_one_ticker` uses for `bars`, so the no-look-ahead check's
    shift ladder has the same history available that the run itself did.
    A ticker with no bars in that window is omitted from the result rather
    than included with an empty frame — `research.harness._indexed_bars`
    already drops empty frames, so this just avoids the pointless read.
    """
    import pandas as pd
    from sqlalchemy import text

    start = date.fromisoformat(config.splits.ingest_start)
    indicator_cols_sql = ", ".join(_BACKTEST_INDICATOR_COLUMNS)
    out: dict = {}
    with engine.connect() as conn:
        for ticker in tickers:
            read_params: dict[str, Any] = {"ticker": ticker, "start": start}
            bars = pd.read_sql(
                text(
                    "SELECT ticker, ts, open, high, low, close, adj_close, volume "
                    "FROM bars WHERE ticker = :ticker AND interval = '1d' AND ts >= :start "
                    "ORDER BY ts"
                ),
                conn,
                params=read_params,
            )
            if bars.empty:
                continue
            bars["ts"] = pd.to_datetime(bars["ts"]).dt.tz_localize(None)

            indicators = pd.read_sql(
                text(
                    f"SELECT ts, {indicator_cols_sql} FROM indicators "
                    "WHERE ticker = :ticker AND interval = '1d' AND ts >= :start ORDER BY ts"
                ),
                conn,
                params=read_params,
            )
            if indicators.empty:
                out[ticker] = bars
                continue
            indicators["ts"] = pd.to_datetime(indicators["ts"]).dt.tz_localize(None)
            out[ticker] = bars.merge(indicators, on="ts", how="left")
    return out


def _load_hourly_by_ticker(engine, tickers: list[str], config) -> dict:
    """One raw hourly-bar frame per ticker, for `research.harness.run_harness`'s
    `hourly_by_ticker` argument.

    Kept as its own read rather than folded into `_load_bars_by_ticker`
    (see `run_harness`'s docstring on why the two are separate parameters):
    that function's frame is one row per `(ticker, date)`, the grain the
    no-look-ahead/exit/non-overlap checks all rely on, while hourly bars
    are several rows per date — merging them in would break that grain for
    every check except `entry_sanity`, the only one that reads hourly data
    at all (DESIGN §5.4: `TOUCH_5M`/`TOUCH_30M` price from an hourly bar,
    `core.returns.entry_price_for` -> `_first_hourly_touch`).

    Same `config.splits.ingest_start` lower bound as `_load_bars_by_ticker`
    and `research.backtest._backtest_one_ticker`'s own hourly read
    (`_read_bars(engine, ticker, ingest_start, "1h")`), so the harness sees
    the same hourly history the backtest run itself had available. A
    ticker with no hourly rows in that window is omitted, same convention
    `_load_bars_by_ticker` uses — `entry_sanity` already treats a missing
    ticker in `hourly_by_ticker` as "cannot validate this row," not a
    silent pass.
    """
    import pandas as pd
    from sqlalchemy import text

    start = date.fromisoformat(config.splits.ingest_start)
    out: dict = {}
    with engine.connect() as conn:
        for ticker in tickers:
            read_params: dict[str, Any] = {"ticker": ticker, "start": start}
            hourly = pd.read_sql(
                text(
                    "SELECT ticker, ts, open, high, low, close, adj_close, volume "
                    "FROM bars WHERE ticker = :ticker AND interval = '1h' AND ts >= :start "
                    "ORDER BY ts"
                ),
                conn,
                params=read_params,
            )
            if hourly.empty:
                continue
            hourly["ts"] = pd.to_datetime(hourly["ts"]).dt.tz_localize(None)
            out[ticker] = hourly
    return out


def _load_events_for_run(engine, run_id: str):
    """The `events` rows this run itself wrote — the harness's `events`
    argument. Scoped to `run_id`, not `config_hash`, so a rerun against the
    same default config does not pull in a previous run's rows alongside
    this one's (upsert means both share the same `(config_hash, ticker,
    signal_date, signal_type, entry_kind)` keys, but only the most recent
    write carries this run's `run_id`).
    """
    import pandas as pd
    from sqlalchemy import text

    with engine.connect() as conn:
        return pd.read_sql(
            text("SELECT * FROM events WHERE run_id = :run_id"), conn, params={"run_id": run_id}
        )


def _print_harness_report(report) -> None:
    checks = [
        report.no_lookahead,
        report.entry_sanity,
        report.exit_sanity,
        report.return_identity,
        report.non_overlap,
    ]
    console.print("[bold]harness (DESIGN §5.10, ADR 059)[/bold]")
    for check in checks:
        color = "green" if check.passed else "red"
        status = "PASS" if check.passed else f"FAIL ({len(check.violations)} violation(s))"
        console.print(f"  [{color}]{status}[/{color}]  {check.name}")
    if not report.all_passed:
        console.print(
            "[red]harness gate FAILED[/red] — do not sweep and do not hand-inspect "
            "events yet; the failing check(s) above point at the engine, not the data."
        )


@app.command()
def backtest(
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
    workers: int = typer.Option(1, help="ProcessPoolExecutor workers; 1 runs serially"),
    sweep: bool = typer.Option(
        False, help="Run the full config sweep (ADR 059: requires a prior clean default-config run)"
    ),
    config_name: Optional[str] = typer.Option(
        None, "--config-name", help="Not implemented — see `cscan backtest --help` output on error"
    ),
) -> None:
    """Run the backtest engine (DESIGN §5): default config in, `events` rows
    out, then the Phase 3 validation harness (DESIGN §5.10) against what
    this run wrote.

    `--config-name` names a config to run, but there is no named-config
    registry to select from: `jobs.config.resolve_config` layers CLI/env/
    `config.toml`/dataclass-default into exactly one config per invocation,
    with no name attached to any of them. Accepting the flag and silently
    ignoring its value would be worse than refusing outright (Task 11
    brief), so any value here is a hard error.

    The harness runs automatically after every non-sweep run (not behind a
    separate flag): ADR 059 requires it before any sweep or hand-inspection
    happens, and it is cheap relative to the backtest itself — it re-reads
    only the `events` rows and bars/indicators this run touched, never the
    whole `events` table. A run with zero events written skips it (nothing
    to check); a run with events but a failing harness check still exits
    non-zero.

    `--sweep` runs ADR 059's ordering gate first (Task 11), then, once it
    passes, the full 18-config exit sweep itself (DESIGN §5.9, Task 12):
    `research.backtest.sweep_configs` generates the 18 variants and each is
    dispatched through the same `run_backtest` a single default run uses,
    one `runs` row per config (see `_sweep_config_already_done`'s
    docstring for the checkpoint/resume story). The Phase 3 harness is
    **not** rerun per sweep config — DESIGN §5.9's ordering rule is that
    the *default* config passes the full harness before any sweep runs,
    which the ADR 059 gate above already establishes; rerunning it 18 more
    times (~2.5h each, per BUILD §9.10) would turn a ~6 hour sweep into
    a multi-day one for no additional signal, since every config shares
    the same detection/entry engine the harness already validated — only
    the exit parameters vary.
    """
    from dataclasses import asdict

    from rich.progress import Progress

    from capitalscan.jobs import db_io, ingest
    from capitalscan.jobs.config import config_hash as compute_config_hash
    from capitalscan.research.backtest import BacktestRunFailed, run_backtest, sweep_configs
    from capitalscan.research.harness import run_harness

    if config_name is not None:
        console.print(
            "[red]error[/red]: --config-name is not implemented. There is no "
            "named-config registry — `jobs.config.resolve_config` resolves exactly "
            "one config per invocation from CLI/env/config.toml/dataclass defaults. "
            "Drop --config-name; the default config is used unconditionally."
        )
        raise typer.Exit(code=1)

    config = _resolve_config_or_exit()
    chash = compute_config_hash(config)
    engine = db_io.get_engine()

    if sweep:
        if not _prior_clean_default_run_exists(engine, chash):
            console.print(
                "[red]error[/red]: --sweep refused (ADR 059). Sweeping over a buggy "
                "engine produces 18 confidently wrong answers with no signal that "
                f"anything is wrong. No prior clean, full-universe, default-config "
                f"backtest run (config_hash={chash}, status='ok', no failed tickers) "
                "was found in `runs`. Run `cscan backtest` (no --tickers, no --sweep) "
                "first, confirm the harness passes, hand-inspect ~20 events, then "
                "retry --sweep."
            )
            raise typer.Exit(code=1)

        resolved = _resolve_tickers(tickers)
        full_universe = tickers is None
        configs = sweep_configs(config)  # deterministic order (ADR 060) — pure function of `config`

        console.print(
            f"[bold]sweep[/bold]: {len(configs)} configs x {len(resolved)} tickers "
            "(DESIGN §5.9, Task 12)"
        )

        n_run = 0
        n_skipped = 0
        with Progress() as progress:
            task = progress.add_task("[cyan]sweep...", total=len(configs))
            for i, sweep_config in enumerate(configs, start=1):
                sweep_hash = compute_config_hash(sweep_config)
                progress.update(
                    task, description=f"[cyan]config {i}/{len(configs)} {sweep_hash}[/cyan]"
                )

                # Resume: a config already checkpointed by a prior --sweep
                # invocation is never re-dispatched. Idempotent either way
                # (upsert keys on config_hash, not run_id) but re-running a
                # finished config wastes ~20 minutes of write phase for
                # nothing (docstring on `_sweep_config_already_done`).
                if _sweep_config_already_done(engine, sweep_hash):
                    n_skipped += 1
                    progress.update(task, advance=1)
                    continue

                sweep_params = {
                    "config_hash": sweep_hash,
                    "config": asdict(sweep_config),
                    "full_universe": full_universe,
                    "workers": workers,
                    "n_tickers": len(resolved),
                    "sweep_index": i,
                }
                try:
                    with ingest.run_job(engine, "backtest_sweep", sweep_params) as report:
                        bt_report = run_backtest(
                            resolved,
                            sweep_config,
                            report.run_id,
                            engine=engine,
                            max_workers=workers,
                            full_universe=full_universe,
                        )
                        report.rows_written = bt_report.rows_written
                        if bt_report.failed_tickers:
                            failed = sorted(bt_report.failed_tickers)
                            sample = ", ".join(failed[:10])
                            more = "" if len(failed) <= 10 else f", +{len(failed) - 10} more"
                            report.notes = (
                                f"{len(failed)}/{len(resolved)} ticker(s) failed: {sample}{more}"
                            )
                except BacktestRunFailed as exc:
                    console.print(
                        f"[red]error[/red]: sweep config {i}/{len(configs)} "
                        f"(config_hash={sweep_hash}) failed — every dispatched "
                        f"ticker's worker raised, which points at this config, not "
                        f"the data. {exc}"
                    )
                    console.print(
                        f"[yellow]{n_run} config(s) already completed and written "
                        f"before this failure; rerun `cscan backtest --sweep` to "
                        f"resume at config {i}.[/yellow]"
                    )
                    raise typer.Exit(code=1) from None

                n_run += 1
                progress.update(task, advance=1)

        console.print(
            f"[bold]sweep complete[/bold]: {n_run} config(s) run, "
            f"{n_skipped} already done (resumed)"
        )
        return

    resolved = _resolve_tickers(tickers)
    # A `--tickers` subset is a partial run by definition: `run_backtest`'s
    # `full_universe` guard exists so this cannot silently overwrite a
    # previously-correct universe-wide `cofire_count` (see its docstring).
    full_universe = tickers is None

    run_params = {
        "config_hash": chash,
        "config": asdict(config),
        "full_universe": full_universe,
        "workers": workers,
        "n_tickers": len(resolved),
    }

    try:
        with ingest.run_job(engine, "backtest", run_params) as report:
            bt_report = run_backtest(
                resolved,
                config,
                report.run_id,
                engine=engine,
                max_workers=workers,
                full_universe=full_universe,
            )
            report.rows_written = bt_report.rows_written
            if bt_report.failed_tickers:
                failed = sorted(bt_report.failed_tickers)
                sample = ", ".join(failed[:10])
                more = "" if len(failed) <= 10 else f", +{len(failed) - 10} more"
                report.notes = f"{len(failed)}/{len(resolved)} ticker(s) failed: {sample}{more}"
    except BacktestRunFailed as exc:
        console.print(
            "[red]error[/red]: backtest run failed — every dispatched ticker's "
            f"worker raised the same fault, which points at the config, not the "
            f"data. {exc}"
        )
        raise typer.Exit(code=1) from None

    console.print(f"[bold]config_hash[/bold]: {chash}")
    console.print(
        f"backtest: run_id={bt_report.run_id} rows_written={bt_report.rows_written} "
        f"tickers={len(bt_report.tickers)}/{len(resolved)}"
    )

    exit_code = 0
    if bt_report.failed_tickers:
        exit_code = 1
        console.print(
            f"[red]{len(bt_report.failed_tickers)} ticker(s) failed[/red]: {report.notes}"
        )

    if bt_report.tickers:
        bars_by_ticker = _load_bars_by_ticker(engine, bt_report.tickers, config)
        hourly_by_ticker = _load_hourly_by_ticker(engine, bt_report.tickers, config)
        events_for_harness = _load_events_for_run(engine, bt_report.run_id)
        harness_report = run_harness(
            events_for_harness, bars_by_ticker, config, hourly_by_ticker=hourly_by_ticker
        )
        _print_harness_report(harness_report)
        if not harness_report.all_passed:
            exit_code = 1
    else:
        console.print("[yellow]harness skipped[/yellow]: no events written")

    if exit_code:
        raise typer.Exit(code=exit_code)


SIGNAL_TYPE_LABELS = {
    "bb_lower_touch": "Bollinger Band Lower Bound",
    "bb_upper_touch": "Bollinger Band Upper Bound",
    "stoch_oversold": "Stochastic Oversold",
    "stoch_overbought": "Stochastic Overbought",
    "confluence_low": "Confluence Low",
    "confluence_high": "Confluence High",
    "bear_close_above_upper": "Bear Reversal Above Upper Band",
}

SIDE_LABELS = {
    "long": "Long",
    "short": "Short",
}

COLUMN_LABELS = {
    "ticker": "Ticker",
    "signal_date": "Date",
    "signal_type": "Signal Type",
    "signal_types_all": "All Signals",
    "signal_strength": "Signal Strength",
    "side": "Direction",
    "bb_upper": "Upper Bound (Prior Day)",
    "bb_upper_sameday": "Upper Bound (Same Day)",
    "bb_mid": "20-day MA (Split-Adjusted)",
    "bb_lower": "Lower Bound",
    "k_full": "Full Stochastic Value",
    "k_fast": "Fast Stochastic Value",
    "k_cross_up": "K Crossed Up",
    "k_cross_down": "K Crossed Down",
    "dd_bucket": "52-Week Drawdown Range",
    "trend_state": "Price vs 200-day MA",
}


def _map_signal_types_for_csv(signal_types_val):
    """Convert signal types array to human-readable labels."""
    if signal_types_val is None or (isinstance(signal_types_val, str) and signal_types_val == "[]"):
        return ""

    import ast

    types_list = signal_types_val
    if isinstance(signal_types_val, str):
        try:
            types_list = ast.literal_eval(signal_types_val)
        except (ValueError, SyntaxError):
            return str(signal_types_val)

    if not isinstance(types_list, (list, tuple)):
        return str(signal_types_val)

    labels = [SIGNAL_TYPE_LABELS.get(str(t), str(t)) for t in types_list]
    return "; ".join(labels)


@app.command()
def scan(
    ticker: Optional[str] = typer.Option(None, help="Single ticker"),
    universe: str = typer.Option("trade", help="Universe: train or trade"),
    start: Optional[str] = typer.Option(None, help="Start date (YYYY-MM-DD)"),
    end: Optional[str] = typer.Option(None, help="End date (YYYY-MM-DD)"),
    date_: Optional[str] = typer.Option(None, "--date", help="Specific date (YYYY-MM-DD)"),
    confluence_only: bool = typer.Option(
        False, help="Show only confluence signals (both Bollinger and stochastic agree)"
    ),
    bear_close_only: bool = typer.Option(
        False,
        "--bear-close-only",
        help="Show only ADR 109 close-confirmed reversals: the day opened above where it "
        "closed, and the close still held at or above that same day's upper band",
    ),
    actionable: bool = typer.Option(
        False,
        "--actionable",
        help="Longs on confluence alone, shorts only when a close-confirmed reversal "
        "agrees. Asymmetric by design (ADR 016) — see the command docstring",
    ),
) -> None:
    """Query detected events (ADR 049).

    `--confluence-only` and `--bear-close-only` compose as an **AND**:
    passing both shows the intersection, which is the population the
    poller's live tag highlights.

    `--actionable` is the **OR** the other two cannot express (user's
    request, 2026-08-17):

        confluence_low  OR  (confluence_high AND bear_close_above_upper)

    A long needs confluence on its own; a short needs confluence *plus*
    close confirmation. That asymmetry is deliberate and is ADR 016's
    position that the short side is not a mirror of the long — a bounce off
    the lower band is one event, while a push through the upper band is
    only interesting once the day gives it back.

    On 2026-08-03..2026-08-14 this selected 37 rows: 30 longs and 7 shorts,
    excluding 73 `confluence_high` rows with no reversal to confirm them.

    `--actionable` is mutually exclusive with the other two rather than
    composing with them. Intersecting an OR-of-two-conditions with an
    AND-of-two-others produces a set nobody can state in one sentence, and
    a filter whose meaning cannot be stated is a filter that gets
    misread.
    """
    from datetime import date as date_cls

    from capitalscan.jobs import compute

    day = date_cls.fromisoformat(date_) if date_ else None
    start_date = date_cls.fromisoformat(start) if start else day
    end_date = date_cls.fromisoformat(end) if end else day
    tickers = [ticker.upper()] if ticker else None

    result = compute.scan(tickers=tickers, start=start_date, end=end_date, universe=universe)
    if result.empty:
        console.print("[yellow]no events found[/yellow]")
        raise typer.Exit(code=0)

    # Both filters read `signal_types_all`, never `signal_type`.
    #
    # `signal_type` holds only the *most specific* type that fired (ADR 057),
    # and ADR 108 ranks `bear_close_above_upper` above `confluence_high`. A
    # bar firing both therefore reports `signal_type = bear_close_above_upper`,
    # so the original `signal_type.isin([...])` form silently stopped
    # matching confluences the moment the new type shipped — it would have
    # dropped exactly the rows this session set out to surface. Filtering on
    # the full concurrent set is both the fix and the correct question:
    # "did this fire" rather than "did this outrank everything else".
    def _fired(row, wanted: set[str]) -> bool:
        return bool(wanted & set(row or []))

    if actionable and (confluence_only or bear_close_only):
        console.print(
            "[red]error[/red]: --actionable already states its own rule and does not "
            "compose with --confluence-only or --bear-close-only. Pass it alone."
        )
        raise typer.Exit(code=2)

    if actionable:
        # The long side asks only for confluence. The short side asks for
        # confluence AND the close-confirmed reversal, so an upper-band
        # push with no giveback is not surfaced.
        #
        # `bear_close_above_upper` alone is deliberately *not* enough: it
        # fires without any stochastic extreme (see
        # `test_the_flag_alone_fires_without_any_stochastic_extreme`), and
        # this filter is the "act on it" view rather than the "it fired"
        # view. `--bear-close-only` remains the way to see those.
        keep = result["signal_types_all"].map(
            lambda t: (
                _fired(t, {"confluence_low"})
                or (_fired(t, {"confluence_high"}) and _fired(t, {"bear_close_above_upper"}))
            )
        )
        result = result[keep]
        if result.empty:
            console.print("[yellow]no actionable events found[/yellow]")
            raise typer.Exit(code=0)

    if confluence_only:
        keep = result["signal_types_all"].map(
            lambda t: _fired(t, {"confluence_low", "confluence_high"})
        )
        result = result[keep]
        if result.empty:
            console.print("[yellow]no confluence events found[/yellow]")
            raise typer.Exit(code=0)

    if bear_close_only:
        keep = result["signal_types_all"].map(lambda t: _fired(t, {"bear_close_above_upper"}))
        result = result[keep]
        if result.empty:
            console.print("[yellow]no close-confirmed reversal events found[/yellow]")
            raise typer.Exit(code=0)

    console.print(result.to_string(index=False))

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    csv_path = reports_dir / f"{timestamp}.csv"

    csv_export = result.copy()
    csv_export["signal_type"] = csv_export["signal_type"].map(
        lambda x: SIGNAL_TYPE_LABELS.get(x, x)
    )
    csv_export["signal_types_all"] = csv_export["signal_types_all"].apply(_map_signal_types_for_csv)
    csv_export["side"] = csv_export["side"].map(lambda x: SIDE_LABELS.get(x, x))

    # Mark which %K column actually decided oversold/overbought.
    #
    # `SignalParams.stoch_source` selects it, and ADR 110 moved it to
    # `k_fast`. `core/signals.py` still records `k_full` on every
    # `SignalHit` unconditionally, so the CSV exports both columns and
    # neither says which one fired the row. A reader checking a signal
    # against a chart has no way to tell — the same shape of defect as the
    # `t-1` band column ADR 109 exposed.
    #
    # Read from the resolved config rather than hardcoded, so a `k_full`
    # run labels itself correctly too.
    labels = dict(COLUMN_LABELS)
    trigger = _resolve_config_or_exit().signals.stoch_source
    if trigger in labels:
        labels[trigger] = f"{labels[trigger]} [TRIGGER]"

    csv_export = csv_export.rename(columns=labels)
    csv_export.to_csv(csv_path, index=False)
    console.print(f"\n[green]saved to[/green] {csv_path}")


@app.command()
def sync(
    to_serving: bool = typer.Option(False, help="Sync to serving database"),
) -> None:
    """Sync research database to serving database."""
    raise NotImplementedError("sync")


@app.command()
def poll(
    interval: int = typer.Option(300, help="Poll interval in seconds"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Run live band-touch poller until market close (DESIGN §4.8).

    Resolves config via `_resolve_config_or_exit`, which reads and validates
    all seven config sections at once (`jobs.config.resolve_config`'s
    all-or-nothing contract) even though `poll` only consumes
    `signals`/`exits`/`stats`. A malformed `CAPSCAN_COSTS` or
    `CAPSCAN_UNIVERSE` — sections this command never touches — will still
    block startup with a `ConfigError`. This is deliberate (ADR 091: raise
    rather than default), not a `poll`-specific bug, but it means a poller
    refusing to start at market open can be caused by an env var this
    command doesn't otherwise care about.
    """
    from capitalscan.jobs import poll as poll_job

    config = _resolve_config_or_exit()
    resolved = [t.strip().upper() for t in tickers.split(",")] if tickers else None
    report = poll_job.run_poll(
        interval=interval,
        tickers=resolved,
        sp=config.signals,
        ep=config.exits,
        stats=config.stats,
        # The whole config, so `run_poll` hashes what was actually resolved
        # rather than rebuilding a partial one and minting an identity no
        # other job writes under.
        config=config,
    )
    console.print(f"poll: session ended, {report.rows_written} events fired")
    if report.notes:
        console.print(f"[yellow]notes[/yellow]: {report.notes}")


@app.command()
def validate(
    report: bool = typer.Option(False, help="Print validation report"),
) -> None:
    """Validate ingested data: reject counts, coverage, missing-bar and reject checks.

    The Stooq cross-check (DESIGN §2.1, §2.3, §4.11) was removed 2026-08-01:
    the vendor began serving a JavaScript proof-of-work challenge to
    automated requests on every endpoint tried, so the pipeline is now
    single-source on Yahoo.
    """
    from capitalscan.jobs import ingest

    result = ingest.run_validate()
    if report:
        ingest.print_validation_report(result)
    else:
        status = "clean" if result.clean else "NOT clean"
        console.print(f"validation: {status}")

    if not result.clean:
        raise typer.Exit(code=1)


@app.command()
def backfill(
    all: bool = typer.Option(False, help="Backfill every active ticker on file"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
    start: Optional[str] = typer.Option(None, help="Start date (YYYY-MM-DD)"),
    through_validate: bool = typer.Option(False, help="Stop at validation gate"),
    resume: bool = typer.Option(False, help="No-op: all writes are upserts, so a rerun is safe"),
) -> None:
    """Run the ingest backfill pipeline (DESIGN §4.11): calendar, tickers, bars,
    actions, market, then the validation gate.
    """
    from capitalscan.jobs import ingest

    if not all and not tickers:
        console.print("[red]error[/red]: pass --all or --tickers")
        raise typer.Exit(code=1)
    if not start:
        console.print("[red]error[/red]: --start is required (YYYY-MM-DD)")
        raise typer.Exit(code=1)

    resolved = _resolve_tickers(tickers) if tickers else _resolve_tickers(None)
    start_date = date.fromisoformat(start)
    result = ingest.run_backfill(resolved, start_date, through_validate=through_validate)

    for step in result.steps:
        console.print(f"  {step.job}: {step.rows_written} written, {step.rows_rejected} rejected")
    ingest.print_validation_report(result.validation)
    if through_validate:
        console.print(
            "[bold]stopped at the validation gate[/bold] — read the report, "
            "then rerun with the same flags (upserts make this safe) to continue"
        )


db_app = typer.Typer(help="Database operations")
app.add_typer(db_app, name="db")


@db_app.command()
def migrate(
    target: Optional[str] = typer.Option(
        None, "--target", help="Single target: research or serving. Default: both."
    ),
) -> None:
    """Apply migrations. Targets both research and serving databases by default."""
    from capitalscan.jobs import db as db_ops

    db_ops.migrate(only=target)


@db_app.command()
def status(
    target: Optional[str] = typer.Option(
        None, "--target", help="Single target: research or serving. Default: both."
    ),
) -> None:
    """Show current migration revision per database."""
    from capitalscan.jobs import db as db_ops

    db_ops.status(only=target)


@db_app.command()
def rollback(
    target: Optional[str] = typer.Option(
        None, "--target", help="Single target: research or serving. Default: both."
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
) -> None:
    """Downgrade one migration level."""
    from capitalscan.jobs import db as db_ops

    if not yes:
        confirmed = typer.confirm("Downgrade one migration on the selected database(s)?")
        if not confirmed:
            raise typer.Abort()
    db_ops.rollback(only=target)


@db_app.command()
def schema() -> None:
    """Export research database schema to db/schema.sql via pg_dump."""
    from capitalscan.jobs import db as db_ops

    db_ops.schema()


path_app = typer.Typer(help="Forward path store (Session 10)")


@path_app.command("peak-labels")
def path_peak_labels_cmd(
    config_hash: Optional[str] = typer.Option(
        None, "--config-hash", help="Default: the resolved config's own hash"
    ),
) -> None:
    """Materialize `events.peak_ret_*d` from `path` (ADR 093 amendment).

    Idempotent and set-based: one UPDATE recomputes every event of one
    config from `path`, so an event whose forward window closed since the
    last run picks up its value and one still accumulating stays NULL
    rather than freezing at a partial maximum.

    Scoped to a single `config_hash`, defaulting to the resolved config's
    (user's decision, 2026-08-09): only the live hash is in use, and the
    superseded generations in `events` are kept as database history rather
    than as anything a query reads.
    """
    from capitalscan.jobs import db_io, ingest
    from capitalscan.jobs.config import config_hash as compute_config_hash
    from capitalscan.research.peak_labels import backfill_peak_labels

    config = _resolve_config_or_exit()
    chash = config_hash or compute_config_hash(config)
    engine = db_io.get_engine()

    with ingest.run_job(engine, "peak_labels", {"config_hash": chash}) as job:
        updated = backfill_peak_labels(engine, chash, config.stats.fwd_ret_horizons)
        job.rows_written = updated

    console.print(f"peak labels: config_hash={chash} rows_updated={updated:,}")


@path_app.command("backfill")
def path_backfill_cmd(
    quiet: bool = typer.Option(False, "--quiet", help="JSON-lines progress instead of a live bar"),
    workers: int = typer.Option(1, help="ProcessPoolExecutor workers; 1 runs serially"),
) -> None:
    """Populate `path` and `events.fwd_window_days` for every filled entry."""
    from capitalscan.jobs import db_io, ingest
    from capitalscan.research.path_backfill import run_path_backfill

    config = _resolve_config_or_exit()
    engine = db_io.get_engine()
    with ingest.run_job(engine, "path_backfill", {"workers": workers}) as job_report:
        report = run_path_backfill(
            engine, config, job_report.run_id, quiet=quiet, max_workers=workers
        )
        job_report.rows_written = report.rows_written
    console.print(
        f"path backfill: events_processed={report.events_processed} "
        f"skipped_unfilled={report.events_skipped_unfilled} "
        f"skipped_no_signal_bar={report.events_skipped_no_signal_bar} "
        f"rows_written={report.rows_written}"
    )
    if report.events_skipped_no_signal_bar:
        console.print(
            f"[yellow]{report.events_skipped_no_signal_bar} event(s) skipped: no `1d` bar "
            "yet for their signal_date (likely today's live events, before the EOD bars "
            "job has run). Rerun this command after bars catch up.[/yellow]"
        )


@path_app.command("capture")
def path_capture_cmd(
    quiet: bool = typer.Option(False, "--quiet", help="JSON-lines progress instead of a live bar"),
    workers: int = typer.Option(1, help="ProcessPoolExecutor workers; 1 runs serially"),
) -> None:
    """Task 10.6: append path rows for events with an incomplete forward
    window. Intended to run nightly, after `events` — cheap because it
    only touches tickers with at least one incomplete-window event, unlike
    `path backfill`'s full recompute.
    """
    from capitalscan.jobs import db_io, ingest
    from capitalscan.research.path_backfill import run_path_capture

    config = _resolve_config_or_exit()
    engine = db_io.get_engine()
    with ingest.run_job(engine, "path_capture", {"workers": workers}) as job_report:
        report = run_path_capture(
            engine, config, job_report.run_id, quiet=quiet, max_workers=workers
        )
        job_report.rows_written = report.rows_written
    console.print(
        f"path capture: events_processed={report.events_processed} "
        f"skipped_unfilled={report.events_skipped_unfilled} "
        f"skipped_no_signal_bar={report.events_skipped_no_signal_bar} "
        f"rows_written={report.rows_written}"
    )
    if report.events_skipped_no_signal_bar:
        console.print(
            f"[yellow]{report.events_skipped_no_signal_bar} event(s) skipped: no `1d` bar "
            "yet for their signal_date (likely today's live events, before the EOD bars "
            "job has run). Rerun this command after bars catch up.[/yellow]"
        )


@path_app.command("reconcile")
def path_reconcile_cmd(
    config_hash: str = typer.Option(
        ...,
        "--config-hash",
        help="events.config_hash to reconcile against (not run_id: see reconcile()'s docstring)",
    ),
) -> None:
    """Task 10.4: diff path-derived labels against Session 9's stored labels."""
    from capitalscan.jobs import db_io
    from capitalscan.research.path_reconcile import reconcile

    config = _resolve_config_or_exit()
    engine = db_io.get_engine()
    report = reconcile(engine, config, config_hash)
    console.print(f"reconcile: config_hash={config_hash} total_events={report.total_events}")
    for col, frame in report.mismatches.items():
        tag = "[yellow]explained[/yellow]" if col in report.explained else "[red]UNEXPLAINED[/red]"
        sample_ids = list(frame["event_id"].head(5))
        excluded_note = ""
        if col in report.recent_events_excluded:
            excluded_note = (
                f" ({report.recent_events_excluded[col]} more excluded: "
                "recent, bars still settling)"
            )
        if col in report.incomplete_window_excluded:
            excluded_note += (
                f" ({report.incomplete_window_excluded[col]} more excluded: "
                "forward window still accumulating)"
            )
        console.print(
            f"  {col}: {len(frame)} mismatches {tag} "
            f"(sample event_ids: {sample_ids}){excluded_note}"
        )
    # A column whose mismatches were *entirely* excluded is absent from
    # `report.mismatches` (the filters never leave an empty-but-present
    # key), so the loop above prints nothing for it. Printing the orphaned
    # counts here keeps the exclusions visible instead of letting a fully
    # excluded column read as a column with no mismatches at all.
    orphaned: dict[str, list[str]] = {}
    for label, counts in (
        ("recent, bars still settling", report.recent_events_excluded),
        ("forward window still accumulating", report.incomplete_window_excluded),
    ):
        for col, n in counts.items():
            if col not in report.mismatches:
                orphaned.setdefault(col, []).append(f"{n} excluded: {label}")
    for col, notes in orphaned.items():
        console.print(f"  {col}: 0 mismatches ({'; '.join(notes)})")
    console.print("[green]PASS[/green]" if report.passes else "[red]FAIL[/red]")
    if not report.passes:
        raise typer.Exit(code=1)


app.add_typer(path_app, name="path")


stats_app = typer.Typer(help="Statistics layer (Phase 4, Session 11)")


@stats_app.command("self-validate")
def stats_self_validate_cmd(
    seed: int = typer.Option(20260811, help="Base seed; replication r uses seed + 1000*r"),
    replications: int = typer.Option(10, help="Independent synthetic worlds"),
    check_broken: bool = typer.Option(
        True,
        help="Also run the deliberately-broken variant and confirm the null test catches it",
    ),
) -> None:
    """Session 11.4's gate: the null test and the recovery test (ADR 087).

    Reads nothing and writes nothing. Every number comes from seeded
    synthetic data with a known answer, so this is re-runnable at will and
    cannot touch production data.
    """
    from capitalscan.research.selfvalidation import (
        confirm_broken_variant_fails,
        run_null_test,
        run_recovery_test,
    )

    if check_broken:
        null, broken = confirm_broken_variant_fails(seed=seed, replications=replications)
    else:
        null, broken = run_null_test(seed=seed, replications=replications), None

    console.print(
        f"null test: {null.n_significant}/{null.n_tests} cells at q < {null.alpha} = "
        f"[bold]{null.rate:.2%}[/bold] (threshold {null.threshold:.0%}) "
        f"over {null.replications} replications"
    )
    console.print(
        f"  rho_bar={null.rho_bar:.4f} k_bar={null.mean_k_bar:.2f} "
        f"n={null.mean_n:.1f} n_eff={null.mean_n_eff:.1f} z_sd={null.z_sd:.3f} "
        f"min_p={null.min_p_value:.3g}"
    )
    console.print(
        "  per-replication rates: " + ", ".join(f"{r:.1%}" for r in null.rate_by_replication)
    )

    recovery = run_recovery_test(seed=seed + 1)
    console.print(
        f"recovery test: analytical={recovery.analytical:.4f} "
        f"measured={recovery.measured:.4f} gap=[bold]{recovery.gap_pct_points:.3f}[/bold] pp "
        f"(tolerance {recovery.tolerance_pct_points:.1f} pp, "
        f"{recovery.n_ticker_years} ticker-years)"
    )

    if broken is not None:
        caught = broken.rate > broken.threshold
        console.print(
            f"broken variant (SE on raw n): {broken.rate:.2%} at q < {broken.alpha} "
            f"z_sd={broken.z_sd:.3f} -> "
            + ("[green]caught[/green]" if caught else "[red]NOT CAUGHT[/red]")
        )

    # Report the state of the world before the verdict, never instead of it.
    ok = null.passed and recovery.passed and (broken is None or broken.rate > broken.threshold)
    console.print("[green]PASS[/green]" if ok else "[red]FAIL[/red]")
    if not ok:
        raise typer.Exit(code=1)


@stats_app.command("rho")
def stats_rho_cmd(
    config_hash: str = typer.Option(
        ...,
        "--config-hash",
        help="events.config_hash to measure. One rho_era row per era of that config",
    ),
) -> None:
    """Session 11.3: measure `rho_bar` per era and write `rho_era` (ADR 098).

    Session 12 cannot interpret a single `cell_stats` row without the
    `rho_era` row sharing its `config_hash` — `n_eff` is computed from the
    stored `rho_empirical`, so this is a prerequisite, not a report.

    Additive and re-runnable: the write upserts on `(era, config_hash)`, so
    a second config adds rows rather than replacing the first's, and a rerun
    against the same config refreshes its own rows and nothing else.
    """
    from capitalscan.jobs import db_io, ingest
    from capitalscan.jobs.provenance import git_sha
    from capitalscan.research.rho import run_rho_eras

    engine = db_io.get_engine()
    with ingest.run_job(engine, "rho", {"config_hash": config_hash}) as job:
        report = run_rho_eras(engine, config_hash, job.run_id, git_sha())
        job.rows_written = report.rows_written

    if report.n_events == 0:
        console.print(f"[red]no events for config_hash={config_hash}[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"rho: config_hash={config_hash} run_id={report.run_id} "
        f"events={report.n_events} tickers={report.n_tickers} "
        f"rows_written={report.rows_written}"
    )
    for e in report.estimates:
        if e.rho_empirical is None:
            console.print(f"  {e.era}: [yellow]no measurable co-firing, no row written[/yellow]")
            continue
        factor = "null" if e.rho_factor_implied is None else f"{e.rho_factor_implied:.4f}"
        gap = "null" if e.rho_gap is None else f"{e.rho_gap:+.4f}"
        beta = "null" if e.mean_beta is None else f"{e.mean_beta:.3f}"
        console.print(
            f"  {e.era}: rho_empirical={e.rho_empirical:.4f} rho_factor_implied={factor} "
            f"rho_gap={gap} n_pairs={e.n_pairs} n_cofire_days={e.n_cofire_days} "
            f"mean_beta={beta}"
        )
    # An era measured but not written leaves every cell_stats row for that
    # era uninterpretable, so it is called out rather than left to the
    # reader to notice a missing line.
    if report.skipped_eras:
        console.print(f"[yellow]eras written with no rho_empirical: {report.skipped_eras}[/yellow]")


@stats_app.command("cells")
def stats_cells_cmd(
    config_hash: str = typer.Option(
        ..., "--config-hash", help="events.config_hash to measure the headline grid against"
    ),
    split_key: str = typer.Option("train", help="train | validate | holdout"),
    write: bool = typer.Option(True, help="Write to cell_stats. --no-write measures only"),
) -> None:
    """Session 12's headline grid (DESIGN §6.7-§6.11, ADR 102).

    **Added 2026-08-13.** `research.cell_stats.run_cell_stats` shipped in
    Session 12 with no entry point and no caller outside its tests, so the
    published twelve-cell table was produced ad hoc and could not be
    reproduced by anyone reading the CLI. Found while re-measuring under a
    new `config_hash`.

    Requires `cscan stats rho --config-hash <same>` first: `n_eff` is
    computed from the stored `rho_empirical`, so this is a prerequisite
    rather than a report. A missing `rho_era` row makes every cell
    uninterpretable.

    Additive and re-runnable. The write upserts on `(cell_id, config_hash)`
    — ADR 096's composite key — so a second config adds rows rather than
    replacing the first's, and a rerun refreshes its own rows and nothing
    else.
    """
    from capitalscan.jobs import db_io, ingest
    from capitalscan.jobs.provenance import git_sha
    from capitalscan.research.cell_stats import run_cell_stats

    config = _resolve_config_or_exit()
    engine = db_io.get_engine()

    with ingest.run_job(
        engine, "cell_stats", {"config_hash": config_hash, "split_key": split_key}
    ) as job:
        rows, report = run_cell_stats(
            engine,
            config_hash,
            split_key,
            cfg=config,
            run_id=job.run_id,
            git_sha=git_sha(),
            write=write,
        )
        job.rows_written = report.n_written

    console.print(report.summary())
    if rows.empty:
        console.print("[red]no cells measured — check that events exist for this config[/red]")
        raise typer.Exit(code=1)


@stats_app.command("benchmarks")
def stats_benchmarks_cmd(
    config_hash: str = typer.Option(
        ..., "--config-hash", help="events.config_hash to measure the arms against"
    ),
    split_key: str = typer.Option("train", help="train | validate | holdout"),
    replications: Optional[int] = typer.Option(
        None,
        help="Random-entry replications. Defaults to StatsParams.n_replications_default (200). "
        "Use 50 during sweeps where ranking rather than significance is the goal (ADR 061)",
    ),
    write: bool = typer.Option(True, help="Write to benchmarks. --no-write measures only"),
) -> None:
    """Session 13: the eight benchmark arms (DESIGN §6.4-§6.6, ADR 012).

    Buy-and-hold, signal entry, a 200-replication random-entry null,
    trim-and-redeploy, four DCA variants, and ADR 099's high-breadth subset
    re-run of the three-arm comparison. All on one universe and one date
    range, which is the entire basis of the comparison (ADR 012).

    Additive and reversible: every row carries this invocation's `run_id`,
    and `DELETE FROM benchmarks WHERE run_id = '...'` reverses the run
    completely. Nothing else is written and no existing table is touched.

    The signal arm's position against the 97.5th percentile of the null is
    reported whichever way it falls. A gate that requires a favorable result
    is not a gate.
    """
    from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

    from capitalscan.jobs import db_io, ingest
    from capitalscan.jobs.provenance import git_sha
    from capitalscan.research.benchmarks import run_benchmarks

    config = _resolve_config_or_exit()
    engine = db_io.get_engine()
    n_reps = config.stats.n_replications_default if replications is None else replications

    # Two passes over the replications (pooled, then the high-breadth
    # subset), so the bar's total is doubled rather than resetting halfway.
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=console,
    ) as bar:
        task = bar.add_task("null replications", total=max(2 * n_reps, 1))

        def _tick(done: int, total: int) -> None:
            bar.update(task, advance=1)

        with ingest.run_job(
            engine,
            "benchmarks",
            {"config_hash": config_hash, "split_key": split_key, "replications": n_reps},
        ) as job:
            _rows, report = run_benchmarks(
                engine,
                config_hash,
                split_key,
                cfg=config,
                replications=n_reps,
                run_id=job.run_id,
                git_sha=git_sha(),
                write=write,
                progress=_tick,
            )
            job.rows_written = report.rows_written

    console.print(report.summary())
    for note in report.notes:
        console.print(f"  {note}")
    if report.signal_exceeds_null is None:
        console.print("[yellow]no stored null to test against[/yellow]")
    elif report.signal_exceeds_null:
        console.print("[green]signal arm is above the null's 97.5th percentile[/green]")
    else:
        console.print("[yellow]signal arm is at or below the null's 97.5th percentile[/yellow]")


app.add_typer(stats_app, name="stats")


positions_app = typer.Typer(help="Personal trade log (ADR 073)")
app.add_typer(positions_app, name="positions")


@positions_app.command("open")
def positions_open(
    ticker: str = typer.Option(...),
    side: str = typer.Option(..., help="long or short"),
    entry_date: str = typer.Option(..., help="YYYY-MM-DD"),
    entry_price: float = typer.Option(...),
    quantity: Optional[float] = typer.Option(None),
) -> None:
    """Declare a new open position."""
    from capitalscan.jobs import db_io
    from capitalscan.jobs import positions as positions_job

    row = positions_job.open_position(
        db_io.get_engine(),
        ticker.upper(),
        side,
        date.fromisoformat(entry_date),
        entry_price,
        quantity,
    )
    console.print(f"opened position {row['id']}: {ticker.upper()} {side} @ {entry_price}")


@positions_app.command("close")
def positions_close(
    id: int = typer.Option(...),
    exit_date: str = typer.Option(..., help="YYYY-MM-DD"),
    exit_price: float = typer.Option(...),
    reason: str = typer.Option(...),
) -> None:
    """Close an open position and record the realized return."""
    from capitalscan.jobs import db_io
    from capitalscan.jobs import positions as positions_job

    row = positions_job.close_position(
        db_io.get_engine(), id, date.fromisoformat(exit_date), exit_price, reason
    )
    console.print(f"closed position {id}: realized_ret={row['realized_ret']}")


@positions_app.command("list")
def positions_list(
    status: Optional[str] = typer.Option(None, help="Filter: open or closed"),
) -> None:
    """List positions."""
    from capitalscan.jobs import db_io
    from capitalscan.jobs import positions as positions_job

    result = positions_job.list_positions(db_io.get_engine(), status=status)
    if result.empty:
        console.print("[yellow]no positions[/yellow]")
        raise typer.Exit(code=0)
    console.print(result.to_string(index=False))


@app.command()
def nightly() -> None:
    """Orchestrates the nightly chain (DESIGN §4.12): bars, actions, market,
    shares, earnings-forward, indicators, events, path capture. `sync` is
    Phase 5 scope and stays unimplemented.
    """
    from capitalscan.jobs import compute, db_io, ingest, scheduled_runs
    from capitalscan.research.path_backfill import run_path_capture

    engine = db_io.get_engine()
    # Recorded before config resolution, not after: `scheduled_runs.record`
    # takes no config and does one cheap upsert (ADR 080), and it is what
    # `cscan status` reads to tell "Task Scheduler never fired" apart from
    # "nightly fired and died." If config resolution ran first and raised,
    # this run would never appear in `scheduled_runs` at all, and a config
    # failure would look identical to the scheduler never firing — two
    # different problems needing different fixes, made indistinguishable.
    # Config is still resolved before every `ingest.run_*`/`compute.run_*`
    # call below, so a bad config still aborts before any partial pipeline
    # runs — the property the original fix was for is unchanged.
    scheduled_runs.record(engine, "nightly")
    config = _resolve_config_or_exit()
    tickers = _resolve_tickers(None)
    end = date.today()
    start = end - timedelta(days=5)

    ingest.run_bars_daily(tickers, start, end, engine=engine)
    # BUILD.md §9.0: `run_bars_hourly`'s only other caller is the `bars` CLI
    # command, so without this the hourly table goes stale and
    # `core.returns.entry_price_for` returns NaN instead of raising,
    # silently dropping two of four entry kinds in the backtest. Same
    # 5-day `start` as the daily pull above, which is well inside Yahoo's
    # per-request window and yields one 60-day fetch window per ticker
    # rather than the 13 a full 730-day backfill walks (~21 min for ~630
    # tickers at RATE_LIMIT_PER_SEC = 0.5, vs. hours for a full backfill).
    ingest.run_bars_hourly(tickers, start, end, engine=engine)
    ingest.run_actions(tickers, engine=engine)
    ingest.run_market(lookback_days=5, engine=engine)
    ingest.run_shares(tickers, engine=engine)
    ingest.run_earnings(tickers, historical=False, forward_days=90, engine=engine)
    compute.run_indicators(
        tickers, start, end, params=config.indicators, max_workers=1, engine=engine
    )
    compute.run_events(tickers, start, end, config=config, engine=engine)
    # Task 10.6: must run after run_events — a signal fired tonight needs
    # its events row to exist before it can be selected as an
    # incomplete-window event to capture path rows for.
    #
    # Wrapped in `run_job` as of 2026-08-06: `path` now carries `run_id`
    # (ADR 034, migration a1f4c7d2e903) and `run_job` is what mints one.
    # The nightly capture previously wrote path rows outside any `runs`
    # row at all, so nightly-written rows were the one class of path row
    # with no recoverable origin even in principle.
    with ingest.run_job(engine, "path_capture", {"trigger": "nightly"}) as path_job:
        path_report = run_path_capture(engine, config, path_job.run_id, quiet=True)
        path_job.rows_written = path_report.rows_written
    # ADR 093's peak family, refreshed after path capture because it reads
    # what that step just wrote. Must run on a schedule, not only as a
    # one-off backfill: `peak_ret_*d` is NULL until an event's forward
    # window closes, so a column populated once and never again would be
    # permanently NULL for every event signalled after that run. That is
    # exactly how `events.giveback` ended up NULL on all 5.57M rows —
    # migration 699cb410d219 added the column and no writer ever ran.
    from capitalscan.jobs.config import config_hash as _compute_config_hash
    from capitalscan.research.peak_labels import backfill_peak_labels

    chash = _compute_config_hash(config)
    with ingest.run_job(engine, "peak_labels", {"trigger": "nightly", "config_hash": chash}) as pk:
        pk.rows_written = backfill_peak_labels(engine, chash, config.stats.fwd_ret_horizons)
    # Closes the slot `record` opened above. Without it the row stays
    # `'started'` forever and `cscan system-status` cannot tell a chain that
    # finished from one that died halfway (ADR 080 lists `status` and
    # `run_id` as part of this table's contract; both went unwritten until
    # 2026-08-09). `run_id` is the path-capture job's, the last link in the
    # chain, so a reader landing here can follow it into `runs`.
    scheduled_runs.complete(engine, "nightly", "ok", run_id=path_job.run_id)
    console.print("nightly: chain complete (sync --to-serving not yet implemented)")


@app.command()
def weekly(
    workers: int = typer.Option(8, help="ProcessPoolExecutor workers for the backtest refresh"),
) -> None:
    """Orchestrates the weekly chain (DESIGN §4.12): the backtest label
    refresh. `cell_stats` is Phase 4 scope and `sync` is Phase 5 scope; both
    stay unimplemented.

    Why the backtest belongs on a schedule rather than being run by hand.
    Event labels (`mfe`, `mae`, `touched_*pct`, `capture_ratio`,
    `fwd_ret_*d`) are written only by `run_backtest` through
    `research/enrich.py`. `nightly` runs `run_events` and `run_path_capture`,
    so new events get rows and `path` keeps growing, but an event whose
    forward window was still open when the backtest last ran keeps the labels
    frozen at that moment — permanently, with nothing in the system to
    correct it. Measured 2026-08-06: event 2775021 (CAT, signal_date
    2026-07-29) carried `touched_5pct = false` and `mfe = 0.042601` against a
    `path` whose `favorable` had since reached 0.153993.

    Reconciliation's exclusion filters (`research/path_reconcile.py`) hide
    this rather than fix it, and they hide it for exactly 45 days:
    `_drop_recent_events` measures its window from `date.today()`, so a stale
    event ages out of the exclusion while staying stale and
    `cscan path reconcile` starts failing on its own with no code change
    behind it. ADR 094 and DESIGN §9.4's schedule table (Sun 02:00, weekly
    backtest) both call for this refresh; only the wiring was missing.

    The Phase 3 validation harness is deliberately not run here. It is
    single-threaded and takes ~2h28m regardless of worker count (CLAUDE.md),
    it re-validates a detection/entry engine this refresh does not change,
    and a weekly job that runs for two and a half hours will be turned off.
    Run `cscan backtest` by hand when the engine itself changes; that path
    still runs the harness.
    """
    from dataclasses import asdict

    from capitalscan.jobs import db_io, ingest, scheduled_runs
    from capitalscan.jobs.config import config_hash as compute_config_hash
    from capitalscan.research.backtest import BacktestRunFailed, run_backtest

    engine = db_io.get_engine()
    # Same ordering rationale as `nightly`: record the slot before config
    # resolution so a config failure is distinguishable from the scheduler
    # never firing.
    scheduled_runs.record(engine, "weekly")
    config = _resolve_config_or_exit()
    chash = compute_config_hash(config)
    resolved = _resolve_tickers(None)

    run_params = {
        "config_hash": chash,
        "config": asdict(config),
        "full_universe": True,
        "workers": workers,
        "n_tickers": len(resolved),
        "trigger": "weekly",
    }

    try:
        with ingest.run_job(engine, "backtest", run_params) as report:
            bt_report = run_backtest(
                resolved,
                config,
                report.run_id,
                engine=engine,
                max_workers=workers,
                full_universe=True,
            )
            report.rows_written = bt_report.rows_written
            if bt_report.failed_tickers:
                failed = sorted(bt_report.failed_tickers)
                sample = ", ".join(failed[:10])
                more = "" if len(failed) <= 10 else f", +{len(failed) - 10} more"
                report.notes = f"{len(failed)}/{len(resolved)} ticker(s) failed: {sample}{more}"
    except BacktestRunFailed as exc:
        # Closed as `failed` before the exit, not left `'started'`: a chain
        # that raised is exactly the case `system-status` exists to surface,
        # and the old code path left no terminal state at all.
        scheduled_runs.complete(engine, "weekly", "failed")
        console.print(
            "[red]error[/red]: weekly backtest refresh failed — every dispatched "
            f"ticker's worker raised, which points at the config, not the data. {exc}"
        )
        raise typer.Exit(code=1) from None

    console.print(
        f"weekly: label refresh complete config_hash={chash} "
        f"run_id={bt_report.run_id} rows_written={bt_report.rows_written} "
        f"tickers={len(bt_report.tickers)}/{len(resolved)} "
        "(cell_stats is Phase 4 scope, sync is Phase 5 scope)"
    )
    if bt_report.failed_tickers:
        # Partial failure is still a failed slot: `run_backtest` wrote what
        # succeeded, but a reader asking "did the weekly refresh do its job"
        # should not be told yes.
        scheduled_runs.complete(engine, "weekly", "failed", run_id=bt_report.run_id)
        console.print(f"[red]{len(bt_report.failed_tickers)} ticker(s) failed[/red]")
        raise typer.Exit(code=1)
    scheduled_runs.complete(engine, "weekly", "ok", run_id=bt_report.run_id)


@app.command()
def monthly() -> None:
    """Orchestrates the monthly chain (DESIGN §4.12). Retrain/calibrate are
    Phase 6 scope and stay unimplemented — see `weekly`.
    """
    from capitalscan.jobs import db_io, scheduled_runs

    engine = db_io.get_engine()
    scheduled_runs.record(engine, "monthly")
    # Closed immediately and honestly: the slot fired and the chain has
    # nothing wired into it yet, which is a completed no-op rather than an
    # unfinished run. Leaving it `'started'` would make `system-status`
    # report a monthly job perpetually in flight.
    scheduled_runs.complete(engine, "monthly", "ok")
    console.print("monthly: no jobs wired yet (retrain/calibrate are Phase 6 scope)")


logs_app = typer.Typer(help="Logging utilities")
app.add_typer(logs_app, name="logs")


@logs_app.command()
def logs_tail(
    job: str = typer.Argument(..., help="Job name"),
    tail: int = typer.Option(50, help="Number of lines to tail"),
) -> None:
    """View recent logs for a job."""
    raise NotImplementedError("logs tail")


@app.command()
def verify_indicators(
    ticker: list[str] = typer.Option([], help="Ticker"),
    dates: Optional[str] = typer.Option(None, help="Comma-separated dates (YYYY-MM-DD)"),
) -> None:
    """Print computed indicators and write tests/golden/external_reference.csv (ADR 086)."""
    from datetime import datetime

    from capitalscan.jobs import verify

    if not ticker or not dates:
        console.print("[red]error[/red]: --ticker and --dates are required")
        raise typer.Exit(code=1)

    parsed_dates = [datetime.strptime(d.strip(), "%Y-%m-%d").date() for d in dates.split(",")]
    verify.run(tickers=ticker, dates=parsed_dates)


@app.command()
def system_status() -> None:
    """Show last run and staleness per job, schedule catch-up, and any
    interrupted runs (DESIGN §9.6, ADR 080, ADR 083).

    Read-only. Nothing here rewrites a `runs` row: an interrupted process
    is neither `ok` nor `failed`, and `runs_status_check` offers no third
    terminal value, so those rows are reported by age rather than
    relabelled (see `jobs/status.py`'s module docstring).

    Exits non-zero when any job is stale or any run failed, so a scheduled
    wrapper can act on it. Catch-up delay alone does not fail the command:
    ADR 080 treats a missed slot as the normal consequence of a workstation
    being off, which is the reason Task Scheduler's catch-up is enabled.
    """
    import pandas as pd
    from rich.table import Table

    from capitalscan.core.config import DEFAULT_MONITORING
    from capitalscan.jobs import db_io
    from capitalscan.jobs import status as job_status

    engine = db_io.get_engine()
    jobs = job_status.job_summary(engine, DEFAULT_MONITORING)
    schedule = job_status.schedule_summary(engine, DEFAULT_MONITORING)
    interrupted = job_status.stale_running(engine, DEFAULT_MONITORING)

    if jobs.empty:
        console.print("[yellow]no runs recorded[/yellow]")
        return

    table = Table(title="Jobs — last run", header_style="bold")
    for col in ("job", "status", "last seen", "age (days)", "rows"):
        table.add_column(col)
    for _, r in jobs.iterrows():
        age = f"{r['staleness_days']:.1f}"
        colour = "red" if r["is_stale"] else ("yellow" if r["status"] == "running" else "green")
        rows_written = "-" if pd.isna(r["rows_written"]) else f"{int(r['rows_written']):,}"
        table.add_row(
            r["job"],
            f"[{colour}]{r['status']}[/{colour}]",
            str(pd.to_datetime(r["last_seen"]).strftime("%Y-%m-%d %H:%M")),
            f"[{colour}]{age}[/{colour}]",
            rows_written,
        )
    console.print(table)

    if not schedule.empty:
        sched_table = Table(title="Schedule — latest slot per job", header_style="bold")
        for col in ("job", "scheduled for", "delay", "status", "run_id"):
            sched_table.add_column(col)
        for _, r in schedule.iterrows():
            delay = int(r["delay_seconds"] or 0)
            # ADR 080: past the threshold the machine was off, which is a
            # normal workstation condition, not a fault. Yellow, not red.
            marker = "[yellow]" if r["was_caught_up"] else "["
            suffix = " (caught up)" if r["was_caught_up"] else ""
            sched_table.add_row(
                r["job"],
                str(pd.to_datetime(r["scheduled_for"]).strftime("%Y-%m-%d %H:%M")),
                f"{marker}{delay // 60}m{suffix}[/]" if r["was_caught_up"] else f"{delay // 60}m",
                str(r["status"]),
                # `pd.isna`, not `or`: a float NaN is truthy, so `x or "-"`
                # renders the literal "nan" for the NULL `run_id` that
                # `record` leaves on nightly/weekly/monthly slots.
                "-" if pd.isna(r["run_id"]) else str(r["run_id"]),
            )
        console.print(sched_table)

    if not interrupted.empty:
        console.print(
            f"\n[yellow]{len(interrupted)} run(s) still marked 'running' past "
            f"{DEFAULT_MONITORING.stale_running_hours}h[/yellow] — a process that died before "
            "recording a terminal status (machine slept, Ctrl-C). Not rewritten: "
            "'failed' would assert something untrue."
        )
        for _, r in interrupted.iterrows():
            console.print(f"  {r['job']:<14} {r['run_id']}  open {r['open_hours']:.0f}h")

    stale_jobs = jobs.loc[jobs["is_stale"], "job"].tolist()
    failed_jobs = jobs.loc[jobs["status"] == "failed", "job"].tolist()
    if stale_jobs:
        console.print(
            f"\n[red]stale[/red] (>{DEFAULT_MONITORING.stale_after_days}d): {', '.join(stale_jobs)}"
        )
    if failed_jobs:
        console.print(f"[red]last run failed[/red]: {', '.join(failed_jobs)}")
    if stale_jobs or failed_jobs:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
