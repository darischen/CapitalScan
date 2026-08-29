#!/usr/bin/env python
"""Exit-parameter sweep: target x stop, 11 arms (docs/BACKLOG.md).

**Select on `net_ret`, NOT on `q_value` -- and this file said the wrong
thing until 2026-08-28.** `cell_stats` cannot answer an exit-policy
question. `hit_flags` computes `p_hit` from `fwd_ret_{horizon}d`, the raw
forward return over a fixed window, which is a property of the market and
not of the exit policy. Measured on the first swept arm: `avg(fwd_ret_5d)`
was **-0.00019 on both** the baseline and `t4_fix2`, identical, while
`net_ret` moved from +0.00040 to -0.00030 and the stop-out rate went from
33.0% to 53.9%. Selecting on `q_value` would have compared eleven arms on a
number blind to the only thing being swept.

The responsive metrics are `events.net_ret`, the `exit_reason` mix, and
`benchmarks` (signal against its own null *under that exit policy*), which
is the rigorous version and is worth running on the finalists rather than on
all eleven at ~32 min each.

`cell_stats` is still computed per arm -- it is ~2 min and `n_eff`/`rho`
remain worth having -- but it is not the selection metric.

**Selection still happens on `train` only.** Each arm's `validate` numbers
are computed and stored, and the arm to keep is chosen from `train`, then
looked at once on `validate`. Choosing the best of eleven by their validate
numbers is eleven comparisons kept at their luckiest, which is how a sweep
manufactures an edge that is not there. ADR 059 gates a sweep on the default
config having passed the harness first; it has, and `t4_fix2` passed it too.

**Arms are driven by `config.toml`, not by editing `core/config.py`.**
`resolve_config` layers CLI > env > `config.toml` > dataclass defaults, so an
arm is a file this script writes and deletes. Three things that buys:

- `core/config.py` is never touched, so the failure in 77cb5ee -- an arm's
  value committed to main by `git add -A` -- cannot recur. `config.toml` is
  gitignored.
- The restore is one `unlink` in a `finally`, so an ordinary Ctrl-C leaves
  the tree at baseline. A hard kill does **not** run it -- verified on
  2026-08-28, where killing the runner left `config.toml` behind and the
  tree resolving `f7b31c5443d30948`. That is what the next bullet is for.
- `scripts/run_nightly.ps1` refuses to run when the resolved hash is not
  `a38d3ca6b58295e8`. If this script dies without cleaning up, the next
  nightly stops instead of writing events under an arm's hash. That guard is
  why a stray file is survivable rather than silent.

**The universe is copied, not recomputed** -- and that is a correctness
choice, not only a saving. Arms must share arm 1's universe, or the
difference between them is not only the exit parameters. Recomputing per arm
today would hand every later arm a *newer* universe than arm 1, including this
morning's IESC split repair, and confound every comparison the sweep exists
to make. It also saves 25 minutes an arm.

The claim it rests on -- that `ExitParams` cannot reach `universe` -- is
proven before any arm runs, by evaluating one quarter twice at the same
moment under two configs differing only in exit parameters. See
`verify_exit_params_do_not_affect_universe`, including what its first,
wrong version measured instead.

**`benchmarks` is deferred.** It is ~32 min per arm and answers "did this beat
its null", which only matters for an arm that survives FDR at all. Run it
afterwards on whatever is left standing.

Roughly 2h40m per arm. Eleven arms, of which `t4_atr15` is the baseline and
already computed, so a full pass is 10 and ~27h. Resumable: an arm holding
`cell_stats` for both splits is skipped, so a second invocation picks up only
what is missing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_TOML = ROOT / "config.toml"

# **Resolved from the running interpreter, not assumed to be `ROOT/.venv`.**
# `uv` places the environment wherever `UV_PROJECT_ENVIRONMENT` says, and on
# the laptop it had to go outside the user profile entirely: uv's own Python
# had landed in `AppData\Roaming`, which an SSH session's restricted token
# cannot traverse -- every call died with `os error 448`,
# ERROR_UNTRUSTED_MOUNT_POINT. Hardcoding `.venv` made this script
# unrunnable there while `uv run` worked fine, which is a confusing failure
# to debug from the other end of an ssh pipe.
#
# `sys.executable` is the environment actually in use, and `cscan.exe` sits
# beside it in both layouts.
VENV_BIN = Path(sys.executable).parent
CSCAN = VENV_BIN / "cscan.exe"
PYTHON = Path(sys.executable)
BASELINE_HASH = "a38d3ca6b58295e8"
CHUNK_SIZE = "24"  # identical across restarts, or every chunk re-runs
WORKERS = "8"


@dataclass(frozen=True)
class Arm:
    name: str
    target_pct: float
    stop_mode: str
    stop_value: float  # ATR k, or fixed pct

    @property
    def is_default(self) -> bool:
        """True for the arm that reproduces `ExitParams`' own defaults.

        Read from the dataclass rather than hardcoded, so a change to the
        defaults cannot leave this claiming the wrong arm is the baseline.
        """
        from capitalscan.core.config import ExitParams

        d = ExitParams()
        return (
            self.target_pct == d.target_pct
            and self.stop_mode == d.stop_mode
            and self.stop_value == (d.stop_atr_k if d.stop_mode == "atr" else d.stop_fixed_pct)
        )

    def toml(self) -> str:
        lines = [
            "[exits]",
            f"target_pct = {self.target_pct}",
            f'stop_mode = "{self.stop_mode}"',
        ]
        if self.stop_mode == "atr":
            lines.append(f"stop_atr_k = {self.stop_value}")
        else:
            lines.append(f"stop_fixed_pct = {self.stop_value}")
        return "\n".join(lines) + "\n"


def _arm_name(target: float, mode: str, value: float) -> str:
    tag = "atr15" if mode == "atr" else f"fix{int(round(value * 100))}"
    return f"t{int(round(target * 100))}_{tag}"


# **The grid is not a full factorial, deliberately** (user's decision,
# 2026-08-28 after the restart).
#
# `t4_atr15` is the live config -- 4% target, ATR stop at k=1.5 -- so arm 1
# already computed it and it costs nothing. It is the reference every other
# arm is read against.
#
# `t4_fix2` is kept because it is the **only arm that changes exactly one
# knob** from the live config: same target, a 2% stop instead of ATR. That
# is the clean answer to "does a tighter stop help?", and every other arm
# moves two things at once.
#
# `t4_fix3` is dropped. It would only refine the stop axis at a target we
# are not proposing to keep, and the same axis is covered three times over
# at 5/6/7%.
#
# The 5/6/7% rows are full 3x3, so stop x target interaction is still
# measurable there, and the target axis reads along the ATR column against
# arm 1.
#
# 0.07 was added on the user's decision: 74.6% of 4% exits go on to touch
# 5% but only 18.0% reach 10%, so the decay sits between and 6-7% is inside
# it.
ARMS = [
    Arm("t4_atr15", 0.04, "atr", 1.5),  # = arm 1, already computed
    Arm("t4_fix2", 0.04, "fixed", 0.02),  # the one-knob-changed arm
    *(
        Arm(_arm_name(t, m, v), t, m, v)
        for t in (0.05, 0.06, 0.07)
        for m, v in (("atr", 1.5), ("fixed", 0.02), ("fixed", 0.03))
    ),
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(*args: str) -> None:
    log("$ cscan " + " ".join(args))
    proc = subprocess.run([str(CSCAN), *args], cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"ABORT: cscan {' '.join(args)} exited {proc.returncode}")


def resolved_hash() -> str:
    out = subprocess.run(
        [
            str(PYTHON),
            "-c",
            "from capitalscan.jobs.config import config_hash, resolve_config;"
            "print(config_hash(resolve_config()))",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _engine():
    sys.path.insert(0, str(ROOT))
    from capitalscan.jobs import db_io

    return db_io.get_engine()


def already_done(chash: str) -> bool:
    from sqlalchemy import text

    with _engine().connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(DISTINCT split_key) FROM cell_stats "
                "WHERE config_hash = :h AND split_key IN ('train','validate')"
            ),
            {"h": chash},
        ).scalar_one()
    return int(n) >= 2


def copy_universe(chash: str) -> int:
    """Clone the baseline universe under this arm's hash.

    The column list comes from the catalogue rather than being written out,
    so a column added later is carried without editing this script, and
    `config_hash` is substituted rather than copied. `NOT attisdropped` for
    the reason in CLAUDE.md: a dropped column keeps its `pg_attribute` row
    under a placeholder name.
    """
    from sqlalchemy import text

    with _engine().begin() as conn:
        cols = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT a.attname FROM pg_attribute a "
                    "WHERE a.attrelid = 'universe'::regclass AND a.attnum > 0 "
                    "AND NOT a.attisdropped ORDER BY a.attnum"
                )
            )
        ]
        select_list = ", ".join(
            f"'{chash}' AS config_hash" if c == "config_hash" else c for c in cols
        )
        res = conn.execute(
            text(
                f"INSERT INTO universe ({', '.join(cols)}) "  # noqa: S608 - catalogue-derived
                f"SELECT {select_list} FROM universe WHERE config_hash = :base "
                "ON CONFLICT DO NOTHING"
            ),
            {"base": BASELINE_HASH},
        )
        return int(res.rowcount or 0)


def verify_exit_params_do_not_affect_universe(quarter: str) -> None:
    """Prove `ExitParams` cannot change `universe`, by measurement.

    **The first version of this test was wrong and aborted a good sweep.**
    It compared the copied rows against a *fresh* evaluation and found two
    differences on 2019Q2 — IESC, whose market cap had exactly halved, and
    ALL, whose `in_trade` flipped on an unchanged market cap. Neither was
    caused by `ExitParams`. IESC was the stock split repaired earlier the
    same day, and ALL moved because that day's nightly rewrote `shares` and
    `indicators`. The test conflated **config-dependence** with
    **time-dependence**: any fresh evaluation differs from one computed
    weeks earlier, whatever the config.

    This version isolates the variable. It evaluates the same quarter twice,
    at the same moment, from the same inputs, under two configs that differ
    **only** in `ExitParams`. Identical output then means exit parameters
    cannot reach the universe. Measured 2026-08-28: 0 differing rows across
    1,217 tickers.

    **Scratch hashes, deliberately outside the grid** (`target_pct` 0.041 and
    0.042). An earlier run of this check used two real arms and left their
    2019Q2 rows freshly computed while every other quarter was copied — one
    quarter of an arm silently newer than the rest. Both scratch generations
    are deleted afterwards.

    **And this is why copying is right, not merely cheap.** Arms must share
    arm 1's universe or the difference between them is not only the exit
    parameters. Recomputing per arm now would hand arms 2-9 a *newer*
    universe than arm 1 — including today's IESC repair — and quietly
    confound every comparison the sweep exists to make.
    """
    from sqlalchemy import text

    # `--quarter` takes a label ('2019Q2'); `universe.as_of` is the
    # quarter-end date. Passing a date parses `quarter[5]` as the quarter
    # number -- '2019-06-30'[5] is '0', giving `date(2019, 0, 1)` and "month
    # must be in 1..12, not 0". Derived from the job's own function.
    from capitalscan.jobs.compute import _quarter_end

    as_of = _quarter_end(quarter)
    log(f"proving ExitParams cannot affect the universe, on {quarter} ({as_of})")

    hashes: list[str] = []
    for probe in (0.041, 0.042):
        CONFIG_TOML.write_text(
            f'[exits]\ntarget_pct = {probe}\nstop_mode = "fixed"\nstop_fixed_pct = 0.02\n',
            encoding="utf-8",
        )
        h = resolved_hash()
        if h == BASELINE_HASH or any(h == x for x in hashes):
            raise SystemExit(f"ABORT: probe config {probe} produced an unusable hash {h}")
        with _engine().begin() as conn:
            conn.execute(
                text("DELETE FROM universe WHERE config_hash = :h AND as_of = :q"),
                {"h": h, "q": as_of},
            )
        run("universe", "--quarter", quarter)
        hashes.append(h)

    a, b = hashes
    try:
        with _engine().connect() as conn:
            diff = conn.execute(
                text(
                    "SELECT count(*) FROM universe x JOIN universe y "
                    "    ON x.ticker = y.ticker AND x.as_of = y.as_of "
                    " WHERE x.config_hash = :a AND y.config_hash = :b AND x.as_of = :q "
                    "   AND (x.in_trade IS DISTINCT FROM y.in_trade "
                    "     OR x.in_watch IS DISTINCT FROM y.in_watch "
                    "     OR x.mcap_usd IS DISTINCT FROM y.mcap_usd)"
                ),
                {"a": a, "b": b, "q": as_of},
            ).scalar_one()
            counts = conn.execute(
                text(
                    "SELECT count(*) FILTER (WHERE config_hash = :a), "
                    "       count(*) FILTER (WHERE config_hash = :b) "
                    "  FROM universe WHERE as_of = :q AND config_hash IN (:a, :b)"
                ),
                {"a": a, "b": b, "q": as_of},
            ).one()
        n_a, n_b = int(counts[0]), int(counts[1])
        if int(diff) or n_a != n_b or n_a == 0:
            raise SystemExit(
                f"ABORT: two configs differing only in ExitParams produced different "
                f"universes for {quarter} ({int(diff)} differing row(s), {n_a} vs {n_b} "
                f"rows). Copying the universe across arms is invalid -- run a full "
                f"universe pass per arm instead."
            )
        log(f"proven: 0 differing rows across {n_a} tickers; the copy is sound")
    finally:
        with _engine().begin() as conn:
            conn.execute(
                text("DELETE FROM universe WHERE config_hash IN (:a, :b)"),
                {"a": a, "b": b},
            )
        if CONFIG_TOML.exists():
            CONFIG_TOML.unlink()


def run_arm(arm: Arm) -> str:
    """Run one arm and return its config hash.

    The universe proof used to live here, gated on the arm index, and that
    was wrong twice over: `t4_atr15` is skipped as the baseline so the
    check never fired, and the check itself needed no arm at all. It is now
    `verify_exit_params_do_not_affect_universe`, run once by `main`.
    """
    CONFIG_TOML.write_text(arm.toml(), encoding="utf-8")
    chash = resolved_hash()
    # **`t4_atr15` IS the baseline**, target 0.04 with an ATR stop at k=1.5
    # being the current `ExitParams` default, so it resolves to
    # `a38d3ca6b58295e8` and its `cell_stats` already exist. That is the
    # point of including it: the grid gets a reference cell for free, and
    # `already_done` below skips it.
    #
    # For every other arm, resolving to the baseline hash means the override
    # did not take -- a typo'd section name, a `config.toml` that failed to
    # write -- and continuing would silently overwrite the serving
    # generation's own rows. That is worth aborting for.
    if chash == BASELINE_HASH and not arm.is_default:
        raise SystemExit(
            f"ABORT: arm {arm.name} resolved to the baseline hash {BASELINE_HASH}, "
            f"so the config.toml override was ignored. Continuing would overwrite "
            f"the serving generation."
        )
    log(
        f"=== arm {arm.name}: target={arm.target_pct} "
        f"stop={arm.stop_mode}:{arm.stop_value} hash={chash} ==="
    )

    if already_done(chash):
        log(f"arm {arm.name} already has cell_stats for both splits -- skipping")
        return chash

    copied = copy_universe(chash)
    log(f"universe: copied {copied} row(s) from {BASELINE_HASH}")

    run("backtest", "--workers", WORKERS, "--chunk-size", CHUNK_SIZE, "--phase", "compute")
    run("backtest", "--workers", WORKERS, "--phase", "finalize")
    run("backtest", "--workers", WORKERS, "--phase", "harness")
    # Both were missing from rebuild_arms_2_3.sh and fail three steps later
    # with "cannot convert float NaN to integer", naming neither cause.
    run("path", "backfill", "--config-hash", chash, "--workers", WORKERS)
    run("path", "peak-labels", "--config-hash", chash)
    run("stats", "rho", "--config-hash", chash)
    run("stats", "cells", "--config-hash", chash, "--split-key", "train")
    run("stats", "cells", "--config-hash", chash, "--split-key", "validate")
    log(f"=== arm {arm.name} complete ({chash}) ===")
    return chash


def main() -> None:
    ap = argparse.ArgumentParser(description="Exit-parameter sweep (target x stop).")
    ap.add_argument("--start-from", default=None, help="arm name to resume at")
    ap.add_argument("--only", default=None, help="run a single arm by name")
    ap.add_argument(
        "--arms",
        default=None,
        help="comma-separated arm names, run in the order given. Used to split the "
        "grid across machines; each box takes a disjoint subset and `already_done` "
        "makes an overlap harmless rather than duplicated work.",
    )
    ap.add_argument("--no-verify", action="store_true", help="skip the ExitParams/universe proof")
    ap.add_argument("--verify-quarter", default="2019Q2", help="quarter LABEL, e.g. 2019Q2")
    ap.add_argument("--list", action="store_true", help="print the grid and exit")
    args = ap.parse_args()

    arms = ARMS
    if args.list:
        for a in arms:
            print(f"{a.name:14s} target={a.target_pct}  stop={a.stop_mode}:{a.stop_value}")
        return
    if args.arms:
        wanted = [x.strip() for x in args.arms.split(",") if x.strip()]
        by_name = {a.name: a for a in ARMS}
        missing = [w for w in wanted if w not in by_name]
        if missing:
            sys.exit(f"no arm(s) named: {', '.join(missing)}. See --list.")
        arms = [by_name[w] for w in wanted]
    elif args.only:
        arms = [a for a in arms if a.name == args.only]
        if not arms:
            sys.exit(f"no arm named {args.only}")
    elif args.start_from:
        names = [a.name for a in ARMS]
        if args.start_from not in names:
            sys.exit(f"no arm named {args.start_from}")
        arms = ARMS[names.index(args.start_from) :]

    if CONFIG_TOML.exists():
        sys.exit(f"ABORT: {CONFIG_TOML} already exists; a previous run may not have cleaned up.")

    done: list[tuple[str, str]] = []
    try:
        if not args.no_verify:
            verify_exit_params_do_not_affect_universe(args.verify_quarter)
        for arm in arms:
            done.append((arm.name, run_arm(arm)))
    finally:
        # Unconditional: an interrupt must not leave an arm resolving.
        if CONFIG_TOML.exists():
            CONFIG_TOML.unlink()
        back = resolved_hash()
        log(f"config restored -> {back}")
        if back != BASELINE_HASH:
            log(f"WARNING: expected {BASELINE_HASH}. Nightly will refuse until this is fixed.")
        for name, chash in done:
            log(f"  completed: {name}  {chash}")


if __name__ == "__main__":
    main()
