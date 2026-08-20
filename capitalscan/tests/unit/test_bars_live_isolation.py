"""`bars_live` is invisible to everything that computes an indicator.

ADR 128. Today's partial candle lives in its own table for one reason: a
partial row in `bars` would get an indicator row from `cscan indicators`,
and `run_events` / `poll` read the latest indicator *strictly before* the
bar — so today's unfinished values would become tomorrow's t−1.

That is invariant 3's failure mode and it is silent. Every band would
tighten around a price that had not finished happening, every stochastic
would read a close that was still moving, and nothing would raise. The
signal would look fine and be wrong.

The separation is what makes it structural rather than remembered. These
tests are what keep the separation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3] / "capitalscan"

# Modules that compute, read, or act on indicators. None of them may know
# `bars_live` exists.
INDICATOR_PATHS = (
    "core",
    "research",
)

# `jobs/` is checked file by file: the poller legitimately *writes*
# `bars_live`, and `views.py` holds its DDL.
#
# `sync.py` is here for a weaker reason and gets a stronger check.
# It never touches the table — it *explains why it does not ship it*, and
# that paragraph has to name it to be worth anything. A string scan cannot
# tell an exclusion from a use, so the real property is asserted against
# the data structure instead: `test_sync.py::test_the_live_session_is_not_
# shipped` checks `bars_live` is absent from the synced table list. That
# survives a rewording of the comment, which this scan would not.
JOBS_ALLOWED = {"poll.py", "views.py", "sync.py"}


def _sources(package: str) -> list[Path]:
    return sorted((ROOT / package).rglob("*.py"))


@pytest.mark.parametrize(
    "path",
    [p for pkg in INDICATOR_PATHS for p in _sources(pkg)],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_no_indicator_module_mentions_bars_live(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "bars_live" not in text, (
        f"{path.name} mentions bars_live. That table holds *partial* rows for "
        "the current session. Anything that computes or consumes an indicator "
        "must never see one — a partial bar becomes tomorrow's t-1 and the "
        "look-ahead is silent (invariant 3, ADR 128)."
    )


@pytest.mark.parametrize("path", _sources("jobs"), ids=lambda p: p.name)
def test_only_the_poller_and_the_ddl_touch_bars_live(path: Path) -> None:
    if path.name in JOBS_ALLOWED:
        return
    text = path.read_text(encoding="utf-8")
    assert "bars_live" not in text, (
        f"jobs/{path.name} mentions bars_live. Only the poller writes it and "
        f"only views.py declares it; everything else in jobs/ reads closed "
        "bars. Add it to JOBS_ALLOWED with a reason if that changed."
    )


def test_the_indicator_job_reads_bars_and_not_bars_live() -> None:
    """The specific query that would cause the bug.

    `cscan indicators` selects from `bars`. If it ever selected from
    `bars_live`, or from a union of the two, a partial row would get an
    indicator row and the whole separation would be pointless.
    """
    text = (ROOT / "jobs" / "compute.py").read_text(encoding="utf-8")
    assert "bars_live" not in text, (
        "compute.py mentions bars_live. `run_indicators` and `run_events` "
        "must only ever see closed bars."
    )


def test_the_poller_writes_bars_live_and_not_bars() -> None:
    """The inverse. The poller must not write a partial row into `bars`,
    which is the shortcut this whole design exists to refuse."""
    text = (ROOT / "jobs" / "poll.py").read_text(encoding="utf-8")
    assert "bars_live" in text, "the poller stopped writing bars_live"

    # Every `db_io.upsert` target in the poller, as a literal.
    targets = set(re.findall(r'db_io\.upsert\(\s*engine,\s*"(\w+)"', text))
    assert "bars" not in targets, (
        f"the poller upserts into {sorted(targets)}, which includes `bars`. "
        "A partial row there gets an indicator row and becomes tomorrow's "
        "t-1 (ADR 128)."
    )
    assert "bars_live" in targets, f"the poller upserts into {sorted(targets)}"


def test_bars_live_is_keyed_one_row_per_ticker_per_session() -> None:
    """Yahoo's day high/low are cumulative session extremes, so a later
    quote is strictly better information. The primary key is what makes
    each tick an overwrite rather than an append — ~141 rows a day instead
    of ~11,000, and no accumulation logic to get wrong."""
    from capitalscan.jobs.views import BARS_LIVE_DDL

    assert "PRIMARY KEY (ticker, session_date)" in BARS_LIVE_DDL


def test_bars_live_requires_a_close_and_nothing_else() -> None:
    """`close` is the current price; a row without one describes nothing.

    Everything else is nullable because a pre-market quote can carry a
    price and no high, and invariant 4 says absent stays absent rather than
    defaulting to the price.
    """
    from capitalscan.jobs.views import BARS_LIVE_DDL

    assert "close         numeric(12,4) NOT NULL" in BARS_LIVE_DDL
    for column in ("open", "high", "low", "volume"):
        line = next(line for line in BARS_LIVE_DDL.splitlines() if line.strip().startswith(column))
        assert "NOT NULL" not in line, f"{column} is NOT NULL; absent must stay absent"
