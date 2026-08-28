"""The serving sweep runs after a successful sync, not before it.

**The incident, 2026-08-26.** `nightly` swept the Pi's provisional poller
rows for the session, then the sync died 53.8 minutes in. Serving held
**zero** events for the current session while research held 670 — not
stale, empty. The site showed nothing for that day.

The original ordering was deliberate and its comment said why: "Runs before
`run_sync` so the authoritative rows land after the provisional ones are
gone, never the reverse." That reasoning is sound about the *steady state*
and wrong about the *failure*: it converts a sync failure into visible data
loss.

**Reversed, the worst case is superseded rows surviving one night.** They
are stale rather than absent, ADR 140 says they were never authoritative,
and the next successful sync sweeps them. Absent rows have no such
recovery: nothing re-creates them until a full sync is run by hand.

The trigger was physical — the Pi is on WiFi with power-save enabled and
was being handled — so the failure recurs by nature and cannot be argued
away.

These tests pin the ordering in the source. A behavioural test would need
both databases and a mid-sync failure, which is exactly what the
integration tests may not do against live data (CLAUDE.md).
"""

from __future__ import annotations

import inspect
import re

from capitalscan.jobs import cli


def _nightly_source() -> str:
    return inspect.getsource(cli.nightly)


class TestOrdering:
    def test_the_serving_sweep_follows_the_sync_call(self):
        """The regression this file exists for. If the sweep moves back
        above `run_sync`, a failed sync deletes a session from serving
        again."""
        src = _nightly_source()
        sync_at = src.index("run_sync(")
        sweep_at = src.index("serving_engine()")
        assert sweep_at > sync_at, (
            "the serving sweep runs before run_sync; a sync that fails "
            "mid-way then leaves serving with a session deleted and "
            "nothing to replace it (2026-08-26)"
        )

    def test_the_research_sweep_still_runs_first(self):
        """Only the *serving* sweep moved. Research is the source of truth
        and its sweep has no such coupling."""
        src = _nightly_source()
        assert src.index("_sweep_provisional_poll_rows(engine") < src.index("run_sync(")

    def test_the_sweep_is_guarded_by_the_sync_outcome(self):
        """Sweeping after a *failed* sync is the original bug with extra
        steps. The call must be conditional, not merely later."""
        src = _nightly_source()
        tail = src[src.index("run_sync(") :]
        head = tail[: tail.index("serving_engine()")]
        assert re.search(r"sync_ok|if\s+sync_report|synced\s*=", head), (
            "the serving sweep must be conditional on the sync having "
            "succeeded, not just sequenced after it"
        )


class TestReasonIsRecorded:
    def test_the_source_explains_why_the_order_is_what_it_is(self):
        """This ordering has been reasoned about twice in opposite
        directions. The next reader needs the incident, not just the
        order."""
        src = _nightly_source()
        assert "2026-08-26" in src
