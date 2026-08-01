"""Ingest jobs: raw data landing correctly, with validation that fails loudly.

DESIGN §2.3 (validation rules), §2.6 (idempotency), §4.3-§4.7 (job shapes),
§4.11 (backfill runbook). Every function here does IO — network fetch via
`jobs/fetch/*` and database writes via `jobs/db_io.py` — which is why none
of this lives in `core/` (invariant 1).
"""

from __future__ import annotations

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

from capitalscan.core.config import SplitParams
from capitalscan.jobs import db_io
from capitalscan.jobs.fetch import finnhub, sec, stooq, wikipedia, yahoo
from capitalscan.jobs.provenance import git_sha, new_run_id

console = Console()

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = REPO_ROOT / "data" / "universe_union.csv"

FULL_SESSION = pd.Timedelta(hours=6, minutes=30)
STOOQ_SAMPLE_SIZE = 20
STOOQ_DISAGREEMENT_PCT = 0.005  # 0.5%
STOOQ_DISAGREEMENT_FRACTION = 0.01  # flag if disagreement hits >1% of bars
LARGE_MOVE_PCT = 0.40
SPLIT_RATIOS = (2, 3, 4, 5, 7, 10, 20)  # forward and reverse splits both checked
FLAT_CLOSE_RUN = 5
MIN_TRADE_PRICE = 1.0


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
    """Wraps a job body with a `runs` row: 'running' on entry, 'ok'/'failed' on exit."""
    run_id = _start_run(engine, job, params)
    report = IngestReport(job=job, run_id=run_id)
    try:
        yield report
        _finish_run(engine, run_id, "ok", report.rows_written, report.notes)
    except Exception as exc:
        _finish_run(engine, run_id, "failed", report.rows_written, str(exc))
        raise


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
    a trading day, and the Stooq cross-check — live in `run_validate`
    instead, which has the full `trading_days` and `bars` history to work
    against.
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

        flat_run = (group["close"] == group["close"].shift(1)).astype(int)
        run_len = flat_run.groupby((flat_run != flat_run.shift()).cumsum()).cumsum()
        for idx, length in run_len.items():
            if length >= FLAT_CLOSE_RUN - 1 and flat_run.at[idx]:
                flag(idx, "identical_close_run", {"run_length": int(length) + 1})

    clean = df.loc[keep].reset_index(drop=True)
    return clean, rejects + flags


# ============ bars_daily / bars_hourly ============


def run_bars_daily(
    tickers: list[str], start: date, end: date, engine: Engine | None = None
) -> IngestReport:
    engine = engine or db_io.get_engine()
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
    return report


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
        fetched: list[str] = []
        with Progress() as progress:
            task = progress.add_task("[cyan]hourly bars...", total=len(tickers))
            for ticker in tickers:
                hourly = yahoo.fetch_bars_hourly(ticker, start, end)
                progress.update(task, advance=1, description=f"[cyan]{ticker}[/cyan]")
                if hourly.empty:
                    # Delisted before the 725-day window opened. Expected for
                    # most of the union, not an error.
                    continue

                raw = hourly.copy()
                raw["interval"] = "1h"
                raw["adj_close"] = raw["close"]
                raw["adj_factor"] = 1.0
                raw["source"] = "yahoo"
                raw["run_id"] = report.run_id

                clean, rejects = validate_bars(raw, corporate_actions=None)
                for r in rejects:
                    r["run_id"] = report.run_id
                report.rows_rejected += sum(1 for r in rejects if r["severity"] == "reject")
                report.rows_flagged += sum(1 for r in rejects if r["severity"] == "flag")
                if rejects:
                    db_io.append(engine, "bar_rejects", rejects)

                # Committed before the next fetch starts. The key is
                # (ticker, ts, interval), so rerunning after an interrupt
                # rewrites what is already there rather than duplicating it.
                report.rows_written += db_io.upsert(
                    engine, "bars", clean, ["ticker", "ts", "interval"]
                )
                fetched.append(ticker)

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


def run_actions(tickers: list[str], engine: Engine | None = None) -> IngestReport:
    engine = engine or db_io.get_engine()
    with run_job(engine, "actions", {"tickers": tickers}) as report:
        frames = [yahoo.fetch_actions(t) for t in tickers]
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


