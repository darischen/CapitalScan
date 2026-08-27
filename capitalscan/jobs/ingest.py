"""Ingest jobs: raw data landing correctly, with validation that fails loudly.

DESIGN §2.3 (validation rules), §2.6 (idempotency), §4.3-§4.7 (job shapes),
§4.11 (backfill runbook). Every function here does IO — network fetch via
`jobs/fetch/*` and database writes via `jobs/db_io.py` — which is why none
of this lives in `core/` (invariant 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pandas_market_calendars as mcal
from rich.console import Console
from rich.progress import Progress
from rich.table import Table
from sqlalchemy import Engine, text

from capitalscan.core import sectors as core_sectors
from capitalscan.core import training as core_training
from capitalscan.core import universe as core_universe
from capitalscan.core.config import (
    DEFAULT_HOURLY_SPLIT_DETECTION,
    DEFAULT_HOURLY_SPLIT_GUARD,
    SharesPlausibility,
    SplitParams,
)
from capitalscan.jobs import db_io
from capitalscan.jobs.fetch import finnhub, sec, wikipedia, yahoo
from capitalscan.jobs.fetch.base import NotFoundError
from capitalscan.jobs.provenance import git_sha, new_run_id

console = Console()

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = REPO_ROOT / "data" / "universe_union.csv"

FULL_SESSION = pd.Timedelta(hours=6, minutes=30)
LARGE_MOVE_PCT = 0.40
SPLIT_RATIOS = (2, 3, 4, 5, 7, 10, 20)  # forward and reverse splits both checked
FLAT_CLOSE_RUN = 5
MIN_TRADE_PRICE = 1.0

# `run_shares`'s yfinance fallback (BUILD follow-up; see the comment on
# `fetch_shares_full` in `jobs/fetch/yahoo.py` for why `get_shares_full`
# rather than `Ticker.info["sharesOutstanding"]` is what backs it).
#
# SEC's `dei:EntityCommonStockSharesOutstanding` feed does not go stale
# gradually — a filer either reports it every 10-Q/10-K, or it vanishes
# from the concept entirely the quarter that filer starts reporting shares
# per class of stock (`_SHARES_TAG_SOURCES` in `fetch/sec.py`). One year
# covers every filer's slowest cadence (annual-only 10-K reporters), and
# the ~45-day 10-Q lag DESIGN §2.4 already tolerates buys one further
# quarter of grace on top before a gap counts as "the feed stopped," not
# "the next filing hasn't landed yet."
SHARES_STALENESS_DAYS = 365 + 90
# `get_shares_full` was verified live (BRK-B, MA, V, META) to return dated
# points back to ~2015 regardless of how far before that `start` asks for;
# 2015-01-01 costs nothing extra and is far enough back to backfill any
# ticker in the 629-name universe (all listed well after 2015).
SHARES_FULL_HISTORY_START = date(2015, 1, 1)
# Distinct from `"sec_xbrl"` so a query can exclude it — required reading
# for anyone tempted to trust this column the way they trust an SEC filing:
# these are Yahoo's own dated estimates, not filed facts, even though each
# row's `filed_on` is a genuine historical date rather than "today, applied
# backward" (see `fetch_shares_full`'s docstring).
SHARES_YAHOO_SOURCE = "yahoo_shares_full"

# ADR 156. A fund's share count is `netAssets / price`, and the source says
# so: the value is derived, not reported, and a reader must be able to tell
# which without inferring it from the ticker. Yahoo's `sharesOutstanding`
# for an ETF is one constant frozen at 2021-03-17 -- wrong rather than
# stale, because creation and redemption move the real count daily.
SHARES_NETASSETS_SOURCE = "yahoo_netassets"


@dataclass
class IngestReport:
    job: str
    run_id: str
    rows_written: int = 0
    rows_rejected: int = 0
    rows_flagged: int = 0
    tickers: list[str] = field(default_factory=list)
    notes: str | None = None


# ============ Run bookkeeping (invariant 6) ============


def _start_run(engine: Engine, job: str, params: dict) -> str:
    run_id = new_run_id(job)
    db_io.append(
        engine,
        "runs",
        [
            {
                "run_id": run_id,
                "job": job,
                "git_sha": git_sha(),
                "params": params,
                "started_at": datetime.now(UTC),
                "status": "running",
            }
        ],
    )
    return run_id


def _finish_run(
    engine: Engine, run_id: str, status: str, rows_written: int, notes: str | None
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE runs SET status = :status, rows_written = :rows_written, "
                "finished_at = :finished_at, notes = :notes WHERE run_id = :run_id"
            ),
            {
                "status": status,
                "rows_written": rows_written,
                "finished_at": datetime.now(UTC),
                "notes": notes,
                "run_id": run_id,
            },
        )


@contextmanager
def run_job(engine: Engine, job: str, params: dict) -> Iterator[IngestReport]:
    """Wraps a job body with a `runs` row: 'running' on entry, and exactly
    one of 'ok' / 'failed' / 'interrupted' on exit.

    **`BaseException`, not `Exception`.** `KeyboardInterrupt` and
    `SystemExit` derive from `BaseException`, so an `except Exception`
    clause does not see them: Ctrl-C propagated straight out of this
    context manager, `_finish_run` never ran, and the row stayed 'running'
    forever. Found 2026-08-16 with four such rows in the table, each
    carrying params identical to an 'ok' row seconds later — the same
    command, cancelled and immediately re-run.

    That is a false-positive source for anything reading `runs` to answer
    "is a job live right now": `cscan system-status` could not tell a
    running backtest from a Ctrl-C an hour earlier, and the only cure was
    a manual UPDATE.

    Cancellation is recorded as **'interrupted'**, not 'failed'. Migration
    `c5b81f2e64a7` added that status for exactly this state, and the
    distinction is load-bearing: 'failed' means the job hit a defect worth
    investigating, while 'interrupted' means an operator changed their mind
    and the partial state is expected. Collapsing them would bury real
    failures among routine cancellations.

    The bare `raise` is preserved in both branches, so Ctrl-C still
    terminates the process exactly as before. This changes what gets
    recorded, never what gets propagated.
    """
    run_id = _start_run(engine, job, params)
    report = IngestReport(job=job, run_id=run_id)
    try:
        yield report
    except BaseException as exc:
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        _finish_run(engine, run_id, status, report.rows_written, str(exc) or type(exc).__name__)
        raise
    _finish_run(engine, run_id, "ok", report.rows_written, report.notes)


# ============ calendar ============


def run_calendar(through_year: int, engine: Engine | None = None) -> IngestReport:
    engine = engine or db_io.get_engine()
    with run_job(engine, "calendar", {"through": through_year}) as report:
        cal = mcal.get_calendar("NYSE")
        schedule = cal.schedule(
            start_date=SplitParams().ingest_start, end_date=f"{through_year}-12-31"
        )
        session_len = schedule["market_close"] - schedule["market_open"]
        rows = pd.DataFrame(
            {
                "d": pd.DatetimeIndex(schedule.index).date,
                "is_early_close": (session_len < FULL_SESSION).to_numpy(),
            }
        )
        report.rows_written = db_io.upsert(engine, "trading_days", rows, ["d"])
    return report


# ============ tickers ============


def ensure_tickers(tickers: list[str], engine: Engine | None = None) -> IngestReport:
    """Minimal stub rows so `bars` / `corporate_actions` FKs are satisfiable
    for an ad-hoc `--tickers` backfill, independent of the curated Wikipedia
    refresh below.
    """
    engine = engine or db_io.get_engine()
    with run_job(engine, "tickers_ensure", {"tickers": tickers}) as report:
        rows = [{"ticker": t.upper(), "is_active": True} for t in tickers]
        report.rows_written = db_io.upsert(engine, "tickers", rows, ["ticker"])
        report.tickers = [str(r["ticker"]) for r in rows]
    return report


def run_tickers_refresh(engine: Engine | None = None) -> IngestReport:
    """Full universe refresh from Wikipedia + SEC CIK lookup (DESIGN §4.1 `tickers`)."""
    engine = engine or db_io.get_engine()
    with run_job(engine, "tickers", {"refresh": True}) as report:
        constituents = wikipedia.fetch_current_constituents()
        cik_lookup = sec.fetch_cik_lookup().set_index("ticker")["cik"]
        rows = []
        for _, row in constituents.iterrows():
            ticker = str(row["ticker"]).upper().replace(".", "-")
            rows.append(
                {
                    "ticker": ticker,
                    "cik": str(cik_lookup.get(ticker)) if ticker in cik_lookup.index else None,
                    "name": row.get("security"),
                    "sector": row.get("gics_sector"),
                    "industry": row.get("gics_sub-industry"),
                    "is_active": True,
                }
            )
        report.rows_written = db_io.upsert(engine, "tickers", rows, ["ticker"])
        report.tickers = [str(r["ticker"]) for r in rows]
    return report


# ============ membership ============


def drop_pre_window_tickers(
    union_df: pd.DataFrame, event_start: str
) -> tuple[pd.DataFrame, list[str]]:
    """Drop tickers whose entire index tenure ends before `event_start`.

    ADR 035 keeps delisted and removed names on purpose — the union is
    what removes survivorship bias, and ADR 015 depends on it. This is
    **not** that cut. A name that left the index before the event window
    opens contributes no events to the study, so dropping it costs no
    bias; a name that left during the window stays, delisted or not.

    Conservative by construction. A ticker goes only when all three hold:
    it is not a current member, its removal was actually observed, and
    none of its dates reach `event_start`. A ticker added in 2005 with no
    recorded removal stays, because Wikipedia's table covers *selected*
    changes and its exit may simply never have been logged. Dates that
    fail to parse count as reaching the window for the same reason — ADR
    055 expects inconsistent formatting in older rows, and a parse
    failure is a signal to review, not to delete.

    Returns the filtered frame and the sorted list of dropped tickers.
    """
    cutoff = pd.Timestamp(event_start)
    current = set(union_df.loc[~union_df["needs_review"], "ticker"])

    dropped: list[str] = []
    for ticker, group in union_df.groupby("ticker", sort=False):
        if ticker in current:
            continue
        if group["removed"].isna().all():
            continue
        stamps = pd.to_datetime(
            pd.concat([group["added"], group["removed"]]).dropna(),
            format="mixed",
            errors="coerce",
        )
        if stamps.isna().any() or stamps.empty:
            continue
        if stamps.max() < cutoff:
            dropped.append(str(ticker))

    kept = union_df.loc[~union_df["ticker"].isin(dropped)]
    return kept.reset_index(drop=True), sorted(dropped)


class UniverseFrozenError(Exception):
    """`universe_union.csv` has been reviewed and signed off (ADR 055).

    Regenerating would reset every `needs_review` flag and discard the
    manual pass, so the job refuses unless explicitly forced.
    """


def is_reviewed(path: Path) -> bool:
    """Whether `path` is a signed-off universe file: present, non-empty, no pending rows.

    Every uncertain case answers `False` — a missing file, a missing
    `needs_review` column, an unreadable file. The guard should only ever
    block on positive proof that a review happened, otherwise a malformed
    file would lock the job out of a legitimate first run.
    """
    if not path.exists():
        return False
    try:
        df = pd.read_csv(path)
    except Exception:
        return False
    if df.empty or "needs_review" not in df.columns:
        return False
    return not df["needs_review"].astype(bool).any()


def run_membership(force: bool = False) -> IngestReport:
    """Builds `data/universe_union.csv` (ADR 055): once, then reviewed by hand.

    Writes no database rows — membership history is a reviewable file
    artifact, not a table, so composition changes show up as a diff rather
    than silently when the source page edits.

    Refuses to overwrite a file whose rows are all signed off. ADR 055
    calls this artifact frozen, and every regeneration re-flags each
    change row as `needs_review`, so an accidental re-run would throw
    away the review pass with no warning. `force=True` regenerates
    anyway. The check runs before the scrape so a refusal costs no
    network call and leaves the fetch cache untouched.
    """
    if not force and is_reviewed(UNIVERSE_CSV):
        raise UniverseFrozenError(
            f"{UNIVERSE_CSV} is reviewed and frozen (ADR 055); regenerating would "
            "reset every needs_review flag and discard that pass. Pass --force to "
            "regenerate anyway."
        )

    constituents = wikipedia.fetch_current_constituents()
    changes = wikipedia.fetch_membership_changes()

    rows = [
        {
            "ticker": str(t).upper().replace(".", "-"),
            "added": None,
            "removed": None,
            "needs_review": False,
        }
        for t in constituents["ticker"]
    ]
    # Wikipedia labels this column "Effective Date", not "Date", and has
    # renamed it before. Resolve it by suffix so a relabel shows up as a
    # hard failure here rather than as a CSV full of empty dates.
    date_cols = [c for c in changes.columns if c.endswith("date")]
    if len(date_cols) != 1:
        raise ValueError(
            f"membership changes table: expected exactly one date column, "
            f"found {date_cols} in {list(changes.columns)}"
        )
    date_col = date_cols[0]

    for _, row in changes.iterrows():
        for col, key in (("removed_ticker", "removed"), ("added_ticker", "added")):
            if col not in changes.columns:
                continue
            ticker = row.get(col)
            if not ticker or pd.isna(ticker):
                continue
            rows.append(
                {
                    "ticker": str(ticker).upper().replace(".", "-"),
                    "added": row.get(date_col) if key == "added" else None,
                    "removed": row.get(date_col) if key == "removed" else None,
                    "needs_review": True,
                }
            )

    union_df = pd.DataFrame(rows).drop_duplicates()
    union_df, dropped = drop_pre_window_tickers(union_df, SplitParams().event_start)

    UNIVERSE_CSV.parent.mkdir(parents=True, exist_ok=True)
    union_df.to_csv(UNIVERSE_CSV, index=False)
    return IngestReport(
        job="membership",
        run_id=new_run_id("membership"),
        rows_written=len(union_df),
        notes=(
            f"wrote {UNIVERSE_CSV} — {union_df['ticker'].nunique()} tickers, "
            f"{int(union_df['needs_review'].sum())} rows need manual review "
            f"before this is frozen (ADR 055); dropped {len(dropped)} tickers "
            "whose tenure ended before the event window opened"
        ),
    )


# ============ bars_daily validation (DESIGN §2.3) ============


def _looks_like_unexplained_split(pct_change: float, ex_dates: set) -> bool:
    factor = 1 + pct_change
    for ratio in SPLIT_RATIOS:
        if abs(factor - ratio) < 0.02 or abs(factor - 1 / ratio) < 0.005:
            return True
    return False


def validate_bars(
    df: pd.DataFrame, corporate_actions: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, list[dict]]:
    """Apply DESIGN §2.3's per-batch checks. Returns (clean rows, reject rows).

    Rules that need broader context than one fetch batch — missing bars on
    a trading day — live in `run_validate` instead, which has the full
    `trading_days` and `bars` history to work against.
    """
    if df.empty:
        return df, []

    df = df.sort_values(["ticker", "ts"]).reset_index(drop=True)
    split_dates: dict[str, set] = {}
    if corporate_actions is not None and not corporate_actions.empty:
        splits = corporate_actions.loc[corporate_actions["action_type"] == "split"]
        for ticker, group in splits.groupby("ticker"):
            split_dates[str(ticker)] = set(pd.to_datetime(group["ex_date"]).dt.date)

    rejects: list[dict] = []
    flags: list[dict] = []
    keep = pd.Series(True, index=df.index)

    # `idx` is whatever `iterrows()`/`items()`/`groupby()` hand back — always
    # an int row-label here since `df` was just `reset_index(drop=True)`, but
    # typed loosely (`Any`) because pandas-stubs reports it as `Hashable`.
    def reject(idx: Any, rule: str, payload: dict) -> None:
        keep.at[idx] = False
        rejects.append(
            {
                "ticker": df.at[idx, "ticker"],
                "ts": df.at[idx, "ts"],
                "rule": rule,
                "severity": "reject",
                "payload": payload,
            }
        )

    def flag(idx: Any, rule: str, payload: dict) -> None:
        flags.append(
            {
                "ticker": df.at[idx, "ticker"],
                "ts": df.at[idx, "ts"],
                "rule": rule,
                "severity": "flag",
                "payload": payload,
            }
        )

    for idx, row in df.iterrows():
        if row["high"] < row["low"]:
            reject(idx, "high_lt_low", {"high": row["high"], "low": row["low"]})
            continue
        if not (row["low"] <= row["close"] <= row["high"]):
            reject(
                idx,
                "close_outside_range",
                {"close": row["close"], "low": row["low"], "high": row["high"]},
            )
            continue
        if not (row["low"] <= row["open"] <= row["high"]):
            reject(
                idx,
                "open_outside_range",
                {"open": row["open"], "low": row["low"], "high": row["high"]},
            )
            continue
        if not row["volume"] or pd.isna(row["volume"]):
            flag(idx, "zero_or_null_volume", {"volume": row.get("volume")})
        if row["close"] < MIN_TRADE_PRICE:
            flag(idx, "price_below_min", {"close": row["close"]})

    for ticker, group in df.groupby("ticker"):
        prior_close = group["close"].shift(1)
        pct_change = group["close"] / prior_close - 1
        for idx, chg in pct_change.items():
            if pd.isna(chg) or abs(chg) <= LARGE_MOVE_PCT or not keep.at[idx]:
                continue
            ex_dates = split_dates.get(str(ticker), set())
            row_date = pd.Timestamp(df.at[idx, "ts"]).date()  # type: ignore[arg-type]
            explained = row_date in ex_dates or (row_date - timedelta(days=1)) in ex_dates
            if explained:
                flag(idx, "large_return_explained_by_split", {"pct_change": chg})
            elif _looks_like_unexplained_split(chg, ex_dates):
                reject(idx, "unexplained_split_like_move", {"pct_change": chg})
                keep.at[idx] = False
            else:
                flag(idx, "large_unexplained_return", {"pct_change": chg})

        # **Zero volume AND an unchanged close is filler, not a session.**
        # Found 2026-08-27: AVB held four bars at exactly 65.9005 with
        # volume 0 across 2026-08-17..20, spanning its 2.793 split, while
        # the vendor now serves four distinct real closes for those days.
        #
        # Both signals already existed as flags and both fired; neither
        # clears `keep`, so the bars were stored and the indicators read a
        # fake flat line straight through a split. That is deliberate for
        # each signal alone -- CCZ, GJS and SBNY have real no-trade
        # sessions, and a flat close on real volume is a quiet day -- but
        # the conjunction carries no observation at all: no trades, and a
        # price copied from yesterday. Invariant 4 drops an absent value
        # and logs it rather than carrying it forward, so this rejects.
        #
        # Compared against the prior row *within this ticker's group*, so
        # the first bar of a batch is never rejected (nothing to compare)
        # and one ticker cannot be measured against another.
        prior_in_group = group["close"].shift(1)
        for idx, row in group.iterrows():
            if not keep.at[idx]:
                continue
            prior = prior_in_group.at[idx]
            no_volume = not row["volume"] or pd.isna(row["volume"])
            if no_volume and pd.notna(prior) and row["close"] == prior:
                reject(
                    idx,
                    "flat_zero_volume_bar",
                    {"close": row["close"], "prior_close": prior, "volume": row["volume"]},
                )

        flat_run = (group["close"] == group["close"].shift(1)).astype(int)
        run_len = flat_run.groupby((flat_run != flat_run.shift()).cumsum()).cumsum()
        for idx, length in run_len.items():
            if length >= FLAT_CLOSE_RUN - 1 and flat_run.at[idx]:
                flag(idx, "identical_close_run", {"run_length": int(length) + 1})

    clean = df.loc[keep].reset_index(drop=True)
    return clean, rejects + flags


def unresolved_rejects(
    rejects: pd.DataFrame, bar_dates: pd.DataFrame, ingest_start: date
) -> pd.DataFrame:
    """Hard rejects whose bar is still absent — the ones that still cost data.

    `bar_rejects` is append-only: it is an audit trail, not a worklist
    (invariant 6). A bar rejected by one run stays recorded even after a
    later run fetches it correctly and upserts it, so counting every row
    ever written means a single transient rejection makes validation
    permanently dirty with no path back to clean.

    Naively counting every logged reject would make `clean` measure
    something other than what it claims. Here the claim is "no bar was
    dropped for failing validation", and a reject whose bar is now on file
    did not drop anything.

    Measured when this was written: 10 of 12 outstanding hard rejects had
    correct bars on file. The pattern is a batch fetched across a split
    boundary, which shows an artificial discontinuity — ANET 2024-10-07 read
    as -75% — and a later batch fetched wholly after the split landing it
    smoothly at 98.14 between neighbours of 98.99 and 100.06.

    Rejects before `ingest_start` are excluded: their bars are absent
    because the window trim deleted them deliberately, not because a fetch
    failed. Counting those would make the trim itself unfixable.

    The audit trail is untouched — `reject_counts` still reports every row.
    """
    if rejects.empty:
        return rejects.iloc[:0]

    hard = rejects.loc[rejects["severity"] == "reject"].copy()
    hard = hard.loc[hard["d"] >= ingest_start]
    if hard.empty:
        return hard

    on_file = set(zip(bar_dates["ticker"], bar_dates["d"], strict=False))
    still_missing = [(t, d) not in on_file for t, d in zip(hard["ticker"], hard["d"], strict=False)]
    return hard.loc[still_missing].reset_index(drop=True)


def find_missing_bars(bars: pd.DataFrame, trading_days: pd.DataFrame) -> pd.DataFrame:
    """Trading days with no bar, bounded to each ticker's own observed span.

    DESIGN §2.3 lists "missing bar on an NYSE trading day" with threshold
    "any" — no tolerance fraction here. That is only achievable, per
    DESIGN §4.3's silent-truncation warning, if the calendar checked against
    is bounded to the ticker's own `[min(d), max(d)]` rather than the whole `trading_days`
    table: a ticker that listed in 2014 has no bars for 2010-2013 and a
    ticker delisted in 2019 has none after, and neither is a gap — it is
    exactly the expected absence DESIGN §4.3 says `first_bar`/`last_bar`
    exist to describe. Comparing against the full calendar regardless of
    a ticker's life would flag most of the universe permanently and make
    the check impossible to ever pass; bounding by the ticker's own first
    and last observed bar is what keeps "any" a real, checkable threshold.

    `bars` needs columns `ticker`, `d` (a bar date, one row per bar).
    `trading_days` needs a `d` column (the full NYSE calendar). Returns one
    row per (ticker, missing_date).
    """
    if bars.empty:
        return pd.DataFrame(columns=["ticker", "missing_date"])

    all_days = pd.DatetimeIndex(pd.to_datetime(trading_days["d"])).normalize()
    gaps: list[dict] = []
    for ticker, group in bars.groupby("ticker"):
        present = set(pd.to_datetime(group["d"]).dt.normalize())
        first, last = min(present), max(present)
        expected = all_days[(all_days >= first) & (all_days <= last)]
        for d in sorted(set(expected) - present):
            gaps.append({"ticker": ticker, "missing_date": d.date()})
    return pd.DataFrame(gaps, columns=["ticker", "missing_date"])


# ============ daily split back-adjustment ============

# Below this distance from 1.0 a split cannot be told from ordinary daily
# noise by comparing closes across the ex-date. A 1.008 split (SCCO) moves
# price 0.8%; single sessions move more than that constantly. CLAUDE.md
# records the same limitation biting `_split_adjustment_factor`, and a
# first pass at the detection query below reported SCCO, HON, SPGI, CMCSA,
# FNF, UL and CBSH as unadjusted purely because `ratio * 0.85 .. ratio *
# 1.15` brackets 1.0 when the ratio is near 1.0. Those were all false.
MIN_DETECTABLE_SPLIT_DISTANCE = 0.15

# How close the observed jump must sit to the ratio (or to 1.0) to call it.
SPLIT_STATE_TOLERANCE = 0.08


def daily_split_state(before_close: float, after_close: float, ratio: float) -> str:
    """Has the vendor already back-adjusted this ticker's daily history?

    Returns `"unadjusted"`, `"adjusted"` or `"unresolved"`.

    **Why this exists.** `run_bars_daily` assumed the vendor returns
    split-adjusted history. On 2026-08-27 that assumption failed in both
    directions at once:

    - **IESC** split 2-for-1 on 2026-08-24. Yahoo recorded the event on a
      row with `Close = NaN` and `Volume = 0` and never propagated it
      backward. A fresh fetch still returns 685.04 on 08-21 against 313.23
      on 08-25, so re-fetching does **not** fix it -- the vendor's own
      history is unadjusted and our copy faithfully mirrored it.
    - **AVB** split 2.793-for-1 on 2026-08-17. Yahoo *did* adjust, but our
      stored history predated the adjustment and nothing re-read it.

    One assumption, two opposite failures, and neither raised anything.
    Every split through MNST on 2026-08-11 was fine, so the corruption
    window is "any split after the last full re-fetch".

    **The comparison.** `corporate_actions.ratio` is `new_shares /
    old_shares` (10 for KLAC's 10-for-1, 0.2 for AMCR's 1-for-5), the same
    convention `_split_adjustment_factor` documents against the live table.
    Dividing pre-split prices by `ratio` is the adjustment, so an
    *unadjusted* series shows `before / after ~= ratio` and an adjusted one
    shows `before / after ~= 1`.

    **`unresolved` is a real answer, not a failure.** When `ratio` is within
    `MIN_DETECTABLE_SPLIT_DISTANCE` of 1.0 the two hypotheses are not
    separable from daily noise, and guessing picks a wrong one about as
    often as a right one. Silently adjusting on a coin flip would corrupt a
    correct series, which is worse than leaving a known-suspect one alone.
    """
    if before_close is None or after_close is None:
        return "unresolved"
    if not (before_close > 0 and after_close > 0):
        return "unresolved"
    if ratio is None or ratio <= 0 or abs(ratio - 1.0) < MIN_DETECTABLE_SPLIT_DISTANCE:
        return "unresolved"

    observed = before_close / after_close
    to_ratio = abs(observed - ratio) / ratio
    to_one = abs(observed - 1.0)
    if to_ratio <= SPLIT_STATE_TOLERANCE and to_ratio < to_one:
        return "unadjusted"
    if to_one <= SPLIT_STATE_TOLERANCE and to_one < to_ratio:
        return "adjusted"
    return "unresolved"


def back_adjust_daily(frame: pd.DataFrame, ex_date: date, ratio: float) -> pd.DataFrame:
    """Divide price columns strictly before `ex_date` by `ratio`.

    Never mutates: returns a new frame (conventions). Prices are rounded to
    4dp, matching the storage precision every comparison already assumes.

    **`volume` is multiplied, not divided.** A 2-for-1 split doubles the
    share count, so a pre-split volume of 100,000 old shares is 200,000
    new ones. Getting this backwards leaves the price series right and
    every volume-derived figure wrong by `ratio` squared.

    Strictly `<` the ex-date: the ex-date bar itself already trades on the
    new share count.
    """
    out = frame.copy()
    if out.empty or ratio is None or ratio <= 0:
        return out
    mask = pd.to_datetime(out["ts"]).dt.date < ex_date
    if not mask.any():
        return out
    for col in ("open", "high", "low", "close", "adj_close"):
        if col in out.columns:
            out.loc[mask, col] = (out.loc[mask, col] / ratio).round(4)
    if "volume" in out.columns:
        out.loc[mask, "volume"] = (out.loc[mask, "volume"] * ratio).round()
    return out


# ============ hourly split back-adjustment (Session 9 hourly-split-adjust) ============


def _split_adjustment_factor(
    raw: pd.DataFrame, splits: pd.DataFrame, daily_range: pd.DataFrame
) -> tuple[pd.Series, set]:
    """Cumulative back-adjustment factor for one ticker's hourly bars, plus
    the set of (day) values where the vendor's adjustment state could not
    be resolved (see "Unresolved days" below).

    `corporate_actions.ratio` is `new_shares / old_shares`: 10 for KLAC's
    2026-06-12 10-for-1 split, 0.2 for AMCR's 2026-01-15 1-for-5 reverse
    split — verified directly against the live table (`SELECT ticker,
    ex_date, ratio FROM corporate_actions WHERE ticker = 'KLAC'` returns
    `ratio = 10` for that row). Dividing pre-split OHLC by `ratio` is
    exactly Yahoo's own back-adjustment, so this reproduces what the daily
    endpoint already does upstream — *when* the raw hourly fetch is
    genuinely unadjusted, which the detection below no longer assumes
    unconditionally.

    For a bar at time `t`, the **naive** factor is the product of `ratio`
    over every split whose `ex_date > t.date()` — a bar on or after the
    ex-date is already on the post-split share count and needs no
    adjustment for that split. Multiple splits inside the window compound:
    each is applied independently, so a bar before two splits gets divided
    by both ratios.

    A ticker with no splits (or a splits frame already filtered empty)
    returns factor 1.0 for every row and no unresolved days — never
    fabricated. A null or non-positive `ratio` is dropped rather than
    applied, per invariant 4: a missing ratio must not become a guess or a
    divide-by-zero.

    **Session 9 hourly-residual fix (2026-08-02), and the follow-up
    correction the same day.** The premise "Yahoo's hourly endpoint never
    back-adjusts" is false for a window of sessions immediately before
    certain splits' `ex_date` — diagnosed against the live database
    (`hourly-residual-diagnosis.md`): 1,880 bars across 15 tickers where
    dividing by the naive factor over-corrected by exactly the split
    ratio, because the raw hourly fetch for those specific days was
    *already* split-adjusted by the vendor.

    The first attempt at a fix asked one question per pending day: "is the
    raw aggregate within `range_escape_tolerance` (0.50) of daily?" — that
    fails for any split whose ratio itself sits near 1.0. `corporate_actions`
    measured live has 11 of 33 splits in the hourly window with `ratio`
    between 0.667 and 1.5: for those, an *unadjusted* day's ratio-to-daily
    (~1.2, say) is itself inside a 0.50 band around 1.0, so the first
    version misread "not yet adjusted" as "already adjusted" for exactly
    the splits it needed to get right.

    **The corrected test.** Per (ticker, day) with a pending naive factor
    `F != 1.0`, aggregate the raw (pre-adjustment) hourly `high` for the
    day (`day_high`) and the matching daily bar's `high` (`daily_high`) —
    daily is the reliable reference, since Yahoo's daily endpoint
    back-adjusts consistently (verified in the original fix report).
    Compute `observed = day_high / daily_high` and score it against two
    competing hypotheses instead of one absolute threshold:

    - `already adjusted`: `observed` should sit near `1.0`
    - `not yet adjusted`: `observed` should sit near `F`

    Take whichever hypothesis `observed` sits nearer to, but only when
    that nearness is decisive — the two distances must differ by more than
    `HourlySplitDetection.resolution_margin` (0.10, see `core/config.py`
    for where that number comes from and why it cannot reuse
    `range_escape_tolerance`). A day where `observed` sits roughly midway
    between `1.0` and `F` is not resolved either way: recorded as
    **unresolved** and returned separately, so the caller can refuse to
    write it rather than silently pick a hypothesis (invariant 4). This is
    the deliberate asymmetry the review flagged: guessing wrong here
    writes a plausible-looking but silently corrupt number that the
    downstream range-escape guard likely cannot catch either (the whole
    reason the ratio is ambiguous is that the resulting error is too small
    to trip a 50%-tolerance guard) — dropping the row instead means the
    prior good value, if any, survives untouched, and the gap is visible
    in `bar_rejects` rather than invisible in `bars`.

    An absent daily reference for a pending day leaves that day out of the
    unresolved set and defaults to the naive factor (divide) — the
    pre-existing baseline behavior, not a new guess, and the only decision
    obtainable with zero information about which hypothesis holds.

    **Residual limit.** As `HourlySplitDetection`'s docstring states, a
    split whose ratio sits within roughly `2 * resolution_margin` of 1.0
    can occasionally have a noisy day land in the unresolved band even
    when most of its days resolve cleanly — inherent to comparing two
    nearby points under real measurement noise, not a bug in the margin.

    This check runs once per pending split each day is under, not once
    globally, so a day with two compounding pending splits (as observed
    for DD, non-overlapping in practice) is only ever resolved as a unit
    for the whole pending set — a day where the vendor pre-adjusted only
    one of two simultaneously-pending splits is not something the
    diagnosis observed, and such a day would score against `F` = the full
    product, not either split individually, and most likely land in
    "not yet adjusted" or "unresolved" rather than a false "already
    adjusted" — still conservative, not a silent guess.
    """
    ts = raw["ts"]
    naive = pd.Series(1.0, index=ts.index, dtype="float64")
    if splits.empty:
        return naive, set()

    ratios = pd.to_numeric(splits["ratio"], errors="coerce")
    valid = ratios.notna() & (ratios > 0)
    if not valid.any():
        return naive, set()

    row_dates = pd.to_datetime(ts).dt.date
    ex_dates = pd.to_datetime(splits.loc[valid, "ex_date"]).dt.date
    for ex_date, ratio in zip(ex_dates, ratios[valid]):
        pre_split = row_dates < ex_date
        naive = naive.where(~pre_split, naive * float(ratio))

    pending = naive.ne(1.0)
    if not pending.any() or daily_range.empty:
        return naive, set()

    margin = DEFAULT_HOURLY_SPLIT_DETECTION.resolution_margin
    raw_day_high = raw.assign(d=row_dates).loc[pending].groupby("d")["high"].max()
    naive_by_day = naive.groupby(row_dates).first()
    daily_by_day = daily_range.set_index("d")["high"]

    already_adjusted_days: set = set()
    unresolved_days: set = set()
    for d, rh in raw_day_high.items():
        dh = daily_by_day.get(d)
        if dh is None or pd.isna(dh) or dh <= 0 or rh <= 0:
            continue  # no reliable daily reference: default to naive division
        f = naive_by_day.get(d, 1.0)
        observed = rh / dh
        dist_adjusted = abs(observed - 1.0)
        dist_unadjusted = abs(observed - f)
        if dist_adjusted + margin < dist_unadjusted:
            already_adjusted_days.add(d)
        elif dist_unadjusted + margin < dist_adjusted:
            continue  # decisively unadjusted: naive factor already correct
        else:
            unresolved_days.add(d)

    factor = naive.copy()
    if already_adjusted_days:
        skip_mask = row_dates.isin(already_adjusted_days)
        factor = factor.where(~skip_mask, 1.0)
    return factor, unresolved_days


def _back_adjust_hourly(
    raw: pd.DataFrame, splits: pd.DataFrame, daily_range: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Divide OHLC by `_split_adjustment_factor`, rounded to 4dp (convention).

    Computed fresh from the just-fetched `raw` frame, the current
    `corporate_actions` snapshot, and the current daily `bars` range every
    call — never applied to a previously-stored adjusted value — so a
    rerun of `run_bars_hourly` recomputes the same factor from the same
    raw input and rewrites the same numbers rather than adjusting an
    already-adjusted price a second time. That is what makes the upsert on
    `(ticker, ts, interval)` safe to repeat.

    Returns `(adjusted, unresolved)`: `adjusted` carries every input row
    (including unresolved-day rows, still divided by the naive factor —
    the caller drops them before upserting, this function only computes);
    `unresolved` is the subset of rows whose day landed in
    `_split_adjustment_factor`'s unresolved set, for the caller to reject
    and log rather than write.
    """
    if raw.empty:
        return raw, raw.iloc[:0]

    out = raw.copy()
    factor, unresolved_days = _split_adjustment_factor(out, splits, daily_range)
    for col in ("open", "high", "low", "close"):
        out[col] = (out[col] / factor).round(4)

    if not unresolved_days:
        return out, out.iloc[:0]

    row_dates = pd.to_datetime(out["ts"]).dt.date
    unresolved = out.loc[row_dates.isin(unresolved_days)]
    return out, unresolved


