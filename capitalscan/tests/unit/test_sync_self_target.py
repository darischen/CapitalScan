"""A sync must never write to its own source (ADR 153).

`serving_engine()` refuses to *fall back* to the research URL, which covers
the unset case. It cannot cover the set-but-wrong case: on a workstation
that hosts research, `localhost` is a perfectly valid URL to exactly the
wrong database.

That failure is silent in the shape that matters. Every row upserts onto
itself, every run reports `ok`, and the deployed site simply never changes.
ADR 153 runs this after every poll tick, so a wrong URL looks healthy 78
times a session.

`create_engine` does not connect, so these need no database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from capitalscan.jobs.sync import _refuse_self_sync

RESEARCH = "postgresql+psycopg://capscan:pw@localhost:5432/capitalscan"
SERVING = "postgresql+psycopg://capscan:pw@192.168.1.30:5432/capitalscan_serving"


def _engines(source: str, target: str):
    return create_engine(source), create_engine(target)


def test_the_real_configuration_passes():
    """The workstation's own setup: research local, serving on the Pi."""
    _refuse_self_sync(*_engines(RESEARCH, SERVING))


def test_an_identical_url_is_refused():
    """The plain case, and the one a copied `.env.local` produces."""
    with pytest.raises(RuntimeError, match="resolves to the research database"):
        _refuse_self_sync(*_engines(RESEARCH, RESEARCH))


@pytest.mark.parametrize("spelling", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_spellings_do_not_defeat_it(spelling: str):
    """`localhost` and `127.0.0.1` are the same server.

    A guard that a spelling change slips past is not a guard, and this is
    the exact form the mistake takes: the serving URL gets written by hand
    while the research one was copied.
    """
    target = f"postgresql+psycopg://capscan:pw@{spelling}:5432/capitalscan"
    with pytest.raises(RuntimeError):
        _refuse_self_sync(*_engines(RESEARCH, target))


def test_the_same_database_name_on_another_host_is_allowed():
    """Not a false positive.

    The Pi could legitimately call its database `capitalscan`. Matching on
    the name alone would refuse a correct configuration, which is worse
    than the bug being prevented -- it breaks something that works.
    """
    target = "postgresql+psycopg://capscan:pw@192.168.1.30:5432/capitalscan"
    _refuse_self_sync(*_engines(RESEARCH, target))


def test_another_database_on_the_same_host_is_allowed():
    """The other half of the same point.

    Running serving in a second database on this machine is a reasonable
    development setup. Matching on host alone would refuse it.
    """
    target = "postgresql+psycopg://capscan:pw@localhost:5432/capitalscan_serving"
    _refuse_self_sync(*_engines(RESEARCH, target))


def test_the_message_names_the_variable_and_what_goes_wrong():
    """An error that only says "refused" sends the reader to the wrong
    place. This one has to name `DATABASE_URL_SERVING` and say that the
    symptom is a site that never changes, because that is what the reader
    will have observed before they see this."""
    with pytest.raises(RuntimeError) as err:
        _refuse_self_sync(*_engines(RESEARCH, RESEARCH))
    message = str(err.value)
    assert "DATABASE_URL_SERVING" in message
    assert "never changes" in message


def test_both_sync_paths_are_guarded():
    """The nightly has the same hazard as the per-tick push, and the nightly
    one is worse: `run_sync` copies the whole subset, so a self-target
    rewrites millions of rows onto themselves."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "capitalscan/jobs/sync.py").read_text(
        encoding="utf-8"
    )
    assert text.count("_refuse_self_sync(source, target)") == 2
