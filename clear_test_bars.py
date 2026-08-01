#!/usr/bin/env python
"""Clear test bars to allow clean backfill."""

from sqlalchemy import text

from capitalscan.jobs import db_io

engine = db_io.get_engine()

with engine.begin() as conn:
    # Clear bars for test tickers
    conn.execute(text("DELETE FROM bars WHERE ticker IN ('AAPL', 'MSFT', 'GOOGL')"))
    print("Cleared AAPL, MSFT, GOOGL bars")

    result = conn.execute(text("SELECT COUNT(*) FROM bars"))
    count = result.scalar()
    print(f"Remaining bars: {count}")

    result = conn.execute(text("SELECT DISTINCT ticker FROM bars"))
    tickers = [row[0] for row in result]
    print(f"Remaining tickers: {tickers}")