def _read_daily_range(
    engine: Engine, tickers: list[str], start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    """Per-(ticker, day) daily high/low, for the range-escape guard below.

    **Date-bounded since 2026-08-26.** This said "a single small query per
    run is simpler than re-querying per ticker", and the query was not
    small: unbounded over 1,470 tickers of full history it returns ~3.3M
    rows. The guard only ever looks up days inside the hourly window being
    checked, so every row outside it was read, held and merged against for
    nothing -- a nightly's 5-day window needs ~7,000 of those 3.3M.

    `start`/`end` default to None for callers that genuinely want the whole
    range; the hourly ingest passes its own window.
    """
    if not tickers:
        return pd.DataFrame(columns=["ticker", "d", "high", "low"])
    sql = (
        "SELECT ticker, ts::date AS d, high, low FROM bars "
        "WHERE interval = '1d' AND ticker = ANY(:tickers)"
    )
    params: dict[str, object] = {"tickers": tickers}
    if start is not None:
        sql += " AND ts >= :start"
        params["start"] = start
    if end is not None:
        sql += " AND ts <= :end"
        params["end"] = end
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)  # type: ignore[arg-type]


def _flag_range_escape(
    hourly: pd.DataFrame, daily_range: pd.DataFrame, guard=DEFAULT_HOURLY_SPLIT_GUARD
) -> list[dict]:
    """Reject rows whose day-aggregate hourly range escapes the matching
    daily bar's range by more than `guard.range_escape_tolerance`.

    This is the durable half (BUILD task spec): back-adjustment fixes the
    17 tickers already mismatched, but nothing stops the *next* split from
    silently reintroducing the same bug. Every hourly bar for a day whose
    aggregate range escapes is rejected — not just the offending hour —
    because an unadjusted split corrupts every bar on that side of the
    ex-date equally, and `bar_rejects` needs to name a row that is actually
    present in `hourly` to log against.

    Needs the matching daily bar, which may not exist yet on a from-scratch
    backfill (daily and hourly are ingested by separate jobs with no
    ordering guarantee). Absence is not evidence of a problem — a day with
    no daily counterpart is silently skipped (invariant 4: never guess),
    not rejected and not flagged clean. This lives in `run_bars_hourly`
    rather than `run_validate` because the reject needs to reference the
    specific hourly rows at ingest time, in the same run that already
    upserts `bar_rejects` for that ticker; `run_validate` only sees rejects
    already on file and has no per-bar granularity to attach a new one to.
    In the common case — daily ingested before hourly, which is how this
    codebase already runs backfills — the daily rows this guard needs are
    already on file, so the check fires every time it matters.
    """
    if hourly.empty or daily_range.empty:
        return []

    day_agg = (
        hourly.assign(d=pd.to_datetime(hourly["ts"]).dt.date)
        .groupby(["ticker", "d"])
        .agg(h_high=("high", "max"), h_low=("low", "min"))
        .reset_index()
    )
    joined = day_agg.merge(daily_range, on=["ticker", "d"], how="inner")
    if joined.empty:
        return []

    tol = guard.range_escape_tolerance
    escapes = joined.loc[
        (joined["high"] > 0)
        & (joined["low"] > 0)
        & (
            (joined["h_high"] > joined["high"] * (1 + tol))
            | (joined["h_low"] < joined["low"] * (1 - tol))
        )
    ]
    if escapes.empty:
        return []

    bad_days = set(zip(escapes["ticker"], escapes["d"], strict=False))
    hourly_days = pd.to_datetime(hourly["ts"]).dt.date
    rejects: list[dict] = []
    for idx, row in hourly.iterrows():
        key = (row["ticker"], hourly_days.at[idx])
        if key not in bad_days:
            continue
        rejects.append(
            {
                "ticker": row["ticker"],
                "ts": row["ts"],
                "rule": "hourly_daily_range_escape",
                "severity": "reject",
                "payload": {
                    "high": row["high"],
                    "low": row["low"],
                    "tolerance": tol,
                },
            }
        )
    return rejects


