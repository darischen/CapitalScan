"""Materialize ADR 093's peak-within-horizon family onto `events`.

    M_h = max over t in [1, h] of the entry-anchored return

for `h` in `StatsParams.fwd_ret_horizons`, written to
`events.peak_ret_{h}d` (migration `c7e1a4f9d302`).

**Why a writer lives here and not in the two adjacent modules.**
`research/path_labels.py` documents itself as writing nothing — Task 10.3
required the old label columns to stay untouched so rollback stayed free —
and that contract is worth keeping even now the gate has passed.
`research/path_backfill.py` is scoped to populating `path` itself. This is
a third thing: projecting a derived family from `path` onto `events`.

**Set-based, not per-event.** One `UPDATE ... FROM (SELECT ... FILTER ...)`
over the whole config, rather than iterating 622k events in Python. The
per-event Python version in `path_labels.derive_labels_from_path` is kept
as the readable reference and as a cross-check: `test_peak_labels.py`
asserts the two agree on a fixture, so a divergence fails a test rather
than silently producing two answers. That is the same shape as
`path_reconcile`'s Session-9-versus-path-derived comparison, and the reason
two implementations are tolerable here where invariant 2 would normally
forbid them — one is the product, the other is the oracle.

**Completeness, not partial peaks.** `peak_ret_{h}d` is written only when
every offset in `[entry_offset+1, entry_offset+h]` is present in `path`.
A peak taken over a still-accumulating window is a smaller number wearing
a complete label, which is exactly the staleness class ADR 094 exists to
prevent. Events whose window has not closed keep NULL and are picked up by
a later run, which is safe because the UPDATE is idempotent.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

__all__ = ["backfill_peak_labels", "peak_label_sql"]

# `NEXT_OPEN` fills one trading day after the signal; every other kind
# fills on the signal bar. Mirrors `core.returns.entry_offset_for`, which
# is the authority — this is its SQL translation, not a second rule. If a
# fifth `EntryKind` ever lands with a different offset, that function and
# this expression change together.
_ENTRY_OFFSET_SQL = "CASE WHEN entry_kind = 'next_open' THEN 1 ELSE 0 END"


def peak_label_sql(horizons: tuple[int, ...]) -> str:
    """The UPDATE statement, built from `horizons` rather than hardcoded.

    `StatsParams.fwd_ret_horizons` is sweepable (invariant 9), so the
    column list follows it. A horizon with no matching `peak_ret_{h}d`
    column raises from Postgres rather than silently writing nothing —
    the same failure mode `_EVENT_COLUMNS` guards against in
    `research/backtest.py`, and loud is the right answer both times.
    """
    peaks = ",\n           ".join(
        f"max(p.favorable) FILTER (WHERE p.day_offset <= eo.entry_offset + {h}) AS pk{h}"
        for h in horizons
    )
    counts = ",\n           ".join(
        f"count(*) FILTER (WHERE p.day_offset BETWEEN eo.entry_offset + 1 "
        f"AND eo.entry_offset + {h}) AS n{h}"
        for h in horizons
    )
    # `CASE WHEN n{h} = {h}` is the completeness gate: the window
    # [off+1, off+h] holds exactly h offsets, so a smaller count means the
    # forward window is still filling. NULL, never a partial maximum.
    sets = ",\n        ".join(
        f"peak_ret_{h}d = CASE WHEN agg.n{h} = {h} THEN agg.pk{h} ELSE NULL END" for h in horizons
    )
    return f"""
    WITH eo AS (
        SELECT id, {_ENTRY_OFFSET_SQL} AS entry_offset
        FROM events
        WHERE config_hash = :config_hash AND entry_price IS NOT NULL
    ),
    agg AS (
        SELECT p.event_id,
           {peaks},
           {counts}
        FROM path p JOIN eo ON eo.id = p.event_id
        GROUP BY p.event_id
    )
    UPDATE events e SET
        {sets}
    FROM agg
    WHERE e.id = agg.event_id
    """


def backfill_peak_labels(engine: Engine, config_hash: str, horizons: tuple[int, ...]) -> int:
    """Write `peak_ret_*d` for one `config_hash`. Returns rows updated.

    Scoped to a single config deliberately (user's decision, 2026-08-09):
    only the live hash is in use, and the superseded generations in
    `events` are kept as database history rather than as anything a query
    reads. Retrofitting them would multiply the write for no consumer.

    Idempotent. Re-running recomputes from `path`, so an event whose
    forward window closed since the last run picks up its value, and one
    whose window is still open stays NULL rather than being frozen at a
    partial maximum.
    """
    with engine.begin() as conn:
        result = conn.execute(text(peak_label_sql(horizons)), {"config_hash": config_hash})
    return int(result.rowcount)
