"""events gains bb_mid, close and vix_pct_252d — DESIGN §7.3's last three

Revision ID: f1c8a260d94e
Revises: e7b3f052c19a
Create Date: 2026-08-27 04:00:00.000000

DESIGN §7.3 says all twenty-two features are "already on the event row".
Three are not, measured against `information_schema`:

    distance to mid in ATR units   bb_mid absent from events
    atr_14 / close                 atr_14 present, close absent
    vix_pct_252d                   absent from events

Each exists at t-1 in a source table -- `indicators.bb_mid`, `bars.close`,
`market_days.vix_pct_252d` -- which is why the ticker page can show them
while the model cannot use them: the page reads those tables directly and
the training frame reads `events`.

**t-1, and proven so rather than asserted.** Invariant 3 -- "indicators are
read at t-1, never t" -- is the highest-risk silent failure in this system,
so the lateral below was checked against a column the worker already
computes. Backfilling `bb_pctb` through this exact lateral and comparing
against the stored value gave **20,000 of 20,000 exact matches, zero
mismatches, zero nulls**. It reproduces `_prior_indicator`'s "latest
strictly before signal_date" (Ruling C3) rather than merely resembling it.

**`close` is the t-1 close, not `entry_price`.** `BACKLOG.md` records why
that distinction is load-bearing: `entry_price` is priced at *t*, so using
it as the denominator of `atr_14 / close` would put the entry into a
state-at-signal feature and quietly reintroduce look-ahead into the one
place the design is most careful about.

**A post-pass, not a per-worker lookup.** `db_io.fill_event_derived_state`
runs once after the writers return, the same shape as `add_cofire_count`
and `fill_event_sector_and_mcap`. Threading three more lookups into
`_backtest_one_ticker` would put a market-day join inside every one of
eight processes for values that are identical across the whole chunk.

Backfilled for the serving generation only, as `c2b91e4a7d08` did: the
other hashes are superseded and nothing reads them.

**The backfill moved out of this revision on 2026-08-27, and the reason is
the whole point.** This originally did the ADD COLUMNs and the 1.37M-row
UPDATE together. Alembic runs a revision in one transaction, and a lock is
held until that transaction commits -- so the ACCESS EXCLUSIVE taken by the
catalogue-only ALTER above was held for the entire twenty-minute UPDATE.
ACCESS EXCLUSIVE conflicts with reads, so every query against `events` on
the serving store queued behind it and the site stopped answering. Four
page queries sat in `wait_event_type = Lock` for six minutes.

The comment above -- "the ACCESS EXCLUSIVE lock is held for a catalogue
update" -- was true about the *operation* and wrong about the *lock*, which
is exactly the distinction that cost an outage.

`lock_timeout` does not help here and was tried: the ALTER acquires its
lock immediately, so nothing times out. It is the readers who queue, and
they are not the ones holding the setting.

Splitting it fixes it outright rather than mitigating it. A bare UPDATE
takes ROW EXCLUSIVE, which does not conflict with readers, so the backfill
in `a7d4e91c2b35` can run for as long as it likes against a live site.

**Rule: never put DDL and a long DML in one revision.** The DDL's lock
outlives its own runtime and inherits the DML's duration.

"""

from alembic import op

revision = "f1c8a260d94e"
down_revision = "e7b3f052c19a"
branch_labels = None
depends_on = None

CONFIG_HASH = "a38d3ca6b58295e8"


def upgrade() -> None:
    # Nullable, no default: catalogue-only on Postgres 11+, so no rewrite of
    # 6M rows and the ACCESS EXCLUSIVE lock is held for a catalogue update.
    for col in ("bb_mid", "close", "vix_pct_252d"):
        op.execute(f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {col} numeric(18,6)")

    op.execute(
        "COMMENT ON COLUMN events.close IS "
        "'Split-adjusted close at t-1, NOT entry_price. entry_price is "
        "priced at t; using it as the denominator of atr_14/close would put "
        "the entry into a state-at-signal feature (invariant 3).'"
    )
    op.execute(
        "COMMENT ON COLUMN events.bb_mid IS "
        "'Bollinger mid band at t-1, same row bb_pctb comes from.'"
    )
    op.execute(
        "COMMENT ON COLUMN events.vix_pct_252d IS "
        "'VIX 252-day percentile at t-1, from market_days.'"
    )


def downgrade() -> None:
    for col in ("bb_mid", "close", "vix_pct_252d"):
        op.execute(f"COMMENT ON COLUMN events.{col} IS NULL")
        op.execute(f"ALTER TABLE events DROP COLUMN IF EXISTS {col}")
