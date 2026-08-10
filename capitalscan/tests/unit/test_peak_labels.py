"""ADR 093's peak-within-horizon family: the Python reference in
`path_labels.derive_labels_from_path` and the set-based SQL in
`research/peak_labels.py`.

Two implementations exist on purpose (see `peak_labels.py`'s module
docstring): the SQL is the product, the Python is the oracle. The pairing
is only safe if something fails when they diverge, which is what
`TestSqlMatchesPythonReference` is for — it runs the SQL's own arithmetic
against the Python function on the same fixture, in-memory, so the
agreement is checked without a database.

The properties worth pinning are the ones that would produce plausible
wrong numbers rather than crashes: monotonicity across horizons, the
completeness gate, and independence from exit policy.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.types import Side
from capitalscan.research.path_labels import derive_labels_from_path
from capitalscan.research.peak_labels import peak_label_sql

HORIZONS = (1, 2, 3, 5, 10)
TARGETS = (0.02, 0.03, 0.05, 0.10)


def _path(
    favorable: list[float], entry_offset: int = 0, first_offset: int | None = None
) -> pd.DataFrame:
    """One event's path rows.

    `first_offset` is deliberately independent of `entry_offset`. Production
    decouples them: `core.returns.path_for_event` numbers `day_offset` from 1
    for every event, because `fwd_window_for_signal` slices from `pos + 1`
    relative to `signal_date` regardless of entry kind, while
    `entry_offset_for(NEXT_OPEN)` is 1. So a real `next_open` event has a
    `day_offset = 1` row sitting *before* its entry, outside every label
    window by definition.

    Defaulting `first_offset` to `entry_offset + 1` (the old, only behavior)
    made that row impossible to express, which is why the parametrized
    `entry_offset=1` case in `TestSqlMatchesPythonReference` passed
    vacuously while the SQL was summing an out-of-window offset into every
    peak. Pass `first_offset=1, entry_offset=1` for the production shape.
    """
    if first_offset is None:
        first_offset = entry_offset + 1
    return pd.DataFrame(
        {
            "day_offset": [first_offset + i for i in range(len(favorable))],
            "favorable": favorable,
            "adverse": [-abs(f) / 2 for f in favorable],
            "terminal": favorable,
        }
    )


def _derive(
    path: pd.DataFrame,
    entry_offset: int = 0,
    holding_days: int | None = 5,
    exit_price: float | None = None,
) -> dict:
    """`exit_price` defaults to the path's own minimum favorable mark.

    Not arbitrary: `derive_labels_from_path` asserts `giveback = mfe -
    r_exit` is non-negative, and rightly so — a negative giveback would
    mean the peak was not the peak, a real formula bug worth crashing on.
    A fixture pairing a +2% exit with an all-negative path violates that
    by construction and tests nothing but the assertion. Anchoring the
    exit at `min(favorable)` guarantees `r_exit <= mfe` for every path
    shape, so these tests exercise the peak arithmetic rather than
    tripping a guard on an impossible event.
    """
    if exit_price is None:
        floor = float(path["favorable"].min()) if not path.empty else 0.0
        exit_price = 100.0 * (1.0 + floor)
    return derive_labels_from_path(
        path=path,
        entry_offset=entry_offset,
        holding_days=holding_days,
        entry_price=100.0,
        exit_price=exit_price,
        side=Side.LONG,
        max_hold_days=5,
        targets=TARGETS,
        horizons=HORIZONS,
        capture_ratio_cap=100.0,
    )


class TestPeakWithinHorizon:
    def test_peak_is_the_running_maximum_not_the_terminal(self):
        """The whole point of the family: a path that spikes on day 2 and
        falls back must report the spike, not the endpoint."""
        out = _derive(_path([0.01, 0.08, 0.02, 0.01, 0.00, 0.0, 0.0, 0.0, 0.0, 0.0]))
        assert out["peak_ret_2d"] == pytest.approx(0.08)
        assert out["peak_ret_5d"] == pytest.approx(0.08)
        assert out["fwd_ret_5d"] == pytest.approx(0.00)

    def test_monotone_non_decreasing_across_horizons(self):
        """ADR 093 amendment, property 2. A longer window cannot lower a
        maximum, so a fit or a query violating this is a bug, not a
        tolerance."""
        out = _derive(_path([0.01, 0.02, 0.015, 0.03, 0.025, 0.04, 0.01, 0.05, 0.02, 0.06]))
        peaks = [out[f"peak_ret_{h}d"] for h in HORIZONS]
        assert peaks == sorted(peaks)

    def test_peak_is_at_least_the_terminal_at_the_same_horizon(self):
        """ADR 093 amendment, property 1: M_h >= R_h."""
        out = _derive(_path([0.01, 0.05, 0.02, 0.04, 0.03, 0.01, 0.02, 0.01, 0.0, 0.02]))
        for h in HORIZONS:
            assert out[f"peak_ret_{h}d"] >= out[f"fwd_ret_{h}d"]

    def test_negative_peak_is_preserved_not_clamped(self):
        """A position that gapped down and never traded back above entry
        has a genuinely negative peak. ADR 089 makes the same point for
        MFE; clamping here would fabricate a favorable excursion that never
        happened (invariant 4)."""
        falling = [-0.02, -0.01, -0.03, -0.015, -0.04, -0.02, -0.05, -0.03, -0.06, -0.04]
        out = _derive(_path(falling))
        assert out["peak_ret_1d"] == pytest.approx(-0.02)
        assert out["peak_ret_5d"] == pytest.approx(-0.01)


class TestCompletenessGate:
    def test_incomplete_window_is_null_not_a_partial_maximum(self):
        """Three days of path cannot answer a 5- or 10-day question. A
        partial max is a smaller number wearing a complete label — the
        staleness class ADR 094 exists to prevent."""
        out = _derive(_path([0.01, 0.02, 0.03]))
        assert out["peak_ret_1d"] == pytest.approx(0.01)
        assert out["peak_ret_3d"] == pytest.approx(0.03)
        assert pd.isna(out["peak_ret_5d"])
        assert pd.isna(out["peak_ret_10d"])

    def test_next_open_offset_shifts_the_whole_window(self):
        """`path.day_offset` counts from signal_date while the label is
        entry-anchored, so a NEXT_OPEN event's 10-day horizon sits at
        offset 11 (ADR 094's window derivation)."""
        out = _derive(_path([0.01, 0.09, 0.02], entry_offset=1), entry_offset=1)
        assert out["peak_ret_2d"] == pytest.approx(0.09)
        assert pd.isna(out["peak_ret_5d"])

    def test_empty_path_yields_all_null(self):
        empty = pd.DataFrame(columns=["day_offset", "favorable", "adverse", "terminal"])
        out = _derive(empty, holding_days=None)
        for h in HORIZONS:
            assert pd.isna(out[f"peak_ret_{h}d"])


class TestIndependenceFromExitPolicy:
    def test_peak_is_computed_even_when_the_position_never_resolved(self):
        """The substantive placement decision: peak lives outside the
        `holding_days is None` branch. MFE is bounded by exit_idx and so
        depends on exit policy; M_h depends only on the price path, and
        ADR 064's rule against config-coupled targets applies to it."""
        out = _derive(_path([0.01, 0.07, 0.02, 0.03, 0.01] + [0.0] * 5), holding_days=None)
        assert pd.isna(out["mfe"])
        assert out["peak_ret_5d"] == pytest.approx(0.07)

    def test_peak_does_not_move_when_holding_days_changes(self):
        """Sweeping the exit must not move these numbers. `mfe` would.

        The peak sits at offset 4, outside a 2-day hold and inside a 5-day
        one, so `mfe` genuinely differs between the two while
        `peak_ret_5d` cannot.
        """
        path = _path([0.01, 0.02, 0.03, 0.09, 0.01] + [0.0] * 5)
        short_hold = _derive(path, holding_days=2, exit_price=101.0)
        long_hold = _derive(path, holding_days=5, exit_price=101.0)
        assert short_hold["peak_ret_5d"] == long_hold["peak_ret_5d"] == pytest.approx(0.09)
        assert short_hold["mfe"] == pytest.approx(0.02)
        assert long_hold["mfe"] == pytest.approx(0.09)


class TestSqlMatchesPythonReference:
    """The SQL is generated, so its arithmetic can be replayed in pandas
    and diffed against the Python reference. This is what makes two
    implementations acceptable under invariant 2: divergence fails here.
    """

    @staticmethod
    def _sql_semantics(path: pd.DataFrame, entry_offset: int) -> dict:
        """Replays `peak_label_sql`'s FILTER/count logic exactly.

        A transcription, so it can only catch a divergence between the
        Python reference and *this reading* of the SQL — never a divergence
        between this and the SQL Postgres actually runs. That is what
        `tests/integration/test_peak_labels_sql.py` is for; keep both.
        """
        out = {}
        for h in HORIZONS:
            lo, hi = entry_offset + 1, entry_offset + h
            pk = path.loc[path["day_offset"].between(lo, hi), "favorable"]
            n = int(path["day_offset"].between(lo, hi).sum())
            out[f"peak_ret_{h}d"] = float(pk.max()) if n == h else float("nan")
        return out

    @pytest.mark.parametrize(
        "favorable",
        [
            [0.01, 0.08, 0.02, 0.01, 0.00, 0.03, 0.01, 0.02, 0.04, 0.01],
            [-0.02, -0.01, -0.03, -0.015, -0.04, -0.02, -0.05, -0.03, -0.06, -0.04],
            [0.05] * 10,
            [0.01, 0.02, 0.03],
        ],
    )
    @pytest.mark.parametrize(
        ("entry_offset", "first_offset"),
        [
            (0, 1),
            (1, 2),
            # The production shape: path numbered from 1 while entry sits at
            # offset 1, so `day_offset = 1` is present and out of window.
            # The only case that distinguishes a lower-bounded peak filter
            # from an unbounded one; the two cases above cannot.
            (1, 1),
        ],
    )
    def test_both_implementations_agree(self, favorable, entry_offset, first_offset):
        path = _path(favorable, entry_offset=entry_offset, first_offset=first_offset)
        py = _derive(path, entry_offset=entry_offset)
        sql = self._sql_semantics(path, entry_offset)
        for h in HORIZONS:
            key = f"peak_ret_{h}d"
            if pd.isna(sql[key]):
                assert pd.isna(py[key]), f"{key}: SQL null, Python {py[key]}"
            else:
                assert py[key] == pytest.approx(sql[key]), key

    def test_peak_filter_is_bounded_below_like_the_count_filter(self):
        """The peak window is `[off+1, off+h]`, closed at both ends.

        An unbounded `day_offset <= off + h` agrees with the Python
        reference for every `entry_offset = 0` event and diverges for every
        `next_open` one, which is the default entry kind (ADR 059) and the
        population `v_screen`/`v_chart` serve. It shipped and wrote wrong
        values to 80,273 of 155,344 labelled `next_open` events before this
        assertion existed. Structural, because `_sql_semantics` transcribes
        the SQL and so cannot fail on a bug it faithfully copies.
        """
        sql = peak_label_sql(HORIZONS)
        for h in HORIZONS:
            assert f"BETWEEN eo.entry_offset + 1 AND eo.entry_offset + {h}) AS pk{h}" in sql
        assert "<= eo.entry_offset" not in sql

    def test_generated_sql_covers_every_configured_horizon(self):
        """Invariant 9: the column list follows `fwd_ret_horizons`, never a
        hardcoded five."""
        sql = peak_label_sql(HORIZONS)
        for h in HORIZONS:
            assert f"peak_ret_{h}d" in sql
            assert f"AS pk{h}" in sql
            assert f"agg.n{h} = {h}" in sql

    def test_sql_scopes_to_filled_entries_of_one_config(self):
        sql = peak_label_sql(HORIZONS)
        assert "config_hash = :config_hash" in sql
        assert "entry_price IS NOT NULL" in sql

    def test_sql_entry_offset_matches_core_returns(self):
        """The SQL CASE is a translation of `core.returns.entry_offset_for`,
        not a second rule. If that function changes, this must too."""
        from capitalscan.core.returns import entry_offset_for
        from capitalscan.core.types import EntryKind

        assert entry_offset_for(EntryKind.NEXT_OPEN) == 1
        for kind in (EntryKind.TOUCH, EntryKind.TOUCH_5M, EntryKind.TOUCH_30M):
            assert entry_offset_for(kind) == 0
        assert "WHEN entry_kind = 'next_open' THEN 1 ELSE 0" in peak_label_sql(HORIZONS)
