"""CapitalScan command-line interface."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()

app = typer.Typer(help="CapitalScan event-study engine")


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
    resolved = _resolve_tickers(tickers)
    end = date.today()
    start = end - timedelta(days=lookback)
    report = compute.run_indicators(resolved, start, end, max_workers=workers)
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
    resolved = [t.strip().upper() for t in tickers.split(",")] if tickers else None
    report = compute.run_universe(quarter, tickers=resolved)
    console.print(f"universe: evaluated {len(report.tickers)} tickers as of {quarter}")


@app.command()
def events(
    lookback: int = typer.Option(5, help="Days to look back"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
) -> None:
    """Detect signal events."""
    from capitalscan.jobs import compute

    resolved = _resolve_tickers(tickers)
    end = date.today()
    start = end - timedelta(days=lookback)
    report = compute.run_events(resolved, start, end)
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
    from sqlalchemy import text

    import pandas as pd

    start = date.fromisoformat(config.splits.ingest_start)
    indicator_cols_sql = ", ".join(_BACKTEST_INDICATOR_COLUMNS)
    out: dict = {}
    with engine.connect() as conn:
        for ticker in tickers:
            bars = pd.read_sql(
                text(
                    "SELECT ticker, ts, open, high, low, close, adj_close, volume "
                    "FROM bars WHERE ticker = :ticker AND interval = '1d' AND ts >= :start "
                    "ORDER BY ts"
                ),
                conn,
                params={"ticker": ticker, "start": start},
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
                params={"ticker": ticker, "start": start},
            )
            if indicators.empty:
                out[ticker] = bars
                continue
            indicators["ts"] = pd.to_datetime(indicators["ts"]).dt.tz_localize(None)
            out[ticker] = bars.merge(indicators, on="ts", how="left")
    return out


def _load_events_for_run(engine, run_id: str):
    """The `events` rows this run itself wrote — the harness's `events`
    argument. Scoped to `run_id`, not `config_hash`, so a rerun against the
    same default config does not pull in a previous run's rows alongside
    this one's (upsert means both share the same `(config_hash, ticker,
    signal_date, signal_type, entry_kind)` keys, but only the most recent
    write carries this run's `run_id`).
    """
    from sqlalchemy import text

    import pandas as pd

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

    `--sweep` implements only ADR 059's ordering gate (Task 11 scope). The
    18-config sweep itself is Task 12 — passing the gate here reports that
    and exits, rather than pretending to run something that does not exist
    yet.
    """
    from dataclasses import asdict

    from capitalscan.core.config import Config
    from capitalscan.jobs import db_io, ingest
    from capitalscan.jobs.config import config_hash as compute_config_hash
    from capitalscan.research.backtest import BacktestRunFailed, run_backtest
    from capitalscan.research.harness import run_harness

    if config_name is not None:
        console.print(
            "[red]error[/red]: --config-name is not implemented. There is no "
            "named-config registry — `jobs.config.resolve_config` resolves exactly "
            "one config per invocation from CLI/env/config.toml/dataclass defaults. "
            "Drop --config-name; the default config is used unconditionally."
        )
        raise typer.Exit(code=1)

    config = Config()
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
        console.print(
            "[yellow]note[/yellow]: the ADR 059 ordering gate passed, but the "
            "18-config sweep itself is Task 12 scope and is not implemented here."
        )
        raise typer.Exit(code=1)

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
                report.notes = (
                    f"{len(failed)}/{len(resolved)} ticker(s) failed: {sample}{more}"
                )
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
        console.print(f"[red]{len(bt_report.failed_tickers)} ticker(s) failed[/red]: {report.notes}")

    if bt_report.tickers:
        bars_by_ticker = _load_bars_by_ticker(engine, bt_report.tickers, config)
        events_for_harness = _load_events_for_run(engine, bt_report.run_id)
        harness_report = run_harness(events_for_harness, bars_by_ticker, config)
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
    "bb_upper": "Upper Bound",
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
) -> None:
    """Query detected events (ADR 049)."""
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

    csv_export = csv_export.rename(columns=COLUMN_LABELS)
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
    """Run live band-touch poller until market close (DESIGN §4.8)."""
    from capitalscan.jobs import poll as poll_job

    resolved = [t.strip().upper() for t in tickers.split(",")] if tickers else None
    report = poll_job.run_poll(interval=interval, tickers=resolved)
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
    shares, earnings-forward, indicators, events. `sync` is Phase 5 scope
    and stays unimplemented.
    """
    from capitalscan.jobs import compute, db_io, ingest, scheduled_runs

    engine = db_io.get_engine()
    scheduled_runs.record(engine, "nightly")
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
    compute.run_indicators(tickers, start, end, max_workers=1, engine=engine)
    compute.run_events(tickers, start, end, engine=engine)
    console.print("nightly: chain complete (sync --to-serving not yet implemented)")


@app.command()
def weekly() -> None:
    """Orchestrates the weekly chain (DESIGN §4.12). Backtest/cell_stats/sync
    are Phase 3/4/5 scope and stay unimplemented — this records the
    schedule slot now so ADR 080's catch-up tracking is in place before
    those jobs exist.
    """
    from capitalscan.jobs import db_io, scheduled_runs

    scheduled_runs.record(db_io.get_engine(), "weekly")
    console.print("weekly: no jobs wired yet (backtest/cell_stats are Phase 3-4 scope)")


@app.command()
def monthly() -> None:
    """Orchestrates the monthly chain (DESIGN §4.12). Retrain/calibrate are
    Phase 6 scope and stay unimplemented — see `weekly`.
    """
    from capitalscan.jobs import db_io, scheduled_runs

    scheduled_runs.record(db_io.get_engine(), "monthly")
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
    """Show system status and last run times."""
    raise NotImplementedError("system-status")


if __name__ == "__main__":
    app()