# ============ bars_daily / bars_hourly ============


def _drop_delisted_before(engine: Engine, tickers: list[str], start: date) -> list[str]:
    """Tickers that could still have been trading on or after `start`.

    **A delisted symbol does not stop resolving — it starts belonging to
    someone else.** Measured 2026-08-19: a 712-ticker backfill fetched four
    2026-08 bars each for FB, PCLN, PCS, NKTR and CPWR. Yahoo answered every
    one, with the current holder's prices: `FB` came back at $45 against a
    Meta that trades as `META`.

    ADR 035 keeps delisted names on purpose and this does not touch that.
    Their *historical* bars are exactly what removes survivorship bias. What
    cannot be real is a bar dated after the ticker stopped existing, and
    `tickers.delisted_on` is now populated from `last_bar`, so the check is
    a lookup rather than a guess.

    A ticker with no `delisted_on` is always kept: absent evidence of
    delisting is not evidence of delisting, and the alternative would drop
    live names the moment a stamp was missed.
    """
    if not tickers:
        return tickers
    with engine.connect() as conn:
        dead = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT ticker FROM tickers "
                    "WHERE delisted_on IS NOT NULL AND delisted_on < :start "
                    "AND ticker = ANY(:tickers)"
                ),
                {"start": start, "tickers": list(tickers)},
            )
        }
    return [t for t in tickers if t not in dead]


