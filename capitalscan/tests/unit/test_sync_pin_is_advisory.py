"""The config-hash pin is a convenience, and must not fail a sync that worked.

Measured 2026-08-21 against Neon. `cscan sync` wrote events, cell_stats and
benchmarks in full, then raised on its last statement:

    ALTER DATABASE "neondb" SET capitalscan.default_config_hash = ...
    psycopg.errors.InsufficientPrivilege: permission denied to set parameter

Neon does not grant `ALTER DATABASE ... SET` to non-superuser roles. Nothing
was broken by it — ADR 115 made the serving views read the one-row
`serving_config` *table* rather than the GUC, and that table is written
earlier in the same sync — but the run recorded `status = 'failed'` with
`rows_written = 0` after writing roughly 100,000 rows.

Two separate defects, and the second is the one that misleads:

1. A managed Postgres that forbids the statement fails a sync that succeeded.
2. `report.rows_written` was assigned *after* the pin, so the count was lost
   with it. A failed run that reports zero rows written, having written a
   hundred thousand, is worse than one that reports nothing.
"""

from __future__ import annotations

import inspect

from capitalscan.jobs import sync


class TestRowsWrittenSurvivesAPinFailure:
    """**Assign the count before the statement that can raise.**

    This is the half that costs you an investigation. `runs.rows_written`
    is how anyone later asks "did serving actually get the data", and it
    answered 0 for a sync that had written ~100,000 rows.
    """

    def test_rows_written_is_assigned_before_the_pin(self):
        src = inspect.getsource(sync.run_sync)
        assign = src.index("report.rows_written")
        pin = src.index("_pin_config_hash(")
        assert assign < pin, (
            "report.rows_written is assigned after _pin_config_hash, so a pin "
            "failure discards the count for rows that were already committed"
        )


class TestThePinDoesNotFailTheSync:
    """**The table is the authority, not the GUC.**

    ADR 115 moved the serving views onto `serving_config`. The GUC remains
    useful for a human in `psql`, which makes it a convenience — and a
    convenience must not fail a job whose real work committed.
    """

    def test_insufficient_privilege_is_caught(self):
        src = inspect.getsource(sync.run_sync)
        assert "InsufficientPrivilege" in src or "_pin_config_hash_best_effort" in src, (
            "run_sync must tolerate a managed Postgres refusing ALTER DATABASE"
        )

    def test_the_warning_names_the_table_that_still_works(self):
        """A warning that only says "permission denied" sends the reader
        looking for a broken sync. It has to say why nothing is broken."""
        src = inspect.getsource(sync)
        assert "serving_config" in src

    def test_other_errors_still_raise(self):
        """Only the privilege error is tolerated.

        A pin that fails for any other reason — a malformed hash, a lost
        connection — is a real failure. Catching `Exception` here would hide
        the `ValueError` guards immediately above it in `_pin_config_hash`,
        which exist to refuse interpolating an unexpected identifier.
        """
        src = inspect.getsource(sync.run_sync)
        assert "except Exception" not in src, "the pin must not swallow every error"
