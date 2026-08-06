"""Session 10 Task 10.3: recompute Session 9's existing event labels from
the `path` table alone. Controlled comparison, not a new computation —
this module must produce the same numbers Session 9's
`research.enrich.path_metrics` produces for the same event, modulo the
one documented exception (`fwd_ret_*d`; see the module-level note below).

**Writes nothing.** `docs/session10.md` §6-§7 require the old label
columns on `events` to stay untouched until the reconciliation gate
(Task 10.4) passes — rollback has to be free. `derive_session9_labels`
returns an in-memory `DataFrame`; nothing in this module executes an
INSERT or UPDATE.

Reads only the `path` table and `events` metadata columns
(`entry_kind`, `holding_days`, `entry_price`, `exit_price`, `side`) —
never `bars`/`indicators` — per `docs/session10.md`'s 10.3 acceptance
criterion.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.core.returns import entry_offset_for, realized_return
from capitalscan.core.types import EntryKind, Side
from capitalscan.research.enrich import _pct_suffix

# Kept equal to `path_reconcile._FLOAT_TOL` by convention, not by import —
# `path_reconcile` already imports from this module, so importing back
# would cycle. See `derive_labels_from_path`'s giveback block for why this
# value, specifically, is the right one to share rather than duplicate
# with a different number.
_GIVEBACK_ROUNDING_TOL = 1.2e-4


def derive_labels_from_path(
    path: pd.DataFrame,
    entry_offset: int,
    holding_days: int | None,
    entry_price: float,
    exit_price: float,
    side: Side,
    max_hold_days: int,
    targets: tuple,
    horizons: tuple,
    capture_ratio_cap: float,
) -> dict:
    """One event's Session-9-shaped label dict, derived from its `path`
    rows (columns: `day_offset`, `favorable`, `adverse`, `terminal`).

    Two windows, translated from entry-anchored to signal-anchored via
    `entry_offset` (see the plan header, "Day-offset anchor"):

    - MFE/MAE: `day_offset` in `[entry_offset+1, entry_offset+holding_days]`
      — the exact analogue of `mfe_mae`'s `[t+1, exit_idx]`, since
      `holding_days == exit_idx + 1` (`core.exits.ExitResult`'s own
      convention).
    - Reachability: `day_offset` in `[entry_offset+1, entry_offset+max_hold_days]`
      — the full window regardless of `holding_days` (DESIGN §5.6).

    `None`/`NaN` (never a fabricated value) when `holding_days is None`
    — the position never resolved, matching `path_metrics`'s own
    `exit_idx is None` branch exactly.

    `fwd_ret_*d` is the one deliberate exception (see the plan header,
    "Price series discipline"): it reads `path.terminal` at
    `day_offset = entry_offset + horizon`, which is entry-price-anchored,
    split-adjusted-close return — not `forward_returns`'s total-return,
    self-referential-to-entry-bar-close convention. It is still computed
    here, unconditionally, so Task 10.4 has something to diff against and
    document as an explained (not silently ignored) difference.

    Task 10.5 additions:
    - `giveback`: peak favorable return minus terminal return at exit.
      Derivable from existing `mfe` and the exit's terminal mark,
      non-negative by construction for favorable peaks.
    """
    by_offset = path.set_index("day_offset")
    out: dict = {}

    if holding_days is None:
        out["mfe"] = float("nan")
        out["mae"] = float("nan")
        out["time_to_mfe"] = None
        out["capture_ratio"] = None
        out["giveback"] = None
        for target in targets:
            suffix = _pct_suffix(target)
            out[f"touched_{suffix}"] = None
            out[f"day_touched_{suffix}"] = None
    else:
        held_offsets = range(entry_offset + 1, entry_offset + holding_days + 1)
        held = by_offset.loc[by_offset.index.isin(held_offsets)].sort_index()

        if held.empty:
            out["mfe"] = float("nan")
            out["mae"] = float("nan")
            out["time_to_mfe"] = None
            out["capture_ratio"] = None
            out["giveback"] = float("nan")
        else:
            out["mfe"] = float(held["favorable"].max())
            out["mae"] = float(held["adverse"].min())
            # `by_offset` is indexed by integer day offset, so `idxmax`
            # returns one of those offsets.
            peak_offset = int(held["favorable"].idxmax())
            out["time_to_mfe"] = int(peak_offset - entry_offset)

            r_exit = realized_return(entry_price, exit_price, side)
            # Matches `research.enrich.path_metrics`'s clamp (both must
            # produce the same number for this module's own reconciliation
            # purpose — see module docstring).
            if out["mfe"] != out["mfe"] or out["mfe"] <= 0:
                out["capture_ratio"] = None
            else:
                ratio = float(r_exit / out["mfe"])
                out["capture_ratio"] = max(-capture_ratio_cap, min(capture_ratio_cap, ratio))

            # Giveback: peak favorable minus terminal return at exit.
            # Non-negative by construction: the peak is always >= the exit value.
            # Null when mfe is NaN.
            #
            # `mfe` is read back from `path`'s `numeric(12,6)` storage while
            # `r_exit` is computed fresh from `entry_price`/`exit_price` at
            # full float64 precision — the same independent-rounding source
            # `path_reconcile._FLOAT_TOL` exists to absorb for `mfe`/`mae`
            # comparisons (see that module's derivation). A `mfe` that
            # rounded down by up to half a `numeric(12,6)` quantum can land
            # fractionally below the unrounded `r_exit`, producing a
            # `giveback` of a few `1e-7` with no real violation behind it
            # (observed live: event with mfe=0.062834, r_exit=0.062834406...,
            # giveback=-4.07e-7). `_GIVEBACK_ROUNDING_TOL` mirrors that same
            # `1.2e-4` value — reused rather than duplicated with a
            # different number so the two tolerances can't drift apart —
            # and clamps the noise to exactly zero rather than writing a
            # spurious negative. Anything past that tolerance still raises:
            # a true negative giveback would mean the peak wasn't actually
            # the peak, a real formula bug worth crashing on.
            if out["mfe"] == out["mfe"]:  # mfe is not NaN
                giveback = out["mfe"] - r_exit
                if giveback < -_GIVEBACK_ROUNDING_TOL:
                    raise AssertionError(
                        f"giveback must be non-negative; got {giveback} "
                        f"(mfe={out['mfe']}, r_exit={r_exit})"
                    )
                out["giveback"] = float(max(giveback, 0.0))
            else:
                out["giveback"] = float("nan")

        reach_offsets = range(entry_offset + 1, entry_offset + max_hold_days + 1)
        reach = by_offset.loc[by_offset.index.isin(reach_offsets)].sort_index()
        for target in targets:
            suffix = _pct_suffix(target)
            # NOT routed through `_breach`: that function rounds its two
            # operands to 4 decimal PLACES before comparing (DESIGN §3.2,
            # "a float artifact below a hundredth of a cent never decides
            # an event") — a rule sized for comparing dollar prices, not
            # return ratios. Applying it here (as an earlier revision of
            # this function did, per a since-corrected final-review fix)
            # rounds `favorable` to the nearest 0.0001 of return — coarse
            # enough that a value 0.000022 below a round-number target
            # (0.019978 vs 0.02, a real reconciliation-run case) rounds
            # UP to exactly the target and is spuriously reported as
            # touched. `favorable` is already quantized once, correctly,
            # at `numeric(12,6)` by the `path` table itself — a second,
            # coarser rounding here is not reproducing Session 9's
            # `_breach(bar["high"], entry*(1+target), ...)` price-level
            # comparison, it is silently changing what gets compared.
            # Plain `>=` at the stored precision is the faithful match.
            touched_mask = reach["favorable"] >= target
            touched_rows = reach.loc[touched_mask]
            if touched_rows.empty:
                out[f"touched_{suffix}"] = False
                out[f"day_touched_{suffix}"] = None
            else:
                first_offset = touched_rows.index.min()
                out[f"touched_{suffix}"] = True
                out[f"day_touched_{suffix}"] = int(first_offset - entry_offset)

    for h in horizons:
        target_offset = entry_offset + h
        if target_offset in by_offset.index:
            out[f"fwd_ret_{h}d"] = float(by_offset.loc[target_offset, "terminal"])
        else:
            out[f"fwd_ret_{h}d"] = float("nan")

    return out


def derive_session9_labels(engine: Engine, config: Config, config_hash: str) -> pd.DataFrame:
    """Read-only: one row per `event_id` for `config_hash`, columns matching
    the Session 9 label family on `events`. See the module docstring —
    this performs no writes.

    Filters by `config_hash`, not `run_id`. `events`'s natural key is
    `(config_hash, ticker, signal_date, signal_type, entry_kind)` — it does
    not include `run_id` — and `db_io.upsert`'s default behavior overwrites
    every non-key column on conflict, `run_id` included. So any later run
    that reuses the same `config_hash` (a sweep cell matching the default
    config, a live `events`/poll job, a rerun) silently relabels the
    `run_id` on rows a prior run wrote, while `config_hash` keeps
    identifying the same durable row set regardless of which run last
    touched it. Discovered when reconciling against the Phase 3 gate's
    documented `run_id` returned zero rows — the rows were still there,
    just relabeled under a later run's `run_id` after a same-config sweep
    cell ran.
    """
    with engine.connect() as conn:
        events = pd.read_sql(
            text(
                "SELECT id AS event_id, entry_kind, holding_days, entry_price, "
                "exit_price, side FROM events WHERE config_hash = :config_hash"
            ),
            conn,
            params={"config_hash": config_hash},
        )
        if events.empty:
            return events.assign(**{})
        path_params: dict[str, Any] = {"ids": events["event_id"].tolist()}
        path = pd.read_sql(
            text(
                "SELECT event_id, day_offset, favorable, adverse, terminal "
                "FROM path WHERE event_id = ANY(:ids)"
            ),
            conn,
            params=path_params,
        )

    max_hold_days = config.exits.max_hold_days
    targets = config.stats.reach_targets
    horizons = config.stats.fwd_ret_horizons

    # Grouped once, outside the loop, rather than `path.loc[path["event_id"]
    # == ev["event_id"]]` inside it — the latter rescans the whole `path`
    # frame per event (O(events * len(path))), which matters once `path`
    # holds the full 11-day window for every event in a run (finding #8 of
    # the final review).
    empty_path = path.iloc[0:0]
    path_by_event = {eid: grp for eid, grp in path.groupby("event_id")}

    rows = []
    for _, ev in events.iterrows():
        event_path = path_by_event.get(ev["event_id"], empty_path)
        holding_days = None if pd.isna(ev["holding_days"]) else int(ev["holding_days"])
        labels = derive_labels_from_path(
            path=event_path,
            entry_offset=entry_offset_for(EntryKind(ev["entry_kind"])),
            holding_days=holding_days,
            entry_price=float(ev["entry_price"]),
            exit_price=float(ev["exit_price"]) if not pd.isna(ev["exit_price"]) else float("nan"),
            side=Side(ev["side"]),
            max_hold_days=max_hold_days,
            targets=targets,
            horizons=horizons,
            capture_ratio_cap=config.exits.capture_ratio_cap,
        )
        labels["event_id"] = ev["event_id"]
        rows.append(labels)

    return pd.DataFrame(rows)
