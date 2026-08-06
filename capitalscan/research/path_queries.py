"""Session 10 Task 10.5: the threshold x horizon x direction label grid,
derived from `path` on demand.

`docs/session10.md` §10.5 is explicit that these are **queries against the
path table, not materialized columns**: the grid is `4 x 5 x 2 = 40`
touched flags plus 40 first-touch days with today's config, one new
threshold adds ~10 cells and one new horizon ~16, and materializing that
makes every future config change a migration. Session 9's existing
`touched_*pct` / `day_touched_*pct` / `fwd_ret_*d` columns stay on `events`
as the hot serving cache (ADR 094); everything else is computed here.

**Writes nothing**, same as `path_labels`. Every function takes a `path`
frame (or an engine to read one) and returns values.

Three families, matching §0:

1. Reachability at any (threshold, horizon, direction) — `touched_by`.
2. First-touch day for the same grid — `first_touch_day`.
3. Terminal mark at a horizon — `terminal_at`. **Not** a forward return:
   see that function's docstring, it is the one place a caller can take the
   wrong price series without noticing.

Direction. §0 requires the path store to be direction-neutral, "so both
tails exist for every event regardless of signal type". `path.favorable`
and `path.adverse` already are: `favorable` is the best excursion in the
event's own direction (long or short) and `adverse` the worst, both signed
relative to entry. `Direction.ADVERSE` therefore means "the threshold was
breached against the position", tested as `adverse <= -target`, with
`target` staying a positive magnitude on both sides. Session 9 materialized
the favorable tail only; the adverse tail has never had labels before this
module.

Three-valued touched, not two-valued. An event whose window is too short
to cover the horizon and has not touched by its last observed day returns
`None`, never `False` — "not touched within 5 days" and "we have only seen
3 days" are different statements, and invariant 4's never-fabricate rule
applies to a label exactly as it applies to a price. A touch already
observed inside the available days returns `True` regardless of window
length, since a later missing day cannot un-touch it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.core.returns import entry_offset_for
from capitalscan.core.types import EntryKind
from capitalscan.research.enrich import _pct_suffix


class Direction(str, Enum):
    """Which tail of the path a threshold is tested against."""

    FAVORABLE = "favorable"
    ADVERSE = "adverse"


def _window(path: pd.DataFrame, entry_offset: int, horizon: int) -> pd.DataFrame:
    """The `[entry+1, entry+horizon]` slice of `path`, in `day_offset` order.

    `path.day_offset` is anchored to `signal_date` while every label is
    entry-anchored, so the horizon translates through `entry_offset` — the
    same translation `path_labels.derive_labels_from_path` applies, for the
    same reason (`core.returns.entry_offset_for`). Getting this wrong is a
    silent off-by-one for `NEXT_OPEN` events only, which is exactly the
    shape of bug that survives a spot check on `touch` events.
    """
    wanted = range(entry_offset + 1, entry_offset + horizon + 1)
    return path.loc[path["day_offset"].isin(wanted)].sort_values("day_offset")


def _breaches(window: pd.DataFrame, target: float, direction: Direction) -> pd.Series:
    """Boolean mask of days where `target` was breached in `direction`.

    Plain `>=` / `<=` at the stored `numeric(12,6)` precision, deliberately
    not routed through `core.signals._breach`: that function rounds to 4
    decimal *places* for comparing dollar prices (DESIGN §3.2), which
    applied to a return ratio rounds to the nearest 0.0001 of return and
    reports a value 0.000022 below a round-number target as touched. That
    was a real bug in the Session 9 comparison path, fixed in
    `path_labels`; this module must not reintroduce it.
    """
    if direction is Direction.FAVORABLE:
        return window["favorable"] >= target
    return window["adverse"] <= -target


def touched_by(
    path: pd.DataFrame,
    target: float,
    horizon: int,
    direction: Direction,
    entry_offset: int,
) -> bool | None:
    """Was `target` breached in `direction` within `horizon` trading days
    of entry? `None` when the window is too short to answer (see the module
    docstring's three-valued note)."""
    window = _window(path, entry_offset, horizon)
    if _breaches(window, target, direction).any():
        return True
    return False if len(window) >= horizon else None


def first_touch_day(
    path: pd.DataFrame,
    target: float,
    horizon: int,
    direction: Direction,
    entry_offset: int,
) -> int | None:
    """Trading days from entry to the first breach of `target`, or `None`.

    `None` covers both "never touched inside the window" and "window too
    short to know", per §10.5's "null means untouched, never zero" — day 0
    is not a legal answer, since the window starts at entry+1.
    """
    window = _window(path, entry_offset, horizon)
    touched = window.loc[_breaches(window, target, direction)]
    if touched.empty:
        return None
    return int(touched["day_offset"].min() - entry_offset)


def grid_column_names(config: Config) -> list[str]:
    """Every column `reach_grid` produces for `config`, in a stable order.

    Names route through `enrich._pct_suffix` rather than formatting the
    threshold here — `0.10 * 100` is `10.000000000000002` in binary
    floating point, and a naive f-string produces `touched_10.0000...pct`.
    §10.5's acceptance criterion names this specifically.
    """
    names = []
    for direction in Direction:
        prefix = "" if direction is Direction.FAVORABLE else "adverse_"
        for target in config.stats.reach_targets:
            suffix = _pct_suffix(target)
            for h in config.stats.fwd_ret_horizons:
                names.append(f"{prefix}touched_{suffix}_by_{h}d")
                names.append(f"{prefix}first_touch_{suffix}_by_{h}d")
    return names


def reach_grid(path: pd.DataFrame, entry_offset: int, config: Config) -> dict:
    """The full grid for one event: `{column_name: value}` over every
    (direction, threshold, horizon) cell in `config`.

    Adding a threshold to `StatsParams.reach_targets` or a horizon to
    `fwd_ret_horizons` widens this dict with no code change here — the
    §4 gate's item 4 ("adding a threshold requires only a config change and
    a re-run") is a property of this loop, and
    `test_path_queries.py::test_adding_a_threshold_widens_the_grid_with_no_code_change`
    is what holds it.
    """
    out: dict = {}
    for direction in Direction:
        prefix = "" if direction is Direction.FAVORABLE else "adverse_"
        for target in config.stats.reach_targets:
            suffix = _pct_suffix(target)
            for h in config.stats.fwd_ret_horizons:
                out[f"{prefix}touched_{suffix}_by_{h}d"] = touched_by(
                    path, target, h, direction, entry_offset
                )
                out[f"{prefix}first_touch_{suffix}_by_{h}d"] = first_touch_day(
                    path, target, h, direction, entry_offset
                )
    return out


def terminal_at(path: pd.DataFrame, horizon: int, entry_offset: int) -> float | None:
    """`path.terminal` at `entry + horizon`, or `None` if that day is
    missing.

    **This is not the forward return.** `path.terminal` is split-adjusted
    close expressed against `entry_price`; `events.fwd_ret_*d` is
    total-return `adj_close`, self-referential to the entry bar's own close
    (`core.returns.forward_returns`, DESIGN §2.2). The two disagree on
    245,475 of 246,134 events in the real run, entirely by design, which is
    why `path_reconcile.EXPLAINED_COLUMNS` marks the whole `fwd_ret_*d`
    family explained rather than matching it.

    Phase 4 reads `events.fwd_ret_*d` for return measurement (BUILD.md,
    "Label source contract"). Use this function for path-shape questions —
    where the mark sat on day 3 relative to entry — never as a return.
    """
    row = path.loc[path["day_offset"] == entry_offset + horizon, "terminal"]
    return None if row.empty else float(row.iloc[0])


def reach_grid_for_config(engine: Engine, config: Config, config_hash: str) -> pd.DataFrame:
    """Read-only: one row per `event_id` under `config_hash`, columns per
    `grid_column_names(config)`.

    Filters by `config_hash`, never `run_id` — `events`'s natural key does
    not include `run_id` and `db_io.upsert` relabels it on conflict, so a
    `run_id` lookup silently returns the wrong row set once any later run
    reuses the config. `path_labels.derive_session9_labels`'s docstring has
    the full account of how that produced a false PASS.
    """
    with engine.connect() as conn:
        events = pd.read_sql(
            text("SELECT id AS event_id, entry_kind FROM events WHERE config_hash = :config_hash"),
            conn,
            params={"config_hash": config_hash},
        )
        if events.empty:
            return events
        path_params: dict[str, Any] = {"ids": events["event_id"].tolist()}
        path = pd.read_sql(
            text(
                "SELECT event_id, day_offset, favorable, adverse, terminal "
                "FROM path WHERE event_id = ANY(:ids)"
            ),
            conn,
            params=path_params,
        )

    # Grouped once outside the loop, not filtered per event: the per-event
    # filter rescans the whole frame every iteration, which is O(events *
    # len(path)) against a table holding eleven rows for every event in the
    # run. Same fix as finding #8 on `derive_session9_labels`.
    empty_path = path.iloc[0:0]
    path_by_event = {eid: grp for eid, grp in path.groupby("event_id")}

    rows = []
    for _, ev in events.iterrows():
        cells = reach_grid(
            path_by_event.get(ev["event_id"], empty_path),
            entry_offset_for(EntryKind(ev["entry_kind"])),
            config,
        )
        cells["event_id"] = ev["event_id"]
        rows.append(cells)
    return pd.DataFrame(rows)