def run_bars_daily(
    tickers: list[str], start: date, end: date, engine: Engine | None = None
) -> IngestReport:
    engine = engine or db_io.get_engine()
    tickers = _drop_delisted_before(engine, tickers, start)
    with run_job(
        engine, "bars_daily", {"tickers": tickers, "start": str(start), "end": str(end)}
    ) as report:
        raw = yahoo.fetch_bars_daily(tickers, start, end)
        raw["interval"] = "1d"
        raw["source"] = "yahoo"
        raw["run_id"] = report.run_id

        actions = _read_corporate_actions(engine, tickers)
        clean, rejects = validate_bars(raw, actions)

        for r in rejects:
            r["run_id"] = report.run_id
        report.rows_rejected = sum(1 for r in rejects if r["severity"] == "reject")
        report.rows_flagged = sum(1 for r in rejects if r["severity"] == "flag")
        if rejects:
            db_io.append(engine, "bar_rejects", rejects)

        report.rows_written = db_io.upsert(engine, "bars", clean, ["ticker", "ts", "interval"])
        report.tickers = sorted(set(raw["ticker"])) if not raw.empty else []
        _update_ticker_coverage(engine, clean)

        # **The vendor's adjustment cannot be assumed.** See
        # `reconcile_daily_splits`. Runs after the upsert because it
        # reconciles what is now stored, not what was just fetched.
        adjusted = reconcile_daily_splits(engine, report.tickers, report.run_id)
        if adjusted:
            console.print(
                f"[bold yellow]back-adjusted {len(adjusted)} ticker(s)[/bold yellow] "
                f"for splits the vendor left unadjusted: {', '.join(sorted(adjusted))}"
            )
    return report


# How far back to look for splits needing reconciliation. A split is only
# ever mis-served around its ex-date, and re-checking twenty years of
# history every night would read the whole table to find nothing.
SPLIT_RECONCILE_DAYS = 120


def reconcile_daily_splits(
    engine: Engine, tickers: Sequence[str], run_id: str, lookback_days: int = SPLIT_RECONCILE_DAYS
) -> list[str]:
    """Back-adjust stored daily bars for splits the vendor did not apply.

    Returns the tickers adjusted.

    **`run_bars_daily` assumed Yahoo returns split-adjusted history.** On
    2026-08-27 that failed in both directions inside one week:

    - **IESC** (2-for-1, 2026-08-24) — the vendor never adjusted. A fresh
      fetch still returned 685.04 on 08-21 against 313.23 on 08-25, so
      re-fetching could not fix it. Our copy was faithful to a wrong
      source, and IESC's indicators were computed across a fabricated 52%
      crash that produced a signal.
    - **AVB** (2.793-for-1, 2026-08-17) — the vendor *did* adjust; our
      stored history was stale, because nothing re-reads history.

    Both were repaired by hand. This exists so the next one is not.

    **Only acts on a decisive `"unadjusted"`.** `daily_split_state` returns
    `"unresolved"` when the ratio sits within `MIN_DETECTABLE_SPLIT_DISTANCE`
    of 1.0, because there the adjusted and unadjusted hypotheses are not
    separable from ordinary daily noise — a first pass at this detection
    reported SCCO, HON, SPGI, CMCSA, FNF, UL and CBSH as unadjusted and all
    seven were false. Adjusting on a coin flip corrupts a correct series,
    which is strictly worse than leaving a suspect one alone.

    **Idempotent.** Once applied, the same comparison returns `"adjusted"`,
    so a second run is a no-op. That matters because this runs every night.

    **Writes an audit row per adjustment.** Silently rewriting stored prices
    is the kind of change that is impossible to explain later.
    """
    if not tickers:
        return []
    cutoff = date.today() - timedelta(days=lookback_days)
    with engine.connect() as conn:
        splits = pd.read_sql(
            text(
                "SELECT ticker, ex_date, ratio FROM corporate_actions "
                " WHERE action_type = 'split' AND ratio IS NOT NULL "
                "   AND ex_date >= :cutoff AND ticker = ANY(:tickers) "
                " ORDER BY ticker, ex_date"
            ),
            conn,
            params={"cutoff": cutoff, "tickers": list(tickers)},  # type: ignore[arg-type]
        )
    if splits.empty:
        return []

    adjusted: list[str] = []
    for row in splits.itertuples(index=False):
        ticker = str(row.ticker)
        ex_date = _as_day(row.ex_date)
        ratio = float(row.ratio)  # type: ignore[arg-type]
        with engine.connect() as conn:
            before = conn.execute(
                text(
                    "SELECT close FROM bars WHERE ticker=:t AND interval='1d' "
                    " AND ts < :d ORDER BY ts DESC LIMIT 1"
                ),
                {"t": ticker, "d": ex_date},
            ).scalar_one_or_none()
            after = conn.execute(
                text(
                    "SELECT close FROM bars WHERE ticker=:t AND interval='1d' "
                    " AND ts >= :d ORDER BY ts ASC LIMIT 1"
                ),
                {"t": ticker, "d": ex_date},
            ).scalar_one_or_none()
        if before is None or after is None:
            continue
        if daily_split_state(float(before), float(after), ratio) != "unadjusted":
            continue

        # Only the pre-ex-date rows are read and rewritten. `back_adjust_daily`
        # is the same function the unit tests pin, not a second copy of the
        # arithmetic (invariant 2).
        with engine.connect() as conn:
            frame = pd.read_sql(
                text("SELECT * FROM bars WHERE ticker=:t AND interval='1d' AND ts < :d"),
                conn,
                params={"t": ticker, "d": ex_date},  # type: ignore[arg-type]
            )
        if frame.empty:
            continue
        fixed = back_adjust_daily(frame, ex_date, ratio)
        fixed["run_id"] = run_id
        db_io.upsert(engine, "bars", fixed, ["ticker", "ts", "interval"])
        db_io.append(
            engine,
            "bar_rejects",
            [
                {
                    "ticker": ticker,
                    "ts": pd.Timestamp(ex_date),
                    "rule": "vendor_split_unadjusted",
                    "severity": "flag",
                    "payload": _json_safe_payload(
                        {
                            "ratio": ratio,
                            "ex_date": str(ex_date),
                            "rows_adjusted": int(len(fixed)),
                            "close_before": float(before),
                            "close_after": float(after),
                        }
                    ),
                    "run_id": run_id,
                }
            ],
        )
        adjusted.append(ticker)
    return adjusted


def _as_day(value: object) -> date:
    """`ex_date` comes back as a `date` from psycopg and a `Timestamp` from
    pandas depending on the path; `back_adjust_daily` compares against a
    `date`."""
    ts = pd.Timestamp(value)  # type: ignore[arg-type]
    return date(ts.year, ts.month, ts.day)


# Tickers buffered before one `bars` upsert in `run_bars_hourly`. Small
# enough that an interrupt costs little to redo, large enough that the round
# trips stop dominating: 1,470 tickers becomes 15 writes rather than 1,470.
HOURLY_WRITE_CHUNK = 100


