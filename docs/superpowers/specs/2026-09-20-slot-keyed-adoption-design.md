# Slot-keyed adoption — design

**Date:** 2026-09-20. **Status:** approved in chat, not built.
**Follows:** ADR 196, which built adoption and shipped it keyed on the wrong
thing.

## The problem, measured

The 2026-09-20 nightly adopted 100 of the Pi's predictions and left **every
one** with a NULL `event_id`. Adoption remaps through the predictions natural
key `(config_hash, ticker, as_of, signal_type, entry_kind)`, and research
holds no event under that key.

| measurement (2026-09-20) | value |
|---|---:|
| poller-written events on serving since 2026-09-08 | 338 |
| of those, exact natural-key match in research | **0** |
| of those, research has any event for that ticker+date | 114 |
| adopted rows in research carrying a NULL `event_id` | 100 of 100 |

**Cause: `signal_type` is not part of a signal's identity.** DESIGN §4.7 and
`core/signals.py::debounce_key` define the slot as
`(ticker, signal_date, bound)` — one event per ticker, per side, per day.
The label attached to that slot is whichever hit filled it first, and the two
detectors cannot fill it the same way: `breach_live` has no close to confirm
against, so the poller emits `bb_lower_touch` intraday while the end-of-day
pass sees the close inside the band and labels the same slot
`bull_close_below_lower` (ADR 194, enabled 2026-09-10 — which is why this
surfaced now). USB 2026-09-18 is the worked example: `bb_lower_touch` on
serving, `bull_close_below_lower` in research, same bar.

## Decision

**Adopt on the debounce slot, not on the label.** The inbound remap resolves
`(config_hash, ticker, signal_date, side, entry_kind)`, which is
`debounce_key` plus the fill convention. `side` is derived from the
prediction's `signal_type` through `core/cells.py`'s `LONG_SIGNALS` /
`SHORT_SIGNALS` — the existing single source that `side_for_signal_type`
already reads. No second table.

**Both labels survive, and nothing is relabelled.** The adopted row keeps the
live `signal_type` the reader saw; the research event it links to keeps the
end-of-day label. The disagreement becomes joinable data rather than a silent
mismatch.

**Outbound is untouched.** Research-written predictions carry the end-of-day
label, which matches their own events, and an adopted row synced back to
serving resolves against serving's own poller event under its live label. Only
the inbound path changes.

## Ambiguity

Measured: **15 slots of 182,921 events** since 2026-08-01 hold two events with
different `signal_type`, all on the prior generation, long side, `touch`.

A slot resolving to more than one research event adopts with
`event_id = NULL` and is counted separately. Picking one would be a guess, and
ADR 191 already established that a confidently wrong link is worse than an
absent one.

## Components

### 1. A slot remap for the inbound pull

`jobs/sync.py` gains a slot-keyed spec used only by `_pull_predictions`:

- Frame side: `config_hash`, `ticker`, `as_of`, derived `side`, `entry_kind`.
- Target side: `config_hash`, `ticker`, `signal_date`, `side`, `entry_kind`
  on `events`.
- A key resolving to exactly one event gets that id; to none or to several,
  NULL.

`_apply_remap` already resolves a natural key against a target table; it
gains the derived column and the ambiguity rule, or a sibling helper does, so
the existing outbound behaviour is not disturbed.

### 2. Counts that say which failure happened

`pull_live_records` returns `predictions`, `predictions_unmapped_no_slot` and
`predictions_unmapped_ambiguous`. "100 adopted, 100 unmapped" told us nothing
about why; these two numbers separate "research never saw this ticker-date"
from "research saw it twice".

### 3. A NULL-only backfill for what is already adopted

Adoption is insert-only (ADR 195), so tonight's 100 NULL rows will never be
repaired by a later pull. A script sets `event_id` **only where it is
currently NULL**, using the same slot resolution, and touches no other column
on any row. It is idempotent, `--dry-run` by default, and reports the same
three counts.

This is the parked follow-up from the 2026-09-19 review, now with a rule that
can resolve the links.

### 4. Docs

An ADR recording slot-keyed adoption, the 0-of-338 measurement, and the
reason the labels cannot be made to agree. `RESULTS.md` carries the
measurement; `BACKLOG.md`'s parked NULL-link item closes.

## Testing

Unit, no database:

1. A slot resolving to exactly one event links to it, though the labels differ
   (`bb_lower_touch` prediction onto a `bull_close_below_lower` event).
2. A slot with no research event yields NULL and counts as `no_slot`.
3. A slot with two events yields NULL and counts as `ambiguous`, never a pick.
4. `side` derives from `signal_type` through `core/cells.py`, and an unknown
   type raises rather than defaulting to long.
5. The adopted row keeps its live `signal_type` — adoption relabels nothing.
6. Outbound `run_sync` still uses the natural-key remap, unchanged.
7. The backfill sets only NULL `event_id`s, and a second run changes nothing.

Against real Postgres, on `zz_` scratch tables: a slot-keyed link resolving
across differing labels, and the ambiguous case staying NULL.

## Out of scope

The 114-of-338 rows whose ticker-date research never saw at all: those stay
NULL by design and are now counted. ADR 150's exclusion of provisional poller
events stands.
