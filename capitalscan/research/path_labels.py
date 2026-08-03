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

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.core.returns import entry_offset_for, realized_return
from capitalscan.core.signals import _breach
from capitalscan.core.types import Bound, EntryKind, Side
from capitalscan.research.enrich import _pct_suffix


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
    """
    by_offset = path.set_index("day_offset")
    out: dict = {}

    if holding_days is None:
        out["mfe"] = float("nan")
        out["mae"] = float("nan")
        out["time_to_mfe"] = None
        out["capture_ratio"] = None
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
        else:
            out["mfe"] = float(held["favorable"].max())
            out["mae"] = float(held["adverse"].min())
            peak_offset = held["favorable"].idxmax()
            out["time_to_mfe"] = int(peak_offset - entry_offset)

            r_exit = realized_return(entry_price, exit_price, side)
            out["capture_ratio"] = (
                None if out["mfe"] != out["mfe"] or out["mfe"] <= 0 else float(r_exit / out["mfe"])
            )

        reach_offsets = range(entry_offset + 1, entry_offset + max_hold_days + 1)
        reach = by_offset.loc[by_offset.index.isin(reach_offsets)].sort_index()
        for target in targets:
            suffix = _pct_suffix(target)
            # Routed through `_breach`, the one band comparison in the repo
            # (invariant 2), matching `research.enrich.path_metrics`'s own
            # reachability check. `favorable` is already direction-neutral
            # (always "distance moved in the favorable direction for this
            # side," as a return fraction), so the comparison is always
            # `Bound.UPPER` ("at or above target") regardless of `side` —
            # `side` already got baked into `favorable`'s sign upstream in
            # `core.returns.mfe_mae`/`path_for_event`.
            touched_mask = reach["favorable"].apply(
                lambda v: _breach(float(v), target, Bound.UPPER)
            )
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


def derive_session9_labels(engine: Engine, config: Config, run_id: str) -> pd.DataFrame:
    """Read-only: one row per `event_id` in `run_id`, columns matching the
    Session 9 label family on `events`. See the module docstring — this
    performs no writes.
    """
    with engine.connect() as conn:
        events = pd.read_sql(
            text(
                "SELECT id AS event_id, entry_kind, holding_days, entry_price, "
                "exit_price, side FROM events WHERE run_id = :run_id"
            ),
            conn,
            params={"run_id": run_id},
        )
        if events.empty:
            return events.assign(**{})
        event_ids = events["event_id"].tolist()
        path = pd.read_sql(
            text(
                "SELECT event_id, day_offset, favorable, adverse, terminal "
                "FROM path WHERE event_id = ANY(:ids)"
            ),
            conn,
            params={"ids": event_ids},
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
        )
        labels["event_id"] = ev["event_id"]
        rows.append(labels)

    return pd.DataFrame(rows)