def run_bars_hourly(
    tickers: list[str], start: date, end: date, engine: Engine | None = None
) -> IngestReport:
    engine = engine or db_io.get_engine()
    with run_job(
        engine, "bars_hourly", {"tickers": tickers, "start": str(start), "end": str(end)}
    ) as report:
        # Checkpointed per ticker (BUILD.md §5.4). The full 725-day walk is
        # ~13 windows across ~630 tickers at 0.5 req/s — over four hours,
        # well past CLAUDE.md's 10-minute checkpoint threshold. Buffering
        # every frame and writing once at the end meant an interrupt threw
        # away the whole run, which is exactly what happened twice.
        # Splits back-adjust the raw fetch — but only where the vendor has
        # not already done so (Session 9 hourly-residual fix: Yahoo's
        # hourly endpoint pre-adjusts a window of sessions immediately
        # before certain ex-dates, so `_split_adjustment_factor` checks
        # each pending day against the daily range before dividing); the
        # same daily range also backs the range-escape guard below. Both
        # read once for the whole ticker list, same shape as
        # `run_bars_daily`'s `_read_corporate_actions` call — a handful of
        # queries against small tables, not one per ticker.
        actions = _read_corporate_actions(engine, tickers)
        splits = actions.loc[actions["action_type"] == "split"] if not actions.empty else actions
        daily_range = _read_daily_range(engine, tickers, start, end)

        # **Indexed once, not scanned once per ticker.** The loop below used
        # `splits.loc[splits["ticker"] == ticker]` and the same for
        # `daily_range`, which is a full boolean scan of each frame per
        # ticker -- 2,940 scans across 1,470 tickers. That was invisible
        # while every iteration also paid ~2s of network; batching the fetch
        # (2026-08-26) removed the network and left the scans as a
        # measurable share of the step.
        #
        # `groupby` builds both indices in one pass each. Missing tickers get
        # an empty frame with the right columns, which is what the boolean
        # mask produced before, so `_back_adjust_hourly` sees no change.
        splits_by_ticker = (
            {str(k): v for k, v in splits.groupby("ticker", sort=False)} if not splits.empty else {}
        )
        range_by_ticker = (
            {str(k): v for k, v in daily_range.groupby("ticker", sort=False)}
            if not daily_range.empty
            else {}
        )
        actions_by_ticker = (
            {str(k): v for k, v in actions.groupby("ticker", sort=False)}
            if not actions.empty
            else {}
        )
        _no_splits = splits.iloc[0:0]
        _no_range = daily_range.iloc[0:0]
        _no_actions = actions.iloc[0:0]

        # **One request per batch per window, not one per ticker.** The
        # per-ticker path cost 54.5 minutes for 1,470 tickers on 2026-08-26
        # at `RATE_LIMIT_PER_SEC = 0.5`, against 4.9 minutes for the batched
        # daily path over the same universe -- the difference was the
        # request shape, not the data. `fetch_bars_hourly_many` was verified
        # against `fetch_bars_hourly` on live data before this call site
        # changed: identical frames, row for row, for every ticker tried.
        #
        # Fetched up front rather than lazily inside the loop, because the
        # window walk has to run once for the whole list to collapse the
        # per-ticker dimension at all.
        hourly_by_ticker = yahoo.fetch_bars_hourly_many(tickers, start, end)

        fetched: list[str] = []
        pending: list[pd.DataFrame] = []
        pending_tickers: list[str] = []

        def _flush() -> None:
            """Commit the buffered chunk. A no-op when nothing is pending,
            so the call after the loop needs no guard at the call site."""
            if not pending:
                pending_tickers.clear()
                return
            frame = pd.concat(pending, ignore_index=True)
            report.rows_written += db_io.upsert(engine, "bars", frame, ["ticker", "ts", "interval"])
            fetched.extend(pending_tickers)
            pending.clear()
            pending_tickers.clear()

        with Progress() as progress:
            task = progress.add_task("[cyan]hourly bars...", total=len(tickers))
            for ticker in tickers:
                # Absent means Yahoo returned nothing for it, which is what
                # an empty frame meant before.
                hourly = hourly_by_ticker.get(ticker, yahoo._empty_hourly_frame())
                progress.update(task, advance=1, description=f"[cyan]{ticker}[/cyan]")
                if hourly.empty:
                    # Delisted before the 725-day window opened. Expected for
                    # most of the union, not an error.
                    continue

                ticker_splits = splits_by_ticker.get(ticker, _no_splits)
                ticker_daily_range = range_by_ticker.get(ticker, _no_range)
                raw, unresolved = _back_adjust_hourly(hourly, ticker_splits, ticker_daily_range)

                unresolved_rejects: list[dict] = []
                if not unresolved.empty:
                    # Neither hypothesis ("vendor already adjusted this day"
                    # vs. "vendor did not") is decisively closer to the
                    # observed ratio — see _split_adjustment_factor's
                    # docstring. Dropped and logged rather than guessed
                    # (invariant 4): a wrong guess here would likely land
                    # inside the range-escape guard's own tolerance and
                    # pass through silently, the same failure shape this
                    # whole fix exists to close.
                    margin = DEFAULT_HOURLY_SPLIT_DETECTION.resolution_margin
                    for _, row in unresolved.iterrows():
                        unresolved_rejects.append(
                            {
                                "ticker": row["ticker"],
                                "ts": row["ts"],
                                "rule": "hourly_split_adjustment_unresolved",
                                "severity": "reject",
                                "payload": {
                                    "high": row["high"],
                                    "low": row["low"],
                                    "resolution_margin": margin,
                                },
                            }
                        )
                    raw = raw.loc[~raw["ts"].isin(unresolved["ts"])].reset_index(drop=True)

                raw["interval"] = "1h"
                # `adj_close` is total-return: DESIGN §2.2 keeps it separate
                # from the split-adjusted series. Hourly does not carry a
                # dividend adjustment (unchanged pre-existing limitation,
                # out of scope here) — `adj_close = close` inherits the
                # split back-adjustment above for free since `close` is
                # already back-adjusted by this point. `adj_factor` stays a
                # placeholder 1.0 for the same reason: neither line's
                # *meaning* changes, only the value `close` already carries
                # into them.
                raw["adj_close"] = raw["close"]
                raw["adj_factor"] = 1.0
                raw["source"] = "yahoo"
                raw["run_id"] = report.run_id

                # `corporate_actions` real (not None): enables validate_bars'
                # large-move-explained-by-split check on hourly bars too.
                # Safe here — after back-adjustment, a true split no longer
                # produces a jump for it to (mis)explain; the check remains
                # as a safety net for the same batch-boundary artifact
                # `unresolved_rejects` documents for daily (ANET 2024-10-07).
                # **This ticker's actions, not everyone's.** `validate_bars`
                # does `splits.groupby("ticker")` internally, so passing the
                # whole frame walked all 1,470 tickers' splits on every one
                # of 1,470 calls.
                clean, rejects = validate_bars(
                    raw, corporate_actions=actions_by_ticker.get(ticker, _no_actions)
                )
                rejects = rejects + unresolved_rejects

                # **This ticker's daily range, not the whole table.** The
                # guard merges on ["ticker", "d"], so an inner join against
                # the full frame can only ever match this ticker's rows --
                # it just walked ~3.3M of them to find ~5. That merge, 1,470
                # times, was the ~1 second per ticker the progress bar showed.
                guard_rejects = _flag_range_escape(clean, ticker_daily_range)
                if guard_rejects:
                    # `MultiIndex.isin` rather than a row-wise `.apply`.
                    # `axis=1` builds a Series per row and is the slowest
                    # thing pandas offers; this is one vectorised pass and
                    # answers the identical question.
                    bad_keys = {(r["ticker"], r["ts"]) for r in guard_rejects}
                    keys = pd.MultiIndex.from_arrays([clean["ticker"], clean["ts"]])
                    clean = clean.loc[~keys.isin(bad_keys)].reset_index(drop=True)
                    rejects = rejects + guard_rejects

                for r in rejects:
                    r["run_id"] = report.run_id
                report.rows_rejected += sum(1 for r in rejects if r["severity"] == "reject")
                report.rows_flagged += sum(1 for r in rejects if r["severity"] == "flag")
                if rejects:
                    db_io.append(engine, "bar_rejects", rejects)

                # **Buffered, then committed per chunk.** This upserted
                # once per ticker, which is one database round trip per
                # ticker -- 1,470 of them. That was free when each iteration
                # also paid ~2s of network; after the fetch was batched
                # (2026-08-26) the round trips became the step.
                #
                # The checkpoint is kept, only coarser. The key is still
                # (ticker, ts, interval), so an interrupt rewrites rather
                # than duplicates, and the most a restart redoes is one
                # chunk of a 5-day window.
                pending.append(clean)
                pending_tickers.append(ticker)
                if len(pending_tickers) >= HOURLY_WRITE_CHUNK:
                    _flush()

        _flush()
        report.tickers = sorted(set(fetched))
    return report


def _read_corporate_actions(engine: Engine, tickers: list[str]) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=["ticker", "ex_date", "action_type", "ratio", "amount"])
    with engine.connect() as conn:
        # pandas-stubs types `params` as scalar-valued only; psycopg's `ANY(:tickers)`
        # binding genuinely needs a list here, so the mismatch is the stub, not the call.
        return pd.read_sql(
            text("SELECT * FROM corporate_actions WHERE ticker = ANY(:tickers)"),
            conn,
            params={"tickers": tickers},  # type: ignore[arg-type]
        )


def _update_ticker_coverage(engine: Engine, clean_bars: pd.DataFrame) -> None:
    """Record `first_bar` / `last_bar` per ticker — the delisting signal
    DESIGN §4.3 calls out under silent truncation.
    """
    if clean_bars.empty:
        return
    coverage = clean_bars.groupby("ticker")["ts"].agg(first_bar="min", last_bar="max").reset_index()
    coverage["first_bar"] = pd.to_datetime(coverage["first_bar"]).dt.date
    coverage["last_bar"] = pd.to_datetime(coverage["last_bar"]).dt.date
    with engine.begin() as conn:
        for _, row in coverage.iterrows():
            conn.execute(
                text(
                    "UPDATE tickers SET "
                    "first_bar = LEAST(COALESCE(first_bar, :first_bar), :first_bar), "
                    "last_bar = GREATEST(COALESCE(last_bar, :last_bar), :last_bar) "
                    "WHERE ticker = :ticker"
                ),
                {
                    "first_bar": row["first_bar"],
                    "last_bar": row["last_bar"],
                    "ticker": row["ticker"],
                },
            )


# ============ actions ============


ACTIONS_WINDOW_DAYS = 30


def _tickers_with_actions(engine: Engine, tickers: list[str]) -> set[str]:
    """Which of these already have corporate actions on file.

    Decides full-history versus incremental per ticker. One query, not one
    per ticker.
    """
    if not tickers:
        return set()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT ticker FROM corporate_actions WHERE ticker = ANY(:tickers)"),
            {"tickers": tickers},
        ).scalars()
        return set(rows)


def run_actions(
    tickers: list[str],
    engine: Engine | None = None,
    start: date | None = None,
    end: date | None = None,
) -> IngestReport:
    """Splits and dividends.

    **Two paths, split on whether the ticker already has history.**

    A ticker with rows on file needs only what is new, and that is
    batchable: `fetch_actions_many` asks one request per `DAILY_BATCH_SIZE`
    symbols. A ticker with nothing on file needs its whole series, which
    only `yf.Ticker(t).actions` provides, one request each.

    Nightly therefore costs a handful of requests instead of one per ticker.
    Measured 2026-08-26 before this change: 1,470 tickers, 21.3 minutes when
    the cache missed and 0.3 minutes when it hit -- and it always hit,
    because `fetch_actions` keyed on the bare ticker and froze every name at
    its first fetch. Batching without dating the key would have been fast
    and still wrong; dating it without batching would have been correct and
    ~49 minutes.

    `start`/`end` default to a `ACTIONS_WINDOW_DAYS` lookback. The upsert
    key is `(ticker, ex_date, action_type)`, so an overlapping window is a
    no-op and a missed night heals on the next run.
    """
    engine = engine or db_io.get_engine()
    end = end or date.today()
    start = start or (end - timedelta(days=ACTIONS_WINDOW_DAYS))
    with run_job(
        engine, "actions", {"tickers": tickers, "start": str(start), "end": str(end)}
    ) as report:
        known = _tickers_with_actions(engine, tickers)
        fresh = [t for t in tickers if t not in known]
        incremental = [t for t in tickers if t in known]

        frames: list[pd.DataFrame] = []
        if incremental:
            frames.extend(yahoo.fetch_actions_many(incremental, start, end).values())
        # Full history, one request each. Rare by construction -- this is a
        # ticker's first ingest, not a nightly cost.
        frames.extend(yahoo.fetch_actions(t) for t in fresh)
        frames = [f for f in frames if not f.empty]
        if not frames:
            report.tickers = []
            return report

        raw = pd.concat(frames, ignore_index=True)
        rows = []
        for _, row in raw.iterrows():
            rows.append(
                {
                    "ticker": row["ticker"],
                    "ex_date": pd.Timestamp(row["ts"]).date(),
                    "action_type": row["action_type"],
                    "ratio": row["value"] if row["action_type"] == "split" else None,
                    "amount": row["value"] if row["action_type"] == "dividend" else None,
                }
            )
        report.rows_written = db_io.upsert(
            engine, "corporate_actions", rows, ["ticker", "ex_date", "action_type"]
        )
        report.tickers = sorted(set(raw["ticker"]))
    return report


# ============ market ============


def run_market(lookback_days: int, engine: Engine | None = None) -> IngestReport:
    engine = engine or db_io.get_engine()
    with run_job(engine, "market", {"lookback_days": lookback_days}) as report:
        end = date.today()
        start = end - timedelta(
            days=lookback_days + 260
        )  # extra history for the 252d VIX percentile
        spx_bars = yahoo.fetch_bars_daily(["^GSPC"], start, end)
        vix_bars = yahoo.fetch_bars_daily(["^VIX"], start, end)
        if spx_bars.empty and vix_bars.empty:
            return report

        spx_close = (
            spx_bars.set_index("ts")["close"].rename("spx_close")
            if not spx_bars.empty
            else pd.Series(dtype=float)
        )
        vix_close = (
            vix_bars.set_index("ts")["close"].rename("vix_close")
            if not vix_bars.empty
            else pd.Series(dtype=float)
        )
        merged = pd.concat([spx_close, vix_close], axis=1).sort_index()
        merged["spx_ret_1d"] = merged["spx_close"].pct_change()
        merged["vix_pct_252d"] = (
            merged["vix_close"].rolling(252).apply(lambda w: (w <= w.iloc[-1]).mean(), raw=False)
        )
        merged = merged.tail(lookback_days).reset_index().rename(columns={"ts": "ts_full"})
        merged["ts"] = pd.to_datetime(merged["ts_full"]).dt.date
        rows = merged[["ts", "spx_close", "spx_ret_1d", "vix_close", "vix_pct_252d"]]
        report.rows_written = db_io.upsert(engine, "market_days", rows, ["ts"])
    return report


