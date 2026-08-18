"""The MCP server's read-only database role (ADR 027, session 16.2).

> Read-only enforced at the connection level, not by trusting the handlers.
> Use a database role without write grants.

Defense in depth is the entire point, and the session's "what will be
tempting" list names skipping it: *"Skipping the read-only role because the
handlers only read. Defense in depth is the whole point. A future handler
bug should not be able to write."* A role that cannot write turns a whole
class of defect from a data-loss incident into a failed query.

**Not an Alembic migration.** Roles are cluster-level objects, not
database-level ones. A role created by a migration would be created once per
database the chain is applied to, collide on the second, survive a
`downgrade` of the database that made it, and appear in no `pg_dump` of it.
`cscan db grant-readonly` runs this instead, and `db/schema.sql` never
mentions it.

**Why the SQL is built by string formatting and that is not a defect.**
Postgres accepts no bind parameter in `CREATE ROLE`, `GRANT`, or any DDL
identifier position - the parser needs the name at parse time. So the name
is validated against a strict pattern before it is interpolated, and the
password is escaped by the one rule Postgres defines for string literals.
Both are checked by tests that pass real injection attempts.
"""

from __future__ import annotations

import re

# Postgres identifiers are wider than this. The restriction is deliberate:
# a role name is chosen once by an operator and typed into a connection
# string, and every character outside this set is a character that will
# eventually need quoting somewhere it does not get quoted.
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

DEFAULT_ROLE = "capscan_ro"


class InvalidRoleName(ValueError):
    """A role name outside the accepted pattern.

    Raised before any SQL is composed, so a name that could change the
    meaning of a statement never reaches one.
    """


def check_role_name(name: str) -> str:
    if not _ROLE_NAME.match(name):
        raise InvalidRoleName(
            f"role name {name!r} is not accepted. Use lowercase letters, "
            "digits, and underscores, starting with a letter or underscore, "
            "at most 63 characters. The name is interpolated into DDL, where "
            "Postgres accepts no bind parameter, so the pattern is the guard."
        )
    return name


def check_database_name(name: str) -> str:
    """A database name safe to interpolate inside a quoted identifier.

    Wider than `check_role_name` on purpose: a database name is not this
    project's to choose, and `capitalscan` is only the default. It is
    interpolated inside double quotes, so the one character that can escape
    that context is a double quote - which is exactly what is rejected,
    along with a space and any control character. Postgres would accept
    a space inside a quoted identifier; it is refused because a database
    name that needs quoting to be typed is a name that will eventually be
    typed without them, and nothing in this project needs one.
    """
    if not name or '"' in name or min(name) <= " ":
        raise InvalidRoleName(
            f"database name {name!r} is not accepted: it is interpolated "
            "inside a quoted identifier, and a double quote would escape it."
        )
    return name


def quote_literal(value: str) -> str:
    """A Postgres string literal, escaped by doubling single quotes.

    The one rule the standard defines, and the one Postgres implements for
    non-`E''` strings. A backslash is *not* special here unless
    `standard_conforming_strings` is off, which has defaulted on since 9.1
    and which this asserts nothing about - doubling the quote is correct
    either way for the quote itself, and a password containing a backslash
    survives as written.
    """
    return "'" + value.replace("'", "''") + "'"


def grant_statements(role: str, password: str, database: str) -> list[str]:
    """Every statement, in order, that provisions the read-only role.

    Returned as a list rather than executed here so a test can read them and
    an operator can see them before they run. `cscan db grant-readonly`
    prints them with the password redacted.
    """
    check_role_name(role)
    check_database_name(database)
    pw = quote_literal(password)
    return [
        # Idempotent create. `NOINHERIT` so the role cannot pick up
        # privileges from a group it is later added to by accident, and no
        # CREATEDB or CREATEROLE.
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} LOGIN NOINHERIT PASSWORD {pw};
            ELSE
                ALTER ROLE {role} LOGIN NOINHERIT PASSWORD {pw};
            END IF;
        END
        $$;
        """,
        # Start from nothing. Running this on a role that was previously
        # granted more is the case that matters: a re-run must narrow, not
        # merely add.
        f"REVOKE ALL ON SCHEMA public FROM {role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}",
        # Connect and read. `USAGE` on the schema is required separately
        # from `SELECT` on its tables: without it every query fails with
        # "permission denied for schema public", which reads like a missing
        # table and sends the reader looking in the wrong place.
        f'GRANT CONNECT ON DATABASE "{database}" TO {role}',
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}",
        # Views are tables to this grant, and `v_screen`, `v_events`, and
        # `v_positions` are what the handlers actually read. The default
        # privilege covers tables and views a later migration creates, so
        # the role does not silently lose access the next time the schema
        # grows.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role}",
        # No sequence usage, on purpose and stated: a role that somehow
        # reached an INSERT would still fail on `nextval`, so the write path
        # is closed twice.
        f"REVOKE USAGE ON ALL SEQUENCES IN SCHEMA public FROM {role}",
        # `PUBLIC` is a real grantee. Postgres 15+ already removes its
        # CREATE on `public`, but a database restored from an older dump can
        # still carry it, and CREATE on the schema the handlers read is a
        # write path.
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
    ]


def redacted(statements: list[str], password: str) -> list[str]:
    """The same statements with the password replaced.

    Used for printing. The password appears twice in the DO block, so a
    naive first-occurrence replacement would leave the second one on screen.
    """
    if not password:
        return statements
    literal = quote_literal(password)
    return [s.replace(literal, "'<redacted>'") for s in statements]
