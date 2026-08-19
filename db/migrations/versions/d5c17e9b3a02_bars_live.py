r"""bars_live: today's partial candle, kept out of `bars` on purpose

Revision ID: d5c17e9b3a02
Revises: b41f8ac9027e
Create Date: 2026-08-19 18:30:00.000000

ADR 128. The ticker chart stopped at yesterday's close, because `bars`
gains today's row only after the nightly ingest.

**The data was already being fetched and discarded twice.**
`fetch_quotes` calls Yahoo's batch quote endpoint with no field
restriction, so every response carries `regularMarketDayHigh`,
`regularMarketDayLow` and `regularMarketVolume`. `_QUOTE_COLUMNS` kept four
fields and dropped the rest at parse time; then `quotes_live` rows were
written only inside the breach loop, so only a ticker that fired got one at
all -- measured 2026-08-19, 128 rows across 86 tickers.

**Why a separate table rather than a partial row in `bars`.**
`cscan indicators` computes on whatever is in `bars`. A partial row would
get an indicator row, and `run_events`/`poll` read the latest indicator
strictly before the bar -- so today's *unfinished* indicators would become
tomorrow's t-1. That is the look-ahead failure invariant 3 exists to
prevent, and it is silent: every band tightens around a price that has not
finished happening and nothing raises.

The alternative, an `is_partial` flag on `bars`, needs eighteen readers to
carry a predicate and fails silently when one forgets. ADR 122 is the
cautionary case. A separate table needs no predicate anywhere.

**One row per ticker per session.** Yahoo's day high/low are cumulative
session extremes, so each tick overwrites with a better answer rather than
accumulating. A poller down for an hour still writes the correct high on
its next quote.

**Verify:**

    cscan db status
    psql -c "\d bars_live"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "d5c17e9b3a02"
down_revision: Union[str, Sequence[str], None] = "b41f8ac9027e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen per ADR 125: a migration carries its SQL, never imports the live
# constant.
_BARS_LIVE_AT_THIS_REVISION = """CREATE TABLE IF NOT EXISTS public.bars_live (
    ticker        text        NOT NULL REFERENCES public.tickers(ticker),
    session_date  date        NOT NULL,
    ts            timestamptz NOT NULL,
    open          numeric(12,4),
    high          numeric(12,4),
    low           numeric(12,4),
    close         numeric(12,4) NOT NULL,
    volume        bigint,
    run_id        text,
    PRIMARY KEY (ticker, session_date)
)
"""


def upgrade() -> None:
    op.execute(_BARS_LIVE_AT_THIS_REVISION)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.bars_live")