# ============ shares ============


def _period_end_key(row: dict) -> str:
    """Sortable `period_end`, with a missing value ordering lowest.

    A dropped `end` arrives from pandas as `NaN`, not `None`, so the
    self-inequality test is the reliable check for both.
    """
    period_end = row["period_end"]
    if period_end is None or period_end != period_end:
        return ""
    return str(period_end)


def _accn_key(row: dict) -> str:
    """Sortable accession number, with a missing value ordering lowest.

    Accession numbers are assigned in filing sequence
    (`NNNNNNNNNN-YY-NNNNNN`), so a lexicographic compare orders two
    filings from the same filer and year the same way a filing-date
    compare would. That is exactly what breaks a `_period_end_key` tie
    correctly: real duplicate-`filed_on` cases in the wild are same-day
    original/amendment pairs (10-Q then 10-Q/A, or 10-K then 10-K/A)
    reporting the *same* `period_end` with a corrected `value` — the
    amendment always carries the later accession number. Without this,
    the tie resolves on whatever order `facts.iterrows()` happens to
    hand rows back in, which is an accident of the SEC JSON's ordering,
    not a rule.
    """
    accn = row.get("accn")
    if accn is None or accn != accn:
        return ""
    return str(accn)


def _parse_filed_on(value: object) -> date | None:
    """`filed_on` arrives as an ISO string from SEC's JSON, a `date` from a
    prior parse, or a null-like sentinel (`None`/NaN) — normalize all three
    to `date | None` so staleness math never has to special-case the type.

    An unparseable string returns `None` rather than raising: this feeds a
    staleness *check*, not the write path (the malformed value still gets
    upserted as-is, unchanged), so one bad `filed_on` should count toward
    "no usable date to judge freshness by" — same as a missing one — not
    abort the run for every other ticker in the batch.
    """
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _depositary_tickers(engine: Engine, tickers: list[str]) -> set[str]:
    """Which of `tickers` are depositary listings, by name.

    Read here rather than passed in because `run_shares` takes a ticker
    list, and the listing name is the only signal that a price is per
    receipt (`core.universe.is_depositary_listing` explains why the name
    and not `ADR`/`ADS` substrings).
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ticker, name FROM tickers WHERE ticker = ANY(:tickers)"),
            {"tickers": tickers},
        ).fetchall()
    return {r.ticker for r in rows if core_universe.is_depositary_listing(r.name)}


# One implementation, in `db_io`, because `db_io.append` is the choke point
# every reject passes through and per-site wrapping already failed once:
# two sites were wrapped on 2026-08-26 and a third was missed, so the same
# failure recurred two minutes after the commit. These aliases keep the
# construction sites self-documenting.
_json_safe = db_io.json_safe
_json_safe_payload = db_io.json_safe_payload


def _implausible_shares_reason(shares: int, bounds: SharesPlausibility) -> str | None:
    """`None` when `shares` is plausible; otherwise the `bar_rejects.rule`.

    Reject, never correct (invariant 4, and the BUILD note on this task):
    dividing by an inferred scale factor would be guessing at the factor
    from the data's own shape, which is exactly how a wrong guess turns
    into a plausible-looking wrong number. Bounds are absolute, not
    relative to this ticker's own history — see `SharesPlausibility`'s
    docstring in `core/config.py` for why (the PSKY counterexample: its own
    median is 1,000, which would flag its one genuine filing as the
    outlier).
    """
    if shares < bounds.min_shares:
        return "shares_below_plausible_floor"
    if shares > bounds.max_shares:
        return "shares_above_plausible_ceiling"
    return None


# Tickers that are ETFs/trusts rather than operating companies (user's
# decision, 2026-08-05): SEC's `company_tickers.json` assigns them a real
# CIK, so `fetch_cik_lookup` finds one and `run_shares`/`run_earnings`
# proceed to call the XBRL companyfacts/submissions endpoints — which 404
# (`NotFoundError`), because an ETF files none of the 10-K/10-Q/8-K forms
# those endpoints serve. Not a data gap to retry or investigate; expected
# and permanent for this class of ticker. Named explicitly rather than
# caught as a blanket `except NotFoundError` around every SEC call, so a
# 404 on an actual operating company (a real data problem) still surfaces
# instead of being silently swallowed alongside this. Kept here, not in
# `jobs/fetch/sec.py`, because which tickers to skip is ingest policy, not
# a fact about how to talk to SEC.
SEC_NON_FILER_TICKERS = frozenset({"QQQ", "SPY", "VOO", "IBIT"})
# `SPY` added 2026-08-25. It was missing from a list that named the other
# three, which is the least defensible gap of the four: the universe is
# seeded from S&P 500 membership and SPY is that index's tracker.
# `VOO` and `IBIT` added 2026-08-21 with the ETF expansion (ADR 143). Same
# reason as QQQ: an ETF files no 10-K/10-Q/8-K, so the XBRL endpoints 404 by
# design and `run_shares` falls through to the Yahoo source that already
# serves 68 tickers.
#
# **`IBIT` is the one that needs watching**, and not for its shares. A spot
# Bitcoin trust has no sector, no industry and no earnings date, and
# `cell_id` is built from exactly those columns -- so it lands in a
# NULL-sector cell rather than being excluded, and ADR 041's earnings-window
# exclusion silently does nothing for it. Tradeable and uncellable is a
# state nothing in the schema forbids. See `BACKLOG.md`.


def run_exchange_expansion(
    exchange: str,
    min_mcap_usd: float | None = None,
    engine: Engine | None = None,
) -> IngestReport:
    """Seed `tickers` from an exchange screener (ADR 143's path, generalised).

    ADR 143 added Nasdaq as a second seed source beside the S&P 500 union.
    That round inserted its rows by hand; this is the same work written
    down, so the NYSE round is reproducible rather than remembered.

    **Inserts only.** `ON CONFLICT DO NOTHING`, so a ticker already on file
    keeps its sector, its CIK and its `is_active` flag. An upsert here would
    overwrite ADR 148's resolved sectors with the screener's own vocabulary,
    which is neither GICS nor Yahoo's and would silently reintroduce the
    two-levels-for-one-sector defect 148 exists to fix.

    **`sector` is left NULL on new rows for the same reason.**
    `run_sector_backfill` resolves it from the source of record afterwards.
    The screener does return a sector, and using it would look like a free
    win; it is the wrong vocabulary and 148 already paid for that lesson.

    **CIK is resolved at insert**, because without it `run_shares` finds no
    filings, `mcap_usd` stays NULL and every new name fails `crit_mcap`
    while `cscan universe` reports success. ADR 143 records exactly that
    happening: 296 rows with a NULL market cap, no error, no warning. The
    order that works is bars, indicators, **shares**, then universe.

    `min_mcap_usd` defaults to the ingest floor, deliberately below the
    trade floor: a name at $6B today may have been above $20B earlier in
    the window, and its bars are the only way those quarters can be
    measured.
    """
    from capitalscan.jobs.fetch import nasdaq as screener

    engine = engine or db_io.get_engine()
    floor = screener.INGEST_MIN_MCAP_USD if min_mcap_usd is None else min_mcap_usd

    with run_job(engine, "tickers", {"exchange": exchange, "min_mcap_usd": floor}) as report:
        symbols = screener.tickers_above(floor, exchange)
        if not symbols:
            report.notes = f"screener returned no {exchange} listings at or above {floor:,.0f}"
            return report

        cik_lookup = sec.fetch_cik_lookup().set_index("ticker")["cik"]
        rows = [
            {
                "ticker": t,
                "cik": str(cik_lookup.get(t)) if t in cik_lookup.index else None,
                "name": None,
                "sector": None,
                "industry": None,
                "exchange": exchange,
                "is_active": True,
            }
            for t in symbols
        ]

        with engine.begin() as conn:
            existing = {
                r[0]
                for r in conn.execute(
                    text("SELECT ticker FROM tickers WHERE ticker = ANY(:syms)"),
                    {"syms": symbols},
                )
            }
        new_rows = [r for r in rows if r["ticker"] not in existing]

        if new_rows:
            # **Explicit `DO NOTHING`, not `db_io.upsert`.** That helper has
            # no insert-only mode: `update_columns=None` overwrites every
            # non-key column, which on a conflict would blank an existing
            # row's ADR 148 sector and its CIK, and `update_columns=[]` is
            # rejected by design. The pre-filter above already excludes
            # known tickers, so this clause is the guard against a row
            # appearing between the SELECT and the INSERT rather than the
            # normal path.
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tickers "
                        "(ticker, cik, name, sector, industry, exchange, is_active) "
                        "VALUES (:ticker, :cik, :name, :sector, :industry, :exchange, :is_active) "
                        "ON CONFLICT (ticker) DO NOTHING"
                    ),
                    new_rows,
                )

        with_cik = sum(1 for r in new_rows if r["cik"])
        report.rows_written = len(new_rows)
        report.notes = (
            f"{exchange}: {len(symbols)} listings at or above {floor:,.0f}, "
            f"{len(existing)} already on file, {len(new_rows)} inserted, "
            f"{with_cik} with a CIK. Sector is NULL by design -- run "
            f"run_sector_backfill next, then bars, indicators, shares, universe."
        )
    return report


def run_sector_backfill(
    tickers: list[str] | None = None,
    engine: Engine | None = None,
) -> IngestReport:
    """Resolve every non-canonical `tickers.sector` to a GICS sector (ADR 148).

    Covers both defects with one pass, because both make the column unusable
    as a categorical level and both are repaired the same way:

    - **NULL sector.** `run_tickers_refresh` writes this column solely from
      Wikipedia's *current* S&P 500 table, so removed constituents (kept
      deliberately by ADR 035) and the ADR 143 Nasdaq additions arrive blank.
    - **A sector in another vocabulary.** 620 rows carried Nasdaq's names, so
      `Information Technology` and `Technology` were two levels for one
      sector -- 53,031 against 4,513 training events.

    **Re-resolved from Yahoo, never crosswalked from what is stored.** Nasdaq
    and Yahoo both emit the string `"Technology"` and disagree about who is
    in it: Nasdaq files NTES and BILI under it, while GICS and Yahoo both
    call those Communication Services. Mapping the stored label would
    reclassify a gaming company as Information Technology on the strength of
    a matching string, which is precisely the fabricated value invariant 4
    forbids. So the stored value is treated as *unknown* and the ticker is
    looked up again.

    **Anything Yahoo does not resolve stays blank.** No default, no
    `Miscellaneous`, no inference from industry text. ADR 147's training
    frame keeps raising on those rows until a real source resolves them,
    which is the intended outcome rather than a gap.

    Writes `tickers.sector` and nothing else. Sector is not in `config_hash`,
    is not read by any `UniverseParams.required_criteria`, and is not a
    component of `cell_id` (`cell_key` takes nine arguments and sector is not
    among them), so **no universe rebuild and no backtest re-run**.
    """
    engine = engine or db_io.get_engine()
    with run_job(engine, "sector_backfill", {"tickers": tickers}) as report:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT ticker, sector FROM tickers ORDER BY ticker")
            ).fetchall()

        wanted = {t.strip().upper() for t in tickers} if tickers else None
        candidates = [
            r.ticker
            for r in rows
            if core_sectors.needs_resolution(r.sector)
            and (wanted is None or r.ticker in wanted)
            # An ETF has no sector to resolve (ADR 147). Looking one up
            # would invent the attribute rather than populate it.
            and not core_training.is_etf(r.ticker)
        ]

        updates: list[dict] = []
        unresolved: list[str] = []
        for ticker in candidates:
            try:
                frame = yahoo.fetch_sector(ticker)
            except Exception as exc:  # noqa: BLE001 - one bad symbol is a skip
                unresolved.append(f"{ticker}({type(exc).__name__})")
                continue
            raw = None if frame.empty else frame.iloc[0].get("sector")
            gics = core_sectors.normalize_yahoo_sector(raw)
            if gics is None:
                unresolved.append(ticker)
                continue
            updates.append({"ticker": ticker, "sector": gics})

        if updates:
            with engine.begin() as conn:
                for row in updates:
                    conn.execute(
                        text("UPDATE tickers SET sector = :sector WHERE ticker = :ticker"),
                        row,
                    )

        report.rows_written = len(updates)
        report.rows_rejected = len(unresolved)
        report.tickers = [u["ticker"] for u in updates]
        report.notes = (
            f"resolved {len(updates)} of {len(candidates)}; "
            f"unresolved (left blank): {', '.join(sorted(unresolved)) or 'none'}"
        )
    return report


def run_shares(
    tickers: list[str],
    engine: Engine | None = None,
    as_of: date | None = None,
    shares_bounds: SharesPlausibility = SharesPlausibility(),
) -> IngestReport:
    """Point-in-time shares outstanding from SEC XBRL (DESIGN §2.4), with a
    yfinance fallback for tickers SEC has stopped covering (BUILD follow-up).

    Stores every filed value with its `filed_on` date; the "latest filing
    with `filed_on < as_of`" selection happens at query time in the
    `universe` job, not here.

    `as_of` is the reference "today" for the staleness check and for the
    Yahoo fetch window's upper bound — injectable so tests do not depend on
    wall-clock date; defaults to `date.today()` for real runs, consistent
    with how this job is actually invoked (jobs own clock access, invariant
    1's IO carve-out).

    `shares_bounds` gates every candidate row — SEC and Yahoo alike — through
    `_implausible_shares_reason` before it is ever eligible for the upsert.
    A filing outside the band is dropped and logged to `bar_rejects`
    (invariant 4), never corrected: this is the Session 9 shares-guard fix
    for the mcap-outlier diagnosis (a single XBRL filing scaled x1,000 or
    x1,000,000 was reaching `universe.mcap_usd` with no check at all).
    Defaulted rather than required so every existing call site keeps working
    unchanged; `core.config.SharesPlausibility` documents where the default
    bounds come from.
    """
    as_of = as_of or date.today()
    engine = engine or db_io.get_engine()
    with run_job(engine, "shares", {"tickers": tickers}) as report:
        cik_lookup = sec.fetch_cik_lookup().set_index("ticker")["cik"]
        rows = []
        touched = []
        # Rejected candidate rows (SEC and Yahoo both), destined for
        # `bar_rejects` — reused here as the project's one append-only
        # reject log (invariant 4) rather than inventing a second table for
        # a second kind of row.
        share_rejects: list[dict] = []
        # Tickers that end up contributing zero rows, with why. Distinct
        # from a hard failure — some issuers genuinely have no usable fact
        # in the feed (see the comment above `_SHARES_TAG_SOURCES` in
        # `fetch/sec.py`) — but "why" must be visible in the run's notes
        # instead of only showing up later as a silent gap in `universe`'s
        # market-cap numbers, which is how this got missed the first time.
        skipped: list[tuple[str, str]] = []
        for ticker in tickers:
            if ticker in SEC_NON_FILER_TICKERS:
                skipped.append((ticker, "ETF/trust, not an SEC filer"))
                continue
            cik = cik_lookup.get(ticker)
            if cik is None:
                skipped.append((ticker, "no CIK in SEC ticker lookup"))
                continue
            try:
                facts = sec.fetch_company_facts(int(cik))
            except NotFoundError:
                # **Recorded, not raised** (2026-08-21). SEC's own
                # ticker->CIK map can point at a CIK with no `companyfacts`
                # document: OZK (Bank OZK) resolves to CIK 1569650, which
                # 404s, almost certainly a predecessor entity left in the
                # map after a reorganisation.
                #
                # This used to propagate, and one such ticker aborted the
                # whole run -- 316 tickers, zero rows written, `status =
                # failed`. The intent behind letting it escape was right and
                # is preserved: a 404 on a real operating company is a data
                # problem and must not be swallowed. It is now swallowed
                # *per ticker* into `skipped`, which the report surfaces, so
                # the signal survives without taking 315 healthy tickers
                # with it.
                #
                # Same shape as `run_backtest`'s `failed_tickers`: collect
                # per unit, continue, and let the caller see the list.
                skipped.append((ticker, f"SEC has no companyfacts for CIK {int(cik)}"))
                continue
            if facts.empty:
                skipped.append((ticker, f"no shares-outstanding fact for CIK {int(cik)}"))
                continue
            facts = facts.dropna(subset=["filed_on", "value"])
            for _, row in facts.iterrows():
                shares = int(row["value"])
                reason = _implausible_shares_reason(shares, shares_bounds)
                if reason is not None:
                    share_rejects.append(
                        {
                            "ticker": ticker,
                            "ts": row["filed_on"],
                            "rule": reason,
                            "severity": "reject",
                            "payload": _json_safe_payload(
                                {
                                    "shares": shares,
                                    "filed_on": row["filed_on"],
                                    "period_end": row.get("end"),
                                    "source": "sec_xbrl",
                                    "accn": row.get("accn"),
                                }
                            ),
                        }
                    )
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "filed_on": row["filed_on"],
                        "period_end": row["end"],
                        "shares": shares,
                        "source": "sec_xbrl",
                        "accn": row.get("accn"),
                    }
                )
            touched.append(ticker)
        # `shares_outstanding` is keyed (ticker, filed_on), but one filing
        # reports the share count for several periods, so a single `filed_on`
        # yields several XBRL facts. Postgres rejects an ON CONFLICT whose own
        # proposed rows collide with each other ("cannot affect row a second
        # time"), so pick one per filing before the write.
        #
        # The survivor is the fact with the latest `period_end` — the most
        # current count that filing carries. DESIGN §2.4 leaves the "latest
        # filing with filed_on < as_of" selection to the `universe` job, so
        # what belongs here is one correct row per filing, not every fact.
        #
        # Ties on `period_end` (a same-day original/amendment pair, e.g. a
        # 10-Q immediately corrected by a 10-Q/A covering the identical
        # period) break on `accn` — see `_accn_key`. Without a second key
        # the tie resolves on row order alone, which happens to be the
        # order the SEC JSON lists facts in, not a documented rule.
        best: dict[tuple[str, object], dict] = {}
        for row in rows:
            key = (row["ticker"], row["filed_on"])
            prior = best.get(key)
            if prior is None or (_period_end_key(row), _accn_key(row)) >= (
                _period_end_key(prior),
                _accn_key(prior),
            ):
                best[key] = row

        # The x1,000 class, which `_implausible_shares_reason` cannot see.
        #
        # **After the dedup, not before.** The check reads a ticker's filing
        # series and compares each value to its time-neighbours, so it needs
        # exactly one row per `filed_on` -- the shape `shares_outstanding`
        # itself has. Run against `rows`, where one filing contributes
        # several facts, the same value repeats and drags the local median
        # toward whichever filing happened to report the most periods.
        #
        # Rejected and logged like any other bad filing (invariant 4);
        # nothing is divided or corrected. See
        # `core.universe.scale_error_indices` for why a *local* window is
        # not the relative test `SharesPlausibility` rules out.
        by_ticker: dict[str, list[tuple[str, object]]] = {}
        for key in best:
            by_ticker.setdefault(key[0], []).append(key)
        for tkr, keys in by_ticker.items():
            keys.sort(key=lambda k: _parse_filed_on(best[k]["filed_on"]) or date.min)
            series = [float(best[k]["shares"]) for k in keys]
            for i in core_universe.scale_error_indices(series, shares_bounds):
                row = best.pop(keys[i])
                share_rejects.append(
                    {
                        "ticker": tkr,
                        "ts": row["filed_on"],
                        "rule": "shares_scale_error_x1000",
                        "severity": "reject",
                        "payload": _json_safe_payload(
                            {
                                "shares": row["shares"],
                                "filed_on": row["filed_on"],
                                "period_end": row.get("period_end"),
                                "source": "sec_xbrl",
                                "accn": row.get("accn"),
                            }
                        ),
                    }
                )

        upsert_rows = [{k: v for k, v in row.items() if k != "accn"} for row in best.values()]

        # Fallback: SEC's dei fact does not degrade, it disappears the
        # quarter a filer starts reporting per share class (`fetch/sec.py`
        # `_SHARES_TAG_SOURCES`), and a filer skipped above (no CIK / no
        # fact at all) has the identical symptom as one whose only fact
        # predates `SHARES_STALENESS_DAYS` — both need Yahoo's dated series
        # to keep the ticker's market cap from being computed off a decade-
        # old share count (the BRK-B/MA/V bug this session exists to fix).
        latest_sec_filed_on: dict[str, date] = {}
        for (t, _), row in best.items():
            parsed = _parse_filed_on(row["filed_on"])
            if parsed is not None and parsed > latest_sec_filed_on.get(t, date.min):
                latest_sec_filed_on[t] = parsed

        yahoo_used: list[str] = []
        yahoo_skipped: list[tuple[str, str]] = []
        yahoo_best: dict[tuple[str, date], dict] = {}
        # Normalized to (ticker, parsed date): `best`'s keys carry SEC's raw
        # `filed_on` (an ISO string straight from the JSON), while Yahoo's
        # `filed_on` is already a `date` — comparing the raw forms would
        # never match even on the same calendar day and silently let a
        # same-dated Yahoo row through instead of deferring to the SEC one.
        existing_keys = {
            (t, parsed)
            for (t, raw_filed_on) in best
            if (parsed := _parse_filed_on(raw_filed_on)) is not None
        }
        # Depositary listings always take the Yahoo call, however fresh SEC
        # is. SEC reports the issuer's *ordinary* shares while `bars.close`
        # is per ADR, so a current SEC filing is not a usable count here --
        # it is the right number in the wrong unit. NTES priced at $1,666.9B
        # against a real ~$100B on 3,349,335,066 ordinary shares.
        #
        # Source switch, not a scale factor. The ratio is not reliably
        # derivable: VOD measures 11.54 against a real 10:1, and LI and ONC
        # land nowhere near an integer, because SEC's latest filing and
        # Yahoo's current count are months apart. Inferring one is what
        # `_implausible_shares_reason` warns turns into "a plausible-looking
        # wrong number". Yahoo's count is already per ADR, so nothing is
        # computed and nothing is rounded.
        depositary = _depositary_tickers(engine, tickers)

        for ticker in tickers:
            latest = latest_sec_filed_on.get(ticker)
            if (
                latest is not None
                and (as_of - latest).days <= SHARES_STALENESS_DAYS
                and ticker not in depositary
            ):
                continue  # SEC is current enough; do not spend a Yahoo call
            # **ADR 156: a fund does not have a share count to report.**
            # `netAssets / price` is exact for a fund (`price x shares` IS
            # net assets, by definition of NAV), so this is a change of
            # units rather than an estimate. Tried before `shares_full`
            # because for an ETF that fetcher returns either a 2021 constant
            # or nothing at all.
            if core_training.is_etf(ticker):
                try:
                    na = yahoo.fetch_net_assets(ticker)
                except Exception as exc:  # noqa: BLE001 - a skip, not a failure
                    yahoo_skipped.append((ticker, f"netAssets fetch raised: {exc}"))
                    continue
                if na.empty:
                    yahoo_skipped.append((ticker, "no netAssets published"))
                    continue
                row = na.iloc[0]
                derived = int(round(float(row["net_assets"]) / float(row["price"])))
                reason = _implausible_shares_reason(derived, shares_bounds)
                if reason is not None:
                    share_rejects.append(
                        {
                            "ticker": ticker,
                            "ts": as_of,
                            "rule": reason,
                            "severity": "reject",
                            "payload": _json_safe_payload(
                                {
                                    "shares": derived,
                                    "net_assets": float(row["net_assets"]),
                                    "price": float(row["price"]),
                                    "source": SHARES_NETASSETS_SOURCE,
                                }
                            ),
                        }
                    )
                    continue
                # One row, dated today. `netAssets` is a current figure and
                # there is no history to fabricate -- the same reason
                # `fetch_shares_full`'s docstring rejects holding one
                # current number backward across every `as_of`.
                if (ticker, as_of) not in existing_keys:
                    upsert_rows.append(
                        {
                            "ticker": ticker,
                            "filed_on": as_of,
                            "period_end": None,
                            "shares": derived,
                            "source": SHARES_NETASSETS_SOURCE,
                        }
                    )
                yahoo_used.append(ticker)
                continue

            try:
                full = yahoo.fetch_shares_full(ticker, SHARES_FULL_HISTORY_START, as_of)
            except Exception as exc:  # yfinance failure is a skip, not a job failure
                yahoo_skipped.append((ticker, f"yahoo shares_full fetch raised: {exc}"))
                continue
            if full.empty:
                yahoo_skipped.append((ticker, "no yahoo shares_full data either"))
                continue
            for _, row in full.iterrows():
                filed_on = row["filed_on"]
                key = (ticker, filed_on)
                # A `filed_on` this Yahoo fetch shares with an existing SEC
                # row is left to the SEC row: mixing "filed" and "estimated"
                # values under one PK for the same date has no defined
                # merge policy (`db_io.upsert` overwrites whole rows, no
                # per-column merge), and it is also a batch-internal
                # ON CONFLICT collision if both land in one upsert call.
                if key in existing_keys:
                    continue
                shares = int(row["shares"])
                # The fallback source is not exempt from the plausibility
                # guard: Yahoo's dated estimates are as capable of carrying
                # a bad point as an SEC filing is (same rationale as the SEC
                # branch above).
                reason = _implausible_shares_reason(shares, shares_bounds)
                if reason is not None:
                    share_rejects.append(
                        {
                            "ticker": ticker,
                            "ts": filed_on,
                            "rule": reason,
                            "severity": "reject",
                            "payload": _json_safe_payload(
                                {
                                    "shares": shares,
                                    "filed_on": filed_on,
                                    "period_end": None,
                                    "source": SHARES_YAHOO_SOURCE,
                                }
                            ),
                        }
                    )
                    continue
                yahoo_best[key] = {
                    "ticker": ticker,
                    "filed_on": filed_on,
                    "period_end": None,
                    "shares": shares,
                    "source": SHARES_YAHOO_SOURCE,
                }
            yahoo_used.append(ticker)

        upsert_rows.extend(yahoo_best.values())

        report.rows_written = db_io.upsert(
            engine, "shares_outstanding", upsert_rows, ["ticker", "filed_on"]
        )
        report.tickers = touched + [t for t in yahoo_used if t not in touched]

        if share_rejects:
            for r in share_rejects:
                r["run_id"] = report.run_id
            db_io.append(engine, "bar_rejects", share_rejects)
        report.rows_rejected = len(share_rejects)

        note_parts = []
        if skipped:
            note_parts.append(
                f"{len(skipped)} of {len(tickers)} tickers contributed no SEC rows: "
                + "; ".join(f"{t} ({reason})" for t, reason in skipped)
            )
        if yahoo_used:
            note_parts.append(
                f"{len(yahoo_used)} ticker(s) backed by {SHARES_YAHOO_SOURCE} "
                "fallback (SEC data absent or older than "
                f"{SHARES_STALENESS_DAYS} days): " + ", ".join(sorted(yahoo_used))
            )
        if yahoo_skipped:
            note_parts.append(
                f"{len(yahoo_skipped)} ticker(s) had no usable shares data from "
                "either source: " + "; ".join(f"{t} ({reason})" for t, reason in yahoo_skipped)
            )
        if share_rejects:
            rejected_tickers = sorted({r["ticker"] for r in share_rejects})
            note_parts.append(
                f"{len(share_rejects)} filing(s) across {len(rejected_tickers)} ticker(s) "
                "rejected by the shares-plausibility guard (outside "
                f"[{shares_bounds.min_shares}, {shares_bounds.max_shares}] shares, logged to "
                "bar_rejects): " + ", ".join(rejected_tickers)
            )
        if note_parts:
            report.notes = " | ".join(note_parts)
    return report


# ============ earnings ============


def run_earnings(
    tickers: list[str],
    historical: bool = False,
    forward_days: int = 0,
    engine: Engine | None = None,
) -> IngestReport:
    engine = engine or db_io.get_engine()
    with run_job(
        engine,
        "earnings",
        {"tickers": tickers, "historical": historical, "forward_days": forward_days},
    ) as report:
        rows: list[dict] = []

        if historical:
            cik_lookup = sec.fetch_cik_lookup().set_index("ticker")["cik"]
            for ticker in tickers:
                if ticker in SEC_NON_FILER_TICKERS:
                    continue
                cik = cik_lookup.get(ticker)
                if cik is None:
                    continue
                eight_ks = sec.fetch_8k_dates(int(cik))
                for _, row in eight_ks.iterrows():
                    if not row["filed_on"]:
                        continue
                    rows.append(
                        {
                            "ticker": ticker,
                            "report_date": row["filed_on"],
                            "session": "unknown",
                            "source": "sec_8k",
                            "confidence": "filing_date",
                        }
                    )

        if forward_days > 0:
            start = date.today()
            end = start + timedelta(days=forward_days)
            # **One walk of the window, not one request per ticker.** The
            # endpoint is bulk -- omitting `symbol` returns every listing in
            # the range -- and this asked it 1,470 times. At
            # `RATE_LIMIT_PER_SEC = 0.8` that is 30.6 minutes of rate
            # limiting alone, and 43.5 minutes measured end to end on
            # 2026-08-26. The chunked walk covers 90 days in ~33 seconds.
            #
            # It is also *more* complete than a single bulk call, which
            # silently truncates at 1,500 rows: that response omitted AAPL
            # entirely. `fetch_forward_calendar_many` splits any window that
            # comes back at the cap, and returned 5,149 rows across 4,945
            # tickers over the same 90 days.
            calendar = finnhub.fetch_forward_calendar_many(start, end, tickers)
            for _, row in calendar.iterrows():
                session = {"bmo": "bmo", "amc": "amc"}.get(str(row.get("hour")).lower(), "unknown")
                rows.append(
                    {
                        "ticker": row["ticker"],
                        "report_date": row["date"],
                        "session": session,
                        "source": row["source"],
                        "confidence": row["confidence"],
                    }
                )

        # `earnings` is keyed (ticker, report_date) and duplicates here are
        # structural, not anomalous: a company files several 8-Ks in one day and
        # ADR 036 treats every 8-K date as an earnings date. Postgres rejects an
        # ON CONFLICT whose own proposed rows collide with each other
        # ("cannot affect row a second time"), so collapse before the write.
        #
        # Last wins, which is what the two passes want. The Finnhub forward pass
        # appends after the SEC historical pass and is the only source carrying
        # real session timing — SEC 8-K rows always say 'unknown'. Letting it
        # overwrite the same date keeps the better row.
        deduped = list({(r["ticker"], r["report_date"]): r for r in rows}.values())

        report.rows_written = db_io.upsert(engine, "earnings", deduped, ["ticker", "report_date"])
        report.tickers = sorted({r["ticker"] for r in deduped})
    return report


# ============ validate ============


@dataclass
class ValidationReport:
    reject_counts: pd.DataFrame
    coverage: pd.DataFrame
    clean: bool
    # DESIGN §2.3's missing-bar rule (see `find_missing_bars`): one row per
    # (ticker, missing_date) for a trading day inside that ticker's own
    # observed span with no bar.
    missing_bars: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=["ticker", "missing_date"])
    )
    # Hard rejects whose bar is still absent — the subset of `reject_counts`
    # that still costs data. `reject_counts` keeps reporting every row for
    # the audit trail; this is what `clean` is actually gated on.
    open_rejects: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=["ticker", "d", "severity"])
    )

    # No `data_clean`/`checks_complete` split here. That distinction existed
    # only to separate "the Stooq cross-check found bad data" from "the
    # Stooq cross-check could not run at all" (vendor outage, network
    # block) — two different operator responses behind one boolean. Both
    # remaining checks (`missing_bars`, `open_rejects`) are plain queries
    # against this database: they always run, or the job itself has failed
    # loudly rather than reporting a hollow "complete". There is no longer
    # a "check ran but found nothing" vs. "check silently didn't run" gap
    # for `clean` to paper over, so collapsing back to one boolean is not
    # the same silent-success trap this removal is otherwise guarding
    # against — it is removing a distinction that no longer has a second
    # side.


def run_validate(engine: Engine | None = None) -> ValidationReport:
    engine = engine or db_io.get_engine()

    with engine.connect() as conn:
        reject_counts = pd.read_sql(
            text(
                "SELECT rule, severity, count(*) AS n FROM bar_rejects "
                "GROUP BY rule, severity ORDER BY n DESC"
            ),
            conn,
        )
        coverage = pd.read_sql(
            text(
                "SELECT ticker, count(*) AS n_bars, min(ts) AS first_ts, max(ts) AS last_ts "
                "FROM bars WHERE interval = '1d' GROUP BY ticker ORDER BY ticker"
            ),
            conn,
        )
        # Missing-bar rule (DESIGN §2.3): the full NYSE calendar plus every
        # bar date on file, covering the whole universe (like `coverage`
        # above), not a sample.
        trading_days = pd.read_sql(text("SELECT d FROM trading_days"), conn)
        bar_dates = pd.read_sql(
            text("SELECT ticker, ts::date AS d FROM bars WHERE interval = '1d'"), conn
        )
        # Rejects carry their own dates so `unresolved_rejects` can ask
        # whether each one still costs a bar, rather than trusting the
        # append-only log's row count.
        reject_rows = pd.read_sql(
            text("SELECT ticker, ts::date AS d, severity FROM bar_rejects WHERE ts IS NOT NULL"),
            conn,
        )

    missing_bars = find_missing_bars(bar_dates, trading_days)
    open_rejects = unresolved_rejects(
        reject_rows, bar_dates, date.fromisoformat(SplitParams().ingest_start)
    )

    # Gated on rejects that still cost a bar, not on every row the
    # append-only log ever recorded — see `unresolved_rejects`.
    clean = open_rejects.empty and missing_bars.empty

    return ValidationReport(
        reject_counts=reject_counts,
        coverage=coverage,
        clean=clean,
        missing_bars=missing_bars,
        open_rejects=open_rejects,
    )


def print_validation_report(report: ValidationReport) -> None:
    table = Table(title="bar_rejects by rule")
    table.add_column("rule")
    table.add_column("severity")
    table.add_column("count", justify="right")
    for _, row in report.reject_counts.iterrows():
        table.add_row(row["rule"], row["severity"], str(row["n"]))
    console.print(table)

    cov = Table(title="coverage by ticker")
    cov.add_column("ticker")
    cov.add_column("n_bars", justify="right")
    cov.add_column("first_ts")
    cov.add_column("last_ts")
    for _, row in report.coverage.iterrows():
        cov.add_row(row["ticker"], str(row["n_bars"]), str(row["first_ts"]), str(row["last_ts"]))
    console.print(cov)

    if not report.missing_bars.empty:
        console.print(
            f"[red]Missing bars on {len(report.missing_bars)} NYSE trading day(s) "
            f"across {report.missing_bars['ticker'].nunique()} ticker(s):[/red]"
        )
        console.print(report.missing_bars)
    else:
        console.print("[green]No missing bars inside any ticker's observed span[/green]")

    # Separating these two numbers is the point: the log records every
    # rejection ever made, but only the ones whose bar is still absent are
    # a live data problem.
    n_logged = int(
        report.reject_counts.loc[report.reject_counts["severity"] == "reject", "n"].sum()
        if not report.reject_counts.empty
        else 0
    )
    if not report.open_rejects.empty:
        console.print(
            f"[red]{len(report.open_rejects)} hard reject(s) still missing a bar "
            f"(of {n_logged} logged):[/red]"
        )
        console.print(report.open_rejects)
    else:
        console.print(
            f"[green]No unresolved hard rejects "
            f"({n_logged} logged, all since refetched or outside the ingest window)[/green]"
        )

    if report.clean:
        console.print("[green]validation clean[/green]")
    else:
        console.print("[red]validation NOT clean[/red]")


# ============ backfill ============


@dataclass
class BackfillReport:
    steps: list[IngestReport]
    validation: ValidationReport


def run_backfill(
    tickers: list[str],
    start: date,
    through_validate: bool = False,
    engine: Engine | None = None,
) -> BackfillReport:
    """Orchestrates the dependency graph in DESIGN §4.11:

    trading_days, tickers ─┬─▶ bars ──▶ (indicators, downstream)
    corporate_actions ─────┘
    market_days
    shares_outstanding
    earnings
    """
    engine = engine or db_io.get_engine()
    end = date.today()
    steps: list[IngestReport] = []

    steps.append(run_calendar(end.year, engine=engine))
    steps.append(ensure_tickers(tickers, engine=engine))
    steps.append(run_bars_daily(tickers, start, end, engine=engine))
    steps.append(run_actions(tickers, engine=engine))
    steps.append(run_market(lookback_days=(end - start).days, engine=engine))

    validation = run_validate(engine=engine)
    # `through_validate` is the documented stopping point (DESIGN §4.11): a
    # human reads the report before `indicators`/`events` (session 6) run
    # against it. Both jobs land here for now since neither exists yet.
    return BackfillReport(steps=steps, validation=validation)
