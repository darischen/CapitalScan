"""The read-only role's SQL (ADR 027, session 16.2).

Session 16's gate item 5 is one of the two it calls "the difference between
a server and an open database". The other half of it - proving the role
cannot actually write - needs a database and lives in
`tests/integration/test_mcp_readonly_role.py`. This file covers the half
that can be checked without one: that the statements say what they should,
and that nothing a caller supplies can change what they mean.

**Why string formatting is used at all.** Postgres accepts no bind
parameter in `CREATE ROLE`, `GRANT`, or any identifier position - the parser
needs the name before it has values. So the guard is a pattern check before
composition, and these tests are what make that guard evidence rather than
an assertion in a docstring.
"""

from __future__ import annotations

import pytest

from capitalscan.jobs.roles import (
    DEFAULT_ROLE,
    InvalidRoleName,
    check_database_name,
    check_role_name,
    grant_statements,
    quote_literal,
    redacted,
)

PW = "correct horse battery staple"


def _sql(role: str = DEFAULT_ROLE, password: str = PW, database: str = "capitalscan") -> str:
    return "\n".join(grant_statements(role, password, database))


# ---------------------------------------------------------------------------
# What the role may do
# ---------------------------------------------------------------------------


def test_the_role_is_granted_select_and_nothing_wider():
    sql = _sql()
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public" in sql
    for write in ("GRANT INSERT", "GRANT UPDATE", "GRANT DELETE", "GRANT TRUNCATE", "GRANT ALL"):
        assert write not in sql, f"{write} would defeat the point of the role"


def test_schema_usage_is_granted_separately_from_select():
    """Without `USAGE` every query fails with "permission denied for schema
    public", which reads like a missing table and sends the reader looking
    in the wrong place."""
    assert "GRANT USAGE ON SCHEMA public" in _sql()


def test_the_role_cannot_create_databases_or_roles():
    sql = _sql()
    assert "CREATEDB" not in sql
    assert "CREATEROLE" not in sql
    assert "SUPERUSER" not in sql
    assert "NOINHERIT" in sql


def test_sequence_usage_is_revoked():
    """A role that somehow reached an INSERT would still fail on `nextval`,
    so the write path is closed twice."""
    assert "REVOKE USAGE ON ALL SEQUENCES IN SCHEMA public" in _sql()


def test_public_loses_create_on_the_schema():
    """Postgres 15+ already removes it, but a database restored from an
    older dump can still carry it, and CREATE on the schema the handlers
    read is a write path."""
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in _sql()


def test_a_rerun_narrows_rather_than_only_adding():
    """The case that matters is a role someone over-granted by hand.

    Every REVOKE precedes every GRANT, so re-running converges on the
    intended set rather than preserving whatever was there.
    """
    statements = grant_statements(DEFAULT_ROLE, PW, "capitalscan")
    first_grant = next(i for i, s in enumerate(statements) if s.strip().startswith("GRANT"))
    last_revoke_before = [
        i for i, s in enumerate(statements[:first_grant]) if s.strip().startswith("REVOKE")
    ]
    assert last_revoke_before, "no REVOKE runs before the first GRANT"


def test_the_create_is_idempotent():
    assert "IF NOT EXISTS" in _sql()
    assert "ALTER ROLE" in _sql()


def test_default_privileges_cover_tables_a_later_migration_adds():
    """Otherwise the role silently loses access the next time the schema
    grows, and the failure surfaces as an empty tool result."""
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES" in _sql()


# ---------------------------------------------------------------------------
# Nothing a caller supplies can change what the statements mean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "capscan_ro; DROP TABLE bars",
        'capscan_ro"',
        "capscan ro",
        "CapscanRO",
        "1role",
        "",
        "x" * 64,
        "role--comment",
    ],
)
def test_a_role_name_that_could_change_the_sql_is_refused(name):
    with pytest.raises(InvalidRoleName):
        check_role_name(name)


def test_the_refusal_happens_before_any_sql_is_composed():
    """A name that could change a statement never reaches one."""
    with pytest.raises(InvalidRoleName):
        grant_statements("ro; DROP TABLE bars", PW, "capitalscan")


@pytest.mark.parametrize("name", ["capscan_ro", "mcp_reader", "_x", "a1_b2"])
def test_ordinary_role_names_are_accepted(name):
    assert check_role_name(name) == name


@pytest.mark.parametrize("name", ['db"name', "", "db name", "db\tname"])
def test_a_database_name_that_could_escape_its_quotes_is_refused(name):
    with pytest.raises(InvalidRoleName):
        check_database_name(name)


def test_the_default_database_name_is_accepted():
    assert check_database_name("capitalscan") == "capitalscan"


# ---------------------------------------------------------------------------
# The password
# ---------------------------------------------------------------------------


def test_a_quote_in_the_password_is_doubled_not_dropped():
    """The one rule Postgres defines for a non-`E''` string literal.

    Dropping the character would silently set a different password than the
    operator typed, and the failure would surface as an authentication error
    on a value they are sure is right.
    """
    assert quote_literal("pa'ss") == "'pa''ss'"


def test_a_password_cannot_terminate_the_statement():
    sql = _sql(password="x'; DROP TABLE bars; --")
    assert "DROP TABLE bars" in sql  # it is data
    assert "'x''; DROP TABLE bars; --'" in sql  # inside one literal
    assert sql.count("DROP TABLE bars") == 2  # once per CREATE/ALTER branch


def test_a_backslash_in_the_password_survives_as_written():
    assert quote_literal("a\\b") == "'a\\b'"


def test_the_password_is_redacted_in_every_place_it_appears():
    """It appears twice - the CREATE branch and the ALTER branch.

    A first-occurrence replacement would leave the second one on screen,
    which is exactly the kind of near-miss that makes a redaction worse than
    none: it looks handled.
    """
    statements = grant_statements(DEFAULT_ROLE, PW, "capitalscan")
    for statement in redacted(statements, PW):
        assert PW not in statement
    assert "\n".join(redacted(statements, PW)).count("'<redacted>'") == 2


def test_redaction_with_no_password_is_a_no_op():
    statements = grant_statements(DEFAULT_ROLE, "", "capitalscan")
    assert redacted(statements, "") == statements
