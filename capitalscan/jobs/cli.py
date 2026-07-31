"""CapitalScan command-line interface."""

from datetime import date, timedelta
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
) -> None:
    """Build universe membership from S&P 500 history."""
    from capitalscan.jobs import ingest

    if not backfill:
        console.print("[yellow]nothing to do[/yellow]: pass --backfill")
        raise typer.Exit(code=1)
    report = ingest.run_membership()
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


@app.command()
def sync(
    to_serving: bool = typer.Option(False, help="Sync to serving database"),
) -> None:
    """Sync research database to serving database."""
    raise NotImplementedError("sync")


@app.command()
def poll(
    interval: int = typer.Option(300, help="Poll interval in seconds"),
) -> None:
    """Run live band-touch poller."""
    raise NotImplementedError("poll")


@app.command()
def validate(
    report: bool = typer.Option(False, help="Print validation report"),
    tickers: Optional[str] = typer.Option(None, help="Restrict the Stooq cross-check sample"),
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
