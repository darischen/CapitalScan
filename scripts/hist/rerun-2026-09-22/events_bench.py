"""Time `cscan events` on a fixed ticker set and fingerprint what it wrote.

Runs against `capitalscan_hist` only. The fingerprint covers every column a
change could plausibly alter EXCEPT provenance (`run_id`, `git_sha`,
`created_at`), so a speedup that changes one number fails loudly instead of
passing on wall-clock alone.

    python events_bench.py baseline     # time + fingerprint, save as baseline
    python events_bench.py after        # time + fingerprint, compare
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["DATABASE_URL_RESEARCH"] = "postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
os.environ["DATABASE_URL_SERVING"] = ""
os.environ["CAPSCAN_SPLITS"] = '{"ingest_start":"1999-01-01","event_start":"2002-01-01"}'
os.environ["CAPSCAN_UNIVERSE"] = (
    '{"required_criteria":["crit_above_sma200","crit_sma200_slope","crit_rel_return"]}'
)

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from capitalscan.jobs import db_io  # noqa: E402
from capitalscan.jobs.config import config_hash, resolve_config  # noqa: E402

HERE = Path(__file__).resolve().parent
TICKERS = os.environ.get("BENCH_TICKERS", "AAPL,MSFT,JPM,XOM,PG")
CHASH = config_hash(resolve_config())

STABLE_COLS = (
    "ticker, signal_date, signal_type, signal_types_all, entry_kind, side, in_trade, "
    "in_watch, watch_reason, split_key, touch_level, close, bb_mid, bb_pctb, "
    "bb_width_pct, k_full, d_full, k_fast, k_cross_up, k_cross_down, atr_14, "
    "rv_pct_252d, dd_52w, sma200_slope_60, above_sma200, vol_z_20d, days_to_earnings, "
    "vix_close, spx_ret_1d, dd_bucket, bw_regime, era, signal_strength"
)


def fingerprint(engine) -> tuple[int, str]:
    sql = (
        f"SELECT {STABLE_COLS} FROM events "  # noqa: S608 - fixed column list
        "WHERE config_hash = :c AND ticker = ANY(:t) "
        "ORDER BY ticker, signal_date, signal_type, entry_kind"
    )
    with engine.connect() as conn:
        frame = pd.read_sql(text(sql), conn, params={"c": CHASH, "t": TICKERS.split(",")})
    payload = frame.to_csv(index=False, float_format="%.6f").encode()
    return len(frame), hashlib.sha256(payload).hexdigest()[:16]


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    repo = HERE.parents[2]  # <repo>/scripts/hist/rerun-2026-09-22/

    t0 = time.perf_counter()
    proc = subprocess.run(
        ["uv", "run", "cscan", "events", "--lookback", "10200", "--tickers", TICKERS]
        + (["--workers", os.environ["BENCH_WORKERS"]] if os.environ.get("BENCH_WORKERS") else []),
        cwd=repo,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        return 1

    rows, digest = fingerprint(db_io.get_engine())
    record = {"label": label, "seconds": round(elapsed, 1), "rows": rows, "sha": digest}
    print(json.dumps(record))

    path = HERE / os.environ.get("BENCH_BASE", "events_bench_baseline.json")
    if label == "baseline":
        path.write_text(json.dumps(record, indent=2))
        print(f"saved {path}")
        return 0

    if path.exists():
        base = json.loads(path.read_text())
        speedup = base["seconds"] / elapsed if elapsed else float("inf")
        same = base["rows"] == rows and base["sha"] == digest
        print(f"baseline {base['seconds']}s -> {round(elapsed, 1)}s  ({speedup:.1f}x)")
        print(
            f"rows {base['rows']} -> {rows} | sha {base['sha']} -> {digest} | "
            f"{'IDENTICAL' if same else 'DIFFERENT — investigate before trusting the speedup'}"
        )
        return 0 if same else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
