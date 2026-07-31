"""External indicator verification (ADR 086).

Fetches real OHLCV via yfinance, runs it through `core.indicators.compute_all`,
prints the computed band and stochastic values, and writes
`tests/golden/external_reference.csv` with empty `external_*` columns for
the user to fill by hand from StockCharts or TradingView (~30 minutes,
once). This is the tool that catches the ddof and SMA-vs-EMA class of
silent divergence that every downstream number would otherwise inherit.

This module does IO (network fetch), so it lives in jobs/, not core/.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import cast

import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.table import Table

from capitalscan.core import indicators as ind
from capitalscan.core.config import IndicatorParams

console = Console()

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_CSV = REPO_ROOT / "capitalscan" / "tests" / "golden" / "external_reference.csv"

COMPUTED_COLUMNS = ["bb_upper", "bb_mid", "bb_lower", "k_full", "d_full"]
EXTERNAL_COLUMNS = [f"external_{c}" for c in COMPUTED_COLUMNS]


def _fetch_bars(ticker: str, through: date, lookback_days: int = 400) -> pd.DataFrame:
    start = through - timedelta(days=lookback_days)
    df = yf.download(
        ticker,
        start=start.isoformat(),
        end=(through + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        raise ValueError(f"no data returned for {ticker}")
    df.columns = df.columns.get_level_values(0)
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    )
    df.index.name = "ts"
    # yfinance ships no type stubs (see pyproject overrides), so df is Any
    # from here on; cast at the boundary rather than let Any leak upward.
    return cast(pd.DataFrame, df[["open", "high", "low", "close", "adj_close", "volume"]])


def run(tickers: list[str], dates: list[date]) -> None:
    """Compute indicators for each ticker and print rows for the given dates.

    Writes/refreshes tests/golden/external_reference.csv with one row per
    (ticker, date), computed columns filled in, external_* columns left
    empty for manual entry.
    """
    p = IndicatorParams()
    rows: list[dict] = []

    for ticker in tickers:
        through = max(dates)
        bars = _fetch_bars(ticker, through)
        out = ind.compute_all(bars, p)

        table = Table(title=f"{ticker} — computed indicators")
        table.add_column("date")
        for col in COMPUTED_COLUMNS:
            table.add_column(col)

        for d in dates:
            ts = pd.Timestamp(d)
            if ts not in out.index:
                console.print(f"[yellow]warning[/yellow]: {ticker} has no bar on {d}")
                continue
            row = out.loc[ts]
            cell_values = [f"{row[c]:.6f}" if pd.notna(row[c]) else "NaN" for c in COMPUTED_COLUMNS]
            table.add_row(str(d), *cell_values)
            rows.append(
                {"ticker": ticker, "date": d.isoformat(), **{c: row[c] for c in COMPUTED_COLUMNS}}
            )

        console.print(table)

    ref_df = pd.DataFrame(rows)
    for col in EXTERNAL_COLUMNS:
        ref_df[col] = ""
    REFERENCE_CSV.parent.mkdir(parents=True, exist_ok=True)
    ref_df.to_csv(REFERENCE_CSV, index=False)
    console.print(
        f"wrote {REFERENCE_CSV} — fill the external_* columns by hand, then run the agreement test"
    )
