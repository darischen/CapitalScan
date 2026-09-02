"""Wikipedia fetcher: S&P 500 membership, current and historical.

**"Once-and-frozen" applies to the union file, not to these fetchers, and
conflating the two froze the live universe for 32 days.** ADR 055's
`data/universe_union.csv` is reviewed by hand and committed — that is the
frozen artifact. `fetch_current_constituents` is a different thing: its
only caller is `run_tickers_refresh`, and a refresh that cannot see a new
constituent is not a refresh.

Until 2026-09-01 both fetchers keyed their cache on a **constant string**,
so the first fetch answered every later one forever. Measured that day: the
cached snapshot was from 2026-07-31, still listed `FISV` (retired in 2023)
and did not contain `FI`, and `cscan tickers --refresh` had been replaying
it in under a second — which `CLAUDE.md` names as the signature of a cache
read. Worse, replaying it flipped `FISV` back to `is_active = true`.

Same defect class as `fetch_actions` (fixed 2026-08-26): a key that does
not capture *when* the question is asked, for data whose whole content is
"as of now". Both keys now carry the date. No rate limit is pinned for this source in
DESIGN §4.2's table; 1 req/s is a courtesy default, not a documented
constraint.
"""

from __future__ import annotations

from datetime import date
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


@cached(
    source="wikipedia_sp500_v2",
    key_fn=lambda: f"current_constituents_{date.today():%Y-%m-%d}",
)
def fetch_current_constituents() -> pd.DataFrame:
    """The first table on the page: today's S&P 500 membership.

    **The date is in the key, and `source` is `_v2`.** The date makes the
    entry expire; the version bump means no pre-`v2` entry can answer the
    new question even by accident. `CLAUDE.md` records why the second half
    matters: a cache hit is indistinguishable from a fetch except by
    duration, and it fails toward the old behaviour.

    One request per day. The membership of an index does not change more
    often than that, and a daily bucket keeps a same-day re-run free.
    """
    tables = _get_tables(SP500_URL)
    df = cast(pd.DataFrame, tables[0].copy())
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return cast(pd.DataFrame, df.rename(columns={"symbol": "ticker"}))


@cached(
    source="wikipedia_sp500_v2",
    key_fn=lambda: f"membership_changes_{date.today():%Y-%m-%d}",
)
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
