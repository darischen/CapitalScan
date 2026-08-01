"""The frozen-file guard for `run_membership` (ADR 055).

ADR 055 makes the universe CSV a once-and-frozen artifact: scraped once,
reviewed by hand, committed. Nothing in the job enforced that, so a
re-run silently rewrote the file and reset every `needs_review` flag,
discarding the review pass. These tests pin the guard that stops it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from capitalscan.jobs.ingest import UniverseFrozenError, is_reviewed


def _write(path: Path, flags: list[bool]) -> Path:
    pd.DataFrame(
        {
            "ticker": [f"T{i}" for i in range(len(flags))],
            "added": [None] * len(flags),
            "removed": [None] * len(flags),
            "needs_review": flags,
        }
    ).to_csv(path, index=False)
    return path


class TestIsReviewed:
    def test_a_fully_signed_off_file_is_reviewed(self, tmp_path: Path):
        path = _write(tmp_path / "u.csv", [False, False, False])
        assert is_reviewed(path) is True

    def test_a_file_with_any_pending_row_is_not_reviewed(self, tmp_path: Path):
        path = _write(tmp_path / "u.csv", [False, True, False])
        assert is_reviewed(path) is False

    def test_a_missing_file_is_not_reviewed(self, tmp_path: Path):
        assert is_reviewed(tmp_path / "absent.csv") is False

    def test_an_empty_file_is_not_reviewed(self, tmp_path: Path):
        path = _write(tmp_path / "u.csv", [])
        assert is_reviewed(path) is False

    def test_a_file_without_the_column_is_not_reviewed(self, tmp_path: Path):
        """Can't prove a review happened, so don't block on it."""
        path = tmp_path / "u.csv"
        pd.DataFrame({"ticker": ["AAPL"]}).to_csv(path, index=False)
        assert is_reviewed(path) is False

    def test_an_unparseable_file_is_not_reviewed(self, tmp_path: Path):
        path = tmp_path / "u.csv"
        path.write_text("not,a,valid\ncsv\x00\x00", encoding="utf-8")
        assert is_reviewed(path) is False


class TestRunMembershipGuard:
    """`run_membership` must refuse a reviewed file unless forced."""

    def test_refuses_to_clobber_a_reviewed_file(self, tmp_path, monkeypatch):
        from capitalscan.jobs import ingest

        path = _write(tmp_path / "universe_union.csv", [False, False])
        monkeypatch.setattr(ingest, "UNIVERSE_CSV", path)
        original = path.read_text(encoding="utf-8")

        with pytest.raises(UniverseFrozenError) as excinfo:
            ingest.run_membership()

        assert "--force" in str(excinfo.value)
        assert path.read_text(encoding="utf-8") == original, "file was modified anyway"

    def test_the_guard_fires_before_any_network_call(self, tmp_path, monkeypatch):
        """A refusal must not cost a scrape of Wikipedia."""
        from capitalscan.jobs import ingest

        path = _write(tmp_path / "universe_union.csv", [False])
        monkeypatch.setattr(ingest, "UNIVERSE_CSV", path)

        def boom(*args, **kwargs):
            raise AssertionError("network call made despite the guard")

        monkeypatch.setattr(ingest.wikipedia, "fetch_current_constituents", boom)
        monkeypatch.setattr(ingest.wikipedia, "fetch_membership_changes", boom)

        with pytest.raises(UniverseFrozenError):
            ingest.run_membership()

    def test_force_overrides_the_guard(self, tmp_path, monkeypatch):
        from capitalscan.jobs import ingest

        path = _write(tmp_path / "universe_union.csv", [False])
        monkeypatch.setattr(ingest, "UNIVERSE_CSV", path)
        monkeypatch.setattr(
            ingest.wikipedia,
            "fetch_current_constituents",
            lambda: pd.DataFrame({"ticker": ["AAPL", "MSFT"]}),
        )
        monkeypatch.setattr(
            ingest.wikipedia,
            "fetch_membership_changes",
            lambda: pd.DataFrame(
                {
                    "effective_date": ["June 15, 2020"],
                    "added_ticker": ["NVDA"],
                    "removed_ticker": [None],
                }
            ),
        )

        report = ingest.run_membership(force=True)

        assert report.rows_written > 0
        assert set(pd.read_csv(path)["ticker"]) == {"AAPL", "MSFT", "NVDA"}

    def test_an_unreviewed_file_is_regenerated_without_force(self, tmp_path, monkeypatch):
        """The normal first-run and re-scrape path stays frictionless."""
        from capitalscan.jobs import ingest

        path = _write(tmp_path / "universe_union.csv", [True, True])
        monkeypatch.setattr(ingest, "UNIVERSE_CSV", path)
        monkeypatch.setattr(
            ingest.wikipedia,
            "fetch_current_constituents",
            lambda: pd.DataFrame({"ticker": ["AAPL"]}),
        )
        monkeypatch.setattr(
            ingest.wikipedia,
            "fetch_membership_changes",
            lambda: pd.DataFrame({"effective_date": [], "added_ticker": [], "removed_ticker": []}),
        )

        report = ingest.run_membership()

        assert list(pd.read_csv(path)["ticker"]) == ["AAPL"]
        assert report.rows_written == 1