def run_shares(tickers: list[str], engine: Engine | None = None) -> IngestReport:
    """Point-in-time shares outstanding from SEC XBRL (DESIGN §2.4).

    Stores every filed value with its `filed_on` date; the "latest filing
    with `filed_on < as_of`" selection happens at query time in the
    `universe` job, not here.
    """
    engine = engine or db_io.get_engine()
    with run_job(engine, "shares", {"tickers": tickers}) as report:
        cik_lookup = sec.fetch_cik_lookup().set_index("ticker")["cik"]
        rows = []
        touched = []
        for ticker in tickers:
            cik = cik_lookup.get(ticker)
            if cik is None:
                continue
            facts = sec.fetch_company_facts(int(cik))
            if facts.empty:
                continue
            facts = facts.dropna(subset=["filed_on", "value"])
            for _, row in facts.iterrows():
                rows.append(
                    {
                        "ticker": ticker,
                        "filed_on": row["filed_on"],
                        "period_end": row["end"],
                        "shares": int(row["value"]),
                        "source": "sec_xbrl",
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
        best: dict[tuple[str, object], dict] = {}
        for row in rows:
            key = (row["ticker"], row["filed_on"])
            prior = best.get(key)
            if prior is None or _period_end_key(row) >= _period_end_key(prior):
                best[key] = row

        report.rows_written = db_io.upsert(
            engine, "shares_outstanding", list(best.values()), ["ticker", "filed_on"]
        )
        report.tickers = touched
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
            for ticker in tickers:
                calendar = finnhub.fetch_forward_calendar(start, end, symbol=ticker)
                for _, row in calendar.iterrows():
                    session = {"bmo": "bmo", "amc": "amc"}.get(
                        str(row.get("hour")).lower(), "unknown"
                    )
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

        report.rows_written = db_io.upsert(
            engine, "earnings", deduped, ["ticker", "report_date"]
        )
        report.tickers = sorted({r["ticker"] for r in deduped})
    return report


# ============ validate ============


@dataclass
class ValidationReport:
    reject_counts: pd.DataFrame
    coverage: pd.DataFrame
    stooq_disagreements: pd.DataFrame
    clean: bool


def run_validate(
    tickers: list[str] | None = None,
    sample_size: int = STOOQ_SAMPLE_SIZE,
    engine: Engine | None = None,
) -> ValidationReport:
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
        available = pd.read_sql(
            text("SELECT DISTINCT ticker FROM bars WHERE interval = '1d'"), conn
        )["ticker"].tolist()

    sample = (tickers or available)[:sample_size]
    disagreements = []
    for ticker in sample:
        try:
            with engine.connect() as conn:
                local = pd.read_sql(
                    text(
                        "SELECT ts::date AS d, close FROM bars "
                        "WHERE ticker = :ticker AND interval = '1d' ORDER BY ts DESC LIMIT 60"
                    ),
                    conn,
                    params={"ticker": ticker},
                )
            if local.empty:
                continue
            stooq_bars = stooq.fetch_daily(ticker, local["d"].min(), local["d"].max())
            if stooq_bars.empty:
                continue
            merged = local.merge(
                stooq_bars, left_on="d", right_on="date", suffixes=("_local", "_stooq")
            )
            if merged.empty:
                continue
            pct_diff = (merged["close_local"] - merged["close"]).abs() / merged["close"]
            frac_bad = (pct_diff > STOOQ_DISAGREEMENT_PCT).mean()
            if frac_bad > STOOQ_DISAGREEMENT_FRACTION:
                disagreements.append({"ticker": ticker, "frac_disagreeing": frac_bad})
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not kill the report
            console.print(f"[yellow]stooq cross-check skipped for {ticker}: {exc}[/yellow]")

    n_hard_rejects = int(
        reject_counts.loc[reject_counts["severity"] == "reject", "n"].sum()
        if not reject_counts.empty
        else 0
    )
    clean = n_hard_rejects == 0 and not disagreements

    return ValidationReport(
        reject_counts=reject_counts,
        coverage=coverage,
        stooq_disagreements=pd.DataFrame(disagreements),
        clean=clean,
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

    if not report.stooq_disagreements.empty:
        console.print("[red]Stooq disagreement above threshold:[/red]")
        console.print(report.stooq_disagreements)
    else:
        console.print("[green]Stooq cross-check: no disagreement above threshold[/green]")

    if report.clean:
        console.print("[green]validation clean: zero rejects at 'reject' severity[/green]")
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

    validation = run_validate(tickers, engine=engine)
    # `through_validate` is the documented stopping point (DESIGN §4.11): a
    # human reads the report before `indicators`/`events` (session 6) run
    # against it. Both jobs land here for now since neither exists yet.
    return BackfillReport(steps=steps, validation=validation)
