"""CapitalScan command-line interface."""

from typing import Optional

import typer
from rich.console import Console

console = Console()

app = typer.Typer(help="CapitalScan event-study engine")


@app.command()
def calendar(
    through: int = typer.Option(2027, help="Last year to include"),
) -> None:
    """Populate NYSE trading calendar."""
    raise NotImplementedError("calendar")


@app.command()
def tickers(
    refresh: bool = typer.Option(False, help="Refresh ticker list from data source"),
) -> None:
    """Sync ticker reference data."""
    raise NotImplementedError("tickers")


@app.command()
def membership(
    backfill: bool = typer.Option(False, help="Backfill membership history"),
) -> None:
    """Build universe membership from S&P 500 history."""
    raise NotImplementedError("membership")


@app.command()
def bars(
    daily: bool = typer.Option(False, help="Fetch daily bars"),
    hourly: bool = typer.Option(False, help="Fetch hourly bars"),
    backfill: bool = typer.Option(False, help="Full hourly backfill"),
    lookback: int = typer.Option(5, help="Days to look back"),
) -> None:
    """Fetch OHLCV data."""
    raise NotImplementedError("bars")


@app.command()
def actions(
    lookback: int = typer.Option(30, help="Days to look back"),
) -> None:
    """Fetch corporate actions (splits, dividends)."""
    raise NotImplementedError("actions")


@app.command()
def market(
    lookback: int = typer.Option(5, help="Days to look back"),
) -> None:
    """Fetch market indices (SPX, VIX)."""
    raise NotImplementedError("market")


@app.command()
def shares(
    since_last: bool = typer.Option(False, help="Only fetch new filings"),
) -> None:
    """Fetch shares outstanding from SEC XBRL."""
    raise NotImplementedError("shares")


@app.command()
def earnings(
    historical: bool = typer.Option(False, help="Backfill historical earnings from SEC"),
    forward: int = typer.Option(0, help="Days forward to fetch from Finnhub"),
) -> None:
    """Fetch earnings dates."""
    raise NotImplementedError("earnings")


@app.command()
def indicators(
    lookback: int = typer.Option(5, help="Days to look back"),
    only: Optional[str] = typer.Option(None, help="Compute only this indicator"),
) -> None:
    """Compute technical indicators."""
    raise NotImplementedError("indicators")


@app.command()
def universe(
    quarter: Optional[str] = typer.Option(None, help="Quarter to evaluate (e.g. 2026Q3)"),
) -> None:
    """Evaluate universe membership criteria."""
    raise NotImplementedError("universe")


@app.command()
def events(
    lookback: int = typer.Option(5, help="Days to look back"),
) -> None:
    """Detect signal events."""
    raise NotImplementedError("events")


@app.command()
def scan(
    ticker: Optional[str] = typer.Option(None, help="Single ticker"),
    universe: str = typer.Option("trade", help="Universe: train or trade"),
    start: Optional[str] = typer.Option(None, help="Start date (YYYY-MM-DD)"),
    end: Optional[str] = typer.Option(None, help="End date (YYYY-MM-DD)"),
    date: Optional[str] = typer.Option(None, help="Specific date (YYYY-MM-DD)"),
) -> None:
    """Query detected events."""
    raise NotImplementedError("scan")


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
) -> None:
    """Validate ingested data."""
    raise NotImplementedError("validate")


@app.command()
def backfill(
    all: bool = typer.Option(False, help="Backfill all tickers"),
    tickers: Optional[str] = typer.Option(None, help="Comma-separated ticker list"),
    start: Optional[str] = typer.Option(None, help="Start date (YYYY-MM-DD)"),
    through_validate: bool = typer.Option(False, help="Stop at validation gate"),
    resume: bool = typer.Option(False, help="Resume from last run"),
) -> None:
    """Run the full backfill pipeline."""
    raise NotImplementedError("backfill")


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
