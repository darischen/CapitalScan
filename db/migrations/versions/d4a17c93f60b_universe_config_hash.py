"""universe.config_hash: which definition produced this membership

Revision ID: d4a17c93f60b
Revises: c2b91e4a7d08
Create Date: 2026-08-27 02:10:00.000000

`universe` was `PRIMARY KEY (ticker, as_of)` with no record of the config
that evaluated it, so two configs' membership could not coexist --
evaluating a second one overwrote the first, row for row.

**Two things followed, and the second is the sharp one.** The three-arm
ablation plan needed a full 66-quarter pass per arm, because each arm
destroyed the previous one's table. And the poller builds its ticker list
from `universe.in_trade` while `v_universe` feeds the site, so after any
arm ran, **live membership was that arm's** whether or not it was the one
meant to be serving. Restoring production meant another 20-minute pass.

ADR 060 makes universe definition part of the config. This closes the gap
where the table storing that definition's output could not say which
definition it was -- a stale `universe` and a current one were
indistinguishable by inspection.

**Existing rows become `'unknown'`, not the current hash.**

Nothing on file records what produced them. `runs` for the `universe` job
stores only `{"quarter": "2026Q2"}` -- no config, no hash -- so labelling
78,554 rows `a38d3ca6b58295e8` would be a guess written down as a fact. The
last pass did run at 00:09-00:11 on 2026-08-26, which is when the NYSE
rebuild ran, and that is *inference*. `'unknown'` is what is actually
known.

**This means the table serves nothing until a pass re-tags it**, because
every reader now scopes on a real hash. That is deliberate: an empty
screener is loud, and a wrongly-labelled universe is silent. The 66-quarter
pass costs ~20 minutes (measured, ~18 s/quarter) and writes correctly
tagged rows that coexist with the `'unknown'` ones under the new key.

Delete the `'unknown'` rows once a tagged pass is verified. This migration
does not, because a downgrade could not put them back.

**`NOT NULL` with the default dropped afterwards** so a writer that forgets
to set it fails loudly on the next insert rather than silently creating a
second `'unknown'` generation.
"""

from alembic import op

revision = "d4a17c93f60b"
down_revision = "c2b91e4a7d08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `DEFAULT` in the same statement makes this catalogue-only on Postgres
    # 11+ -- no rewrite of 78,554 rows -- and backfills every existing row
    # in one step. Dropping the default immediately afterwards is what makes
    # a forgetful writer fail instead of inventing another 'unknown' batch.
    op.execute("ALTER TABLE universe ADD COLUMN config_hash text NOT NULL DEFAULT 'unknown'")
    op.execute("ALTER TABLE universe ALTER COLUMN config_hash DROP DEFAULT")

    op.execute("ALTER TABLE universe DROP CONSTRAINT universe_pkey")
    op.execute(
        "ALTER TABLE universe ADD CONSTRAINT universe_pkey PRIMARY KEY (ticker, as_of, config_hash)"
    )

    # Every hot reader filters on the hash and then takes the newest `as_of`
    # at or before a date. Without this the lateral in `v_ticker_state`,
    # `features.py` and `v_watchlist` degrades to a scan per row.
    op.execute(
        "CREATE INDEX IF NOT EXISTS universe_config_ticker_asof_idx "
        "ON universe (config_hash, ticker, as_of DESC)"
    )

    op.execute(
        "COMMENT ON COLUMN universe.config_hash IS "
        "'The config whose UniverseParams produced this row. ''unknown'' "
        "marks rows that predate this column - nothing recorded what "
        "evaluated them. Readers must scope on a real hash; see ADR 060.'"
    )


def downgrade() -> None:
    # The 'unknown' rows are the pre-migration set, so collapsing back to
    # (ticker, as_of) requires that no second generation exists. Deleting
    # the tagged rows is the only way back that cannot violate the old key,
    # and it is lossless in the sense that matters: they are reproducible by
    # re-running the pass that wrote them.
    op.execute("DELETE FROM universe WHERE config_hash <> 'unknown'")
    op.execute("DROP INDEX IF EXISTS universe_config_ticker_asof_idx")
    op.execute("ALTER TABLE universe DROP CONSTRAINT universe_pkey")
    op.execute("ALTER TABLE universe ADD CONSTRAINT universe_pkey PRIMARY KEY (ticker, as_of)")
    op.execute("ALTER TABLE universe DROP COLUMN config_hash")
