"""`v_ticker_state` returns what it returned before ADR 116 rewrote it.

A performance change is only safe if it is provably not a behaviour change,
and "provably" here means running both versions against the same data rather
than reading the two queries and agreeing they look equivalent. That reading
is what produced the intermediate version this test would have caught: an
attempt that moved the `DISTINCT ON` inside without keeping the `bars`
filter would have dropped any ticker whose newest indicator row had no bar.

The old view is rebuilt from `views.V_TICKER_STATE_DDL_PRE_116` under a
second name and diffed with `EXCEPT` in both directions. That is the only
form of this test that means anything: comparing row *counts* passes while
every column is wrong, and comparing one hand-picked ticker passes while
611 others are not.

Cheap on CI, where the container is empty. On a populated database the old
view takes ~24 s, which is the cost being removed and a reasonable price to
pay once per run to know it was removed safely.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.jobs import db_io, views

SHADOW = "v_ticker_state_pre_116"


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture(scope="module")
def shadow():
    """The pre-ADR-116 view, built alongside the live one and dropped after.

    `max_parallel_workers_per_gather = 0` on every connection: the old view
    sorts 2.9M joined rows, and with parallelism on it fails outright with
    `could not resize shared memory segment` on the developer database
    (CLAUDE.md). Harmless on CI's empty container.
    """
    engine = db_io.get_engine()
    ddl = views.V_TICKER_STATE_DDL_PRE_116.replace("public.v_ticker_state", f"public.{SHADOW}")
    with engine.begin() as conn:
        conn.execute(text("SET max_parallel_workers_per_gather = 0"))
        conn.execute(text(f"DROP VIEW IF EXISTS public.{SHADOW}"))
        conn.execute(text(ddl))
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP VIEW IF EXISTS public.{SHADOW}"))


def _scalar(engine, sql: str) -> int:
    with engine.connect() as conn:
        conn.execute(text("SET max_parallel_workers_per_gather = 0"))
        return int(conn.execute(text(sql)).scalar_one())


def _shadow_columns(engine) -> str:
    """The pre-ADR-116 column list, quoted, in its original order.

    **Both diffs project this rather than `SELECT *`.** `EXCEPT` requires
    matching arity, so a column added to `v_ticker_state` afterwards turns
    both assertions into a syntax error -- which is what happened on
    2026-08-26 when `a4f8c21d7e63` appended `in_watch` and `watch_reason`,
    and it failed CI as `each EXCEPT query must have the same number of
    columns`.

    That failure said nothing about behaviour. This test exists to prove
    the ADR 116 rewrite returns what it returned before, and a column that
    did not exist then cannot participate in that question. Naming the old
    columns keeps the comparison exact on every one of them while letting
    the view grow.
    """
    with engine.connect() as conn:
        names = list(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :v "
                    "ORDER BY ordinal_position"
                ),
                {"v": SHADOW},
            ).scalars()
        )
    assert names, f"{SHADOW} has no columns; the shadow view was not built"
    return ", ".join(f'"{n}"' for n in names)


def test_the_shadow_view_was_actually_built(shadow):
    """Guards every assertion below against passing on a missing view.

    An `EXCEPT` against a view that does not exist raises rather than
    passing, but a shadow built over *zero* rows would make both diffs
    trivially empty and the whole file vacuous. This says which case CI is
    in rather than leaving it ambiguous.
    """
    rows = _scalar(shadow, f"SELECT count(*) FROM {SHADOW}")
    live = _scalar(shadow, "SELECT count(*) FROM v_ticker_state")
    assert rows == live
    if rows == 0:
        pytest.skip("no ticker state rows; the diffs below would be vacuous")


def test_the_rewrite_drops_nothing(shadow):
    """Every row the old view returned, the new one returns identically.

    Column for column across every pre-ADR-116 column: `EXCEPT` compares
    whole rows, so a single changed `bb_upper` on one ticker fails this.
    Columns added after ADR 116 are excluded deliberately -- see
    `_shadow_columns`.
    """
    cols = _shadow_columns(shadow)
    missing = _scalar(
        shadow,
        f"SELECT count(*) FROM (SELECT {cols} FROM {SHADOW} "
        f"EXCEPT SELECT {cols} FROM v_ticker_state) d",
    )
    assert missing == 0, f"{missing} row(s) present before ADR 116 and absent after"


def test_the_rewrite_invents_nothing(shadow):
    """The other direction, which the first does not imply.

    A rewrite returning two rows per ticker would still contain every
    original row and pass the test above.
    """
    cols = _shadow_columns(shadow)
    extra = _scalar(
        shadow,
        f"SELECT count(*) FROM (SELECT {cols} FROM v_ticker_state "
        f"EXCEPT SELECT {cols} FROM {SHADOW}) d",
    )
    assert extra == 0, f"{extra} row(s) absent before ADR 116 and present after"


def test_one_row_per_ticker(shadow):
    """What `DISTINCT ON (ticker)` guaranteed, still guaranteed by the
    lateral's `LIMIT 1`."""
    duplicated = _scalar(
        shadow,
        "SELECT count(*) FROM (SELECT ticker FROM v_ticker_state "
        "GROUP BY ticker HAVING count(*) > 1) d",
    )
    assert duplicated == 0


def test_every_row_is_the_newest_the_ticker_has(shadow):
    """The row selected is the latest daily indicator row with a bar.

    Independent of the shadow diff: it re-derives the expected `as_of` from
    `indicators` and `bars` rather than trusting that two views agreeing
    means either is right.
    """
    wrong = _scalar(
        shadow,
        """
        SELECT count(*) FROM v_ticker_state v
        WHERE v.as_of <> (
            SELECT max(i.ts) FROM indicators i
            WHERE i.ticker = v.ticker AND i.interval = '1d'
              AND EXISTS (SELECT 1 FROM bars b
                          WHERE b.ticker = i.ticker AND b.ts = i.ts
                            AND b.interval = i.interval)
        )
        """,
    )
    assert wrong == 0


def test_the_supporting_index_exists(shadow):
    """Without it the rewrite still returns the right answer and takes
    1.1 s instead of 27 ms, so a missing index is a silent regression
    rather than a failure."""
    present = _scalar(
        shadow,
        "SELECT count(*) FROM pg_indexes "
        "WHERE schemaname = 'public' AND indexname = 'indicators_daily_latest'",
    )
    assert present == 1


def test_the_index_is_partial_to_daily(shadow):
    """`indicators` holds hourly rows the view never reads. A full index
    would be several times larger and maintained on every hourly write for
    no read it serves."""
    with db_io.get_engine().connect() as conn:
        ddl = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                "AND indexname = 'indicators_daily_latest'"
            )
        ).scalar_one()
    assert "WHERE" in ddl and "1d" in ddl
