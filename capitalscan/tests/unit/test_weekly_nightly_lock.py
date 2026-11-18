"""The weekly holds nightly's lock for its whole run (2026-11-17).

The two took separate locks, so on 2026-11-14 a nightly beside the weekly's
backtest deadlocked on `events` four times. These pin the wrapper text, the
way `test_premarket_slot.py` pins premarket's lock: the behaviour lives in
shell, and the regression to guard against is someone dropping the block.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _sh() -> str:
    return (REPO / "scripts" / "run_job.sh").read_text()


def _ps1() -> str:
    return (REPO / "scripts" / "run_job.ps1").read_text()


def _nightly_runtime_cap_sec() -> int:
    unit = (REPO / "scripts" / "systemd" / "capitalscan-nightly.service").read_text()
    m = re.search(r"^RuntimeMaxSec=(\d+)h\s*$", unit, re.M)
    assert m, "capitalscan-nightly.service lost its RuntimeMaxSec"
    return int(m.group(1)) * 3600


class TestShellWrapper:
    def test_weekly_takes_nightlys_lock(self):
        sh = _sh()
        assert 'if [ "$JOB" = weekly ]; then' in sh
        assert 'exec 8>"reports/nightly/nightly.lock"' in sh

    def test_it_waits_rather_than_skipping(self):
        # A skipped weekly would lose the week's refit; a waited one only
        # starts late.
        assert 'flock -w "$WEEKLY_NIGHTLY_WAIT_SEC" 8' in _sh()

    def test_a_wait_that_runs_out_fails_so_the_unit_retries(self):
        block = _sh().split('flock -w "$WEEKLY_NIGHTLY_WAIT_SEC" 8', 1)[1]
        assert "exit 1" in block.split("fi", 1)[0]

    def test_the_wait_outlasts_any_nightly_systemd_allows(self):
        m = re.search(r"^WEEKLY_NIGHTLY_WAIT_SEC=(\d+)$", _sh(), re.M)
        assert m
        assert int(m.group(1)) > _nightly_runtime_cap_sec()

    def test_it_comes_after_the_weeklys_own_lock(self):
        # Taking nightly's lock first would let two weekly triggers queue
        # on it; the weekly's own non-blocking lock must win or lose first.
        sh = _sh()
        assert sh.index('exec 9>"$LOGDIR/${LOCKJOB}.lock"') < sh.index(
            'exec 8>"reports/nightly/nightly.lock"'
        )

    def test_nightly_and_premarket_still_skip_on_a_held_lock(self):
        # The other half of the arrangement: what makes a held lock mean
        # "skip" for them is the existing non-blocking flock.
        sh = _sh()
        assert "if ! flock -n 9; then" in sh
        assert "LOCKJOB=nightly" in sh


class TestPowerShellMirror:
    def test_weekly_takes_nightlys_lock(self):
        ps1 = _ps1()
        assert "if ($Job -eq 'weekly')" in ps1
        assert "'nightly.lock'" in ps1

    def test_same_wait_and_failure(self):
        ps1 = _ps1()
        m = re.search(r"AddSeconds\((\d+)\)", ps1)
        assert m
        assert int(m.group(1)) > _nightly_runtime_cap_sec()
        assert "exit 1" in ps1.split("$nightlyLockPath after", 1)[1].split("}", 1)[0]
