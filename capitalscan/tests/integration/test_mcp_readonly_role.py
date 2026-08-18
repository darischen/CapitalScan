"""The read-only role, proven against a live database (session 16 gate item 5).

> The read-only role cannot write, verified by attempting an insert through
> the same connection and asserting it fails.

"The same connection" is the load-bearing phrase. A test that checked the
`information_schema` grant tables would assert what Postgres was *told*; a
test that runs an INSERT asserts what Postgres *does*. Those come apart the
first time a default privilege, a group membership, or an ownership change
gets in between.

**Provisions the role itself** rather than assuming one exists.
`cscan db grant-readonly` is idempotent by design, so running it here is the
same operation an operator runs, which also means this test exercises the
provisioning path rather than a hand-made role that happens to resemble it.

This module does not truncate anything and writes no rows that survive: its
one INSERT is expected to fail.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from capitalscan.jobs import db_io
from capitalscan.jobs.roles import grant_statements

ROLE = "capscan_ro_test"
PASSWORD = "role-test-only-not-a-secret"


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
def readonly_engine():
    """A connection as the read-only role, dropped afterwards.

    A distinct name from the production `capscan_ro`, so a run of this
    suite cannot rotate the password of a role a running server is using.
    """
    admin = db_io.get_engine()
    database = admin.url.database or "capitalscan"
    with admin.begin() as conn:
        for statement in grant_statements(ROLE, PASSWORD, database):
            conn.execute(text(statement))

    url = admin.url.set(username=ROLE, password=PASSWORD)
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            # Privileges have to go before the role can. A `DROP ROLE` with
            # grants outstanding fails with "cannot be dropped because some
            # objects depend on it", which would leave a live login behind
            # every time this suite ran.
            conn.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}"))
            conn.execute(text(f"REVOKE ALL ON SCHEMA public FROM {ROLE}"))
            conn.execute(text(f'REVOKE ALL ON DATABASE "{database}" FROM {ROLE}'))
            conn.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {ROLE}"
                )
            )
            conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))


def test_the_role_can_read(readonly_engine):
    """Half the point. A role that cannot read is not a fix, it is an outage."""
    with readonly_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tickers")).scalar_one() >= 0


def test_the_role_can_read_the_views_the_handlers_use(readonly_engine):
    """`GRANT SELECT ON ALL TABLES` covers views, and that is worth pinning:
    `v_screen` and `v_events` are what `screen_signals` and `get_events`
    actually read, and a role that could read `events` but not `v_events`
    would fail only on the serving path."""
    with readonly_engine.connect() as conn:
        conn.execute(text("SELECT 1 FROM v_events LIMIT 1")).fetchall()
        conn.execute(text("SELECT 1 FROM v_screen LIMIT 1")).fetchall()


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("INSERT INTO tickers (ticker, name) VALUES ('ZZTEST', 'x')", id="insert"),
        pytest.param("UPDATE tickers SET name = 'x' WHERE ticker = 'AAPL'", id="update"),
        pytest.param("DELETE FROM tickers WHERE ticker = 'ZZTEST'", id="delete"),
        pytest.param("CREATE TABLE zz_should_not_exist (x int)", id="create_table"),
        pytest.param("DROP TABLE IF EXISTS bar_rejects", id="drop_table"),
    ],
)
def test_every_write_is_refused_on_the_same_connection(readonly_engine, statement):
    """Gate item 5. Not the grant tables - the actual attempt.

    Parameterized across the write verbs rather than testing INSERT alone,
    because the grants that permit each are separate and a role can easily
    end up able to do one of them.
    """
    with pytest.raises(ProgrammingError) as exc:
        with readonly_engine.begin() as conn:
            conn.execute(text(statement))
    message = str(exc.value).lower()
    # Two refusals, not one. DML is refused for lack of a grant ("permission
    # denied for table ..."); DDL on an existing object is refused for lack
    # of *ownership* ("must be owner of table ..."), because no grant confers
    # DROP in Postgres. Asserting only the first would have passed on every
    # row here except `drop_table` and failed there for the right reason,
    # which is how this line came to say both.
    assert "permission denied" in message or "must be owner" in message, message


def test_the_role_cannot_use_a_sequence(readonly_engine):
    """Closed twice on purpose: a write that somehow got past the table
    grant would still fail on `nextval`."""
    with pytest.raises(ProgrammingError):
        with readonly_engine.begin() as conn:
            conn.execute(text("SELECT nextval('events_id_seq')"))


def test_the_role_cannot_create_a_role_of_its_own(readonly_engine):
    with pytest.raises(ProgrammingError):
        with readonly_engine.begin() as conn:
            conn.execute(text("CREATE ROLE zz_escalated LOGIN"))


def test_running_the_grants_twice_leaves_the_same_privileges(readonly_engine):
    """Idempotence, asserted by behaviour rather than by the SQL text.

    An operator re-runs `cscan db grant-readonly` after a schema change;
    if the second run widened or narrowed anything, the server's access
    would change without anyone editing a grant.
    """
    admin = db_io.get_engine()
    database = admin.url.database or "capitalscan"
    with admin.begin() as conn:
        for statement in grant_statements(ROLE, PASSWORD, database):
            conn.execute(text(statement))

    with readonly_engine.connect() as conn:
        conn.execute(text("SELECT count(*) FROM tickers")).scalar_one()
    with pytest.raises(ProgrammingError):
        with readonly_engine.begin() as conn:
            conn.execute(text("INSERT INTO tickers (ticker, name) VALUES ('ZZTEST', 'x')"))
