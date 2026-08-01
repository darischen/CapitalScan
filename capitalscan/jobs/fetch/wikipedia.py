"""Wikipedia fetcher: S&P 500 membership history (`membership`, BUILD §5.2).

This is a once-and-frozen job (DESIGN §4.1) — the result gets manually
reviewed and committed to `data/universe_union.csv` (ADR 055), not
re-fetched on a schedule. No rate limit is pinned for this source in
DESIGN §4.2's table; 1 req/s is a courtesy default, not a documented
constraint.
"""

from __future__ import annotations

from io import StringIO
from typing import cast

import pandas as pd
import requests

from capitalscan.jobs.fetch.base import cached, rate_limited, with_retry

RATE_LIMIT_PER_SEC = 1.0
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


@rate_limited(per_sec=RATE_LIMIT_PER_SEC)
@with_retry
def _get_tables(url: str) -> list[pd.DataFrame]:
    """Fetch `url` and parse every HTML table on the page.

    The body goes through `StringIO` rather than being handed to
    `read_html` as a bare string: pandas 3.0 dropped literal-HTML input,
    so a raw `resp.text` gets treated as a *filename*, and the resulting
    `OSError` carries the entire document as its message.
    """
    resp = requests.get(url, headers={"User-Agent": "capitalscan-research"}, timeout=30)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def _flatten_header(col: tuple) -> str:
    """Collapse one `MultiIndex` column label to a single name.

    Drops `Unnamed:` fillers left by `colspan` groups, and collapses a
    level repeated by a `rowspan` cell down to one occurrence.
    """
    parts: list[str] = []
    for level in col:
        text = str(level)
        if "Unnamed" in text:
            continue
        if parts and parts[-1] == text:
            continue
        parts.append(text)
    return "_".join(parts)


@cached(source="wikipedia_sp500", key_fn=lambda: "current_constituents")
def fetch_current_constituents() -> pd.DataFrame:
    """The first table on the page: today's S&P 500 membership."""
    tables = _get_tables(SP500_URL)
    df = cast(pd.DataFrame, tables[0].copy())
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return cast(pd.DataFrame, df.rename(columns={"symbol": "ticker"}))


@cached(source="wikipedia_sp500", key_fn=lambda: "membership_changes")
def fetch_membership_changes() -> pd.DataFrame:
    """The second table on the page: historical adds and removes.

    Wikipedia's "Selected changes to the list" table has a two-row header
    (Effective Date / Added ticker,security / Removed ticker,security /
    Reason), which `pandas.read_html` flattens into a `MultiIndex`;
    collapse it to single names here so downstream code sees plain
    columns.

    Two distinct header shapes have to collapse correctly. A `colspan`
    group ("Added" over "Ticker","Security") leaves an `Unnamed:` filler
    on one level, which is dropped. A `rowspan` cell ("Effective Date"
    covering both header rows) is repeated verbatim on *both* levels,
    which would otherwise join into `effective_date_effective_date`.
    """
    tables = _get_tables(SP500_URL)
    df = cast(pd.DataFrame, tables[1].copy())
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [_flatten_header(col) for col in df.columns]
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df
