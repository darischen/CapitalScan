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
    tickers: Optional[str] = typer.Option(None, help="Restrict the Stooq cross-check sample"),
    strict: bool = typer.Option(
        False, help="Also fail when a check could not run at all, not just when data is bad"
    ),
) -> None:
    """Validate ingested data: reject counts, coverage, Stooq cross-check (DESIGN §5.8)."""
    from capitalscan.jobs import ingest

    resolved = [t.strip().upper() for t in tickers.split(",")] if tickers else None
    result = ingest.run_validate(tickers=resolved)
    if report:
        ingest.print_validation_report(result)
    else:
        status = "clean" if result.clean else "NOT clean"
        console.print(f"validation: {status}")

    # A vendor outage should not read the same as bad data. `--strict` is the
    # unattended-rerun gate (DESIGN §5.8) and demands both; without it a
    # non-zero exit means the data itself is bad.
    if not result.data_clean or (strict and not result.checks_complete):
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
    ingest.run_actions(tickers, engine=engine)
    ingest.run_market(lookback_days=5, engine=engine)
    ingest.run_shares(tickers, engine=engine)
    ingest.run_earnings(tickers, historical=False, forward_days=90, engine=engine)
    compute.run_indicators(tickers, start, end, engine=engine)
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
