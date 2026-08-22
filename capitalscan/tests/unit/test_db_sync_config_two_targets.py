"""`cscan db sync-config` must reach the serving store, not only research.

Found 2026-08-21, when the deployed site kept showing the previous session
for five hours after a `config_hash` change, and the diagnosis went to the
server and the browser before the table.

`db_sync_config` wrote one row through `db_io.get_engine()` — the research
store — and had no second target. `cscan db migrate` already applies to both
by default and prints a visible `skip <target>: <VAR> not set` when one is
unconfigured; this command did not.

**Why a stale row is invisible rather than loud.** `web/lib/db.ts` pins every
connection from the table (ADR 115):

    SELECT set_config('capitalscan.default_config_hash',
                      (SELECT config_hash FROM serving_config LIMIT 1), false)

So `ALTER DATABASE ... SET` has no effect on the deployed site, and a stale
row makes it query a config generation nobody writes to. It renders empty
with no error, because `current_setting(..., true)` returns NULL rather than
raising.

`test_v_positions_config.py::test_the_stored_row_matches_the_live_config`
encodes the rule and cannot enforce it here: it runs against research, so it
stays green while serving is stale.
"""

from __future__ import annotations

import inspect

from capitalscan.jobs import cli


class TestItWritesBothStores:
    def test_it_resolves_a_serving_target(self):
        src = inspect.getsource(cli.db_sync_config)
        assert "DATABASE_URL_SERVING" in src or "serving_engine" in src, (
            "db_sync_config writes only the research store, so the deployed "
            "site keeps reading the previous config generation"
        )

    def test_an_unset_serving_url_skips_visibly(self):
        """Matching `cscan db migrate`.

        A chain that quietly stops one step short is how a deployed site
        goes stale without anyone noticing — which is exactly what happened.
        """
        src = inspect.getsource(cli.db_sync_config)
        assert "skip" in src

    def test_a_serving_failure_does_not_lose_the_research_write(self):
        """Research is the source of truth and is written first.

        A network problem reaching the cloud copy must not leave the local
        row unwritten, the same reasoning `cscan nightly` applies to its own
        sync step.
        """
        src = inspect.getsource(cli.db_sync_config)
        research_write = src.index("SERVING_CONFIG_UPSERT")
        serving_mention = max(
            src.find("DATABASE_URL_SERVING"),
            src.find("serving_engine"),
        )
        assert serving_mention > research_write, (
            "the serving write must come after the research write, so a "
            "failure reaching serving cannot discard the local one"
        )
