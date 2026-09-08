"""The forward log's resolver: the one measurement nothing can contaminate.

Every other number about this model comes from a split that has been
reused. A prediction recorded before its outcome existed is the exception,
and this job is what turns those rows into a score. The properties below
are the ones that would let contamination back in without any test failing.
"""

from __future__ import annotations

from capitalscan.jobs import outcomes as oc
from capitalscan.tests.unit._probe import code_of


class TestItOnlyResolvesClosedWindows:
    def test_the_sql_requires_a_closed_forward_window(self) -> None:
        """A partial window scores against a smaller-magnitude number
        wearing a complete label -- ADR 094's staleness class."""
        assert "e.peak_ret_5d IS NOT NULL" in oc.RESOLVE_SQL
        assert "e.fwd_ret_5d IS NOT NULL" in oc.RESOLVE_SQL

    def test_it_joins_on_the_event_and_not_on_ticker_and_date(self) -> None:
        """`(ticker, as_of)` is not a key: a name can fire twice in one day
        on opposite sides, and scoring one side's prediction against the
        other's outcome would be silently wrong."""
        assert "e.id = p.event_id" in oc.RESOLVE_SQL


class TestARecordedOutcomeIsNeverRewritten:
    def test_it_does_nothing_on_conflict(self) -> None:
        """`DO UPDATE` would let a changed label quietly improve the
        model's recorded track record -- the exact failure this table
        exists to make impossible."""
        assert "ON CONFLICT (prediction_id) DO NOTHING" in oc.RESOLVE_SQL
        assert "DO UPDATE" not in oc.RESOLVE_SQL

    def test_the_job_never_deletes_from_outcomes(self) -> None:
        src = code_of(oc)
        assert "DELETE" not in src.upper()
        assert "TRUNCATE" not in src.upper()


class TestItScoresTheRightQuantities:
    def test_the_adverse_column_is_the_fixed_window_not_mae(self) -> None:
        """`events.mae` runs until the trade exits, so it carries
        `ExitParams` and is not comparable across config generations. ADR
        175 added `trough_ret_5d` precisely so this row scores a five-day
        window like everything beside it."""
        assert "e.trough_ret_5d" in oc.RESOLVE_SQL
        assert "e.mae" not in oc.RESOLVE_SQL

    def test_the_touch_flags_match_their_thresholds(self) -> None:
        for threshold in ("0.02", "0.03", "0.05"):
            assert f"e.peak_ret_5d >= {threshold}" in oc.RESOLVE_SQL
        # 10% reads the 10-day head, as `research.predict.TARGETS` does.
        assert "e.peak_ret_10d >= 0.10" in oc.RESOLVE_SQL

    def test_the_brier_score_is_against_the_headline_threshold(self) -> None:
        assert oc.BRIER_THRESHOLD == 3
        assert "p.p_touch_3" in oc.RESOLVE_SQL
        assert "power(p.p_touch_3 - (e.peak_ret_5d >= 0.03)::int, 2)" in oc.RESOLVE_SQL


class TestThePinballLoss:
    def test_it_covers_every_stored_quantile_at_its_own_tau(self) -> None:
        sql = oc._pinball_sql()
        for col, tau in oc.FAN:
            assert f"p.{col}" in sql
            assert str(tau) in sql or str(1 - tau) in sql

    def test_the_taus_match_the_writer(self) -> None:
        """A quantile scored against the wrong tau produces a plausible
        loss and no error."""
        from capitalscan.research import predict as rp

        assert tuple(tau for _, tau in oc.FAN) == rp.FAN_TAUS
        assert tuple(col for col, _ in oc.FAN) == rp._FAN_COLUMNS

    def test_it_is_averaged_over_the_fan_not_summed(self) -> None:
        assert f"/ {len(oc.FAN)}.0" in oc._pinball_sql()

    def test_both_branches_of_the_check_function_are_present(self) -> None:
        """`tau * (y - q)` above and `(1 - tau) * (q - y)` below. One
        branch alone is a one-sided penalty that every quantile passes."""
        sql = oc._pinball_sql()
        assert "e.fwd_ret_5d >= p.q05" in sql
        assert sql.count("CASE WHEN") == len(oc.FAN)


class TestTheReportDoesNotOverclaim:
    def test_score_returns_raw_aggregates_and_no_skill_number(self) -> None:
        """Brier skill over a handful of resolved rows invites reading
        noise as evidence, so the caller decides when there is enough."""
        src = code_of(oc.score)
        assert "skill" not in src.lower()
        assert "brier" in src.lower()

    def test_the_summary_says_what_is_still_open(self) -> None:
        report = oc.OutcomeReport(resolved=10, already=3, pending=7)
        text = report.summary()
        assert "10" in text and "3" in text and "7" in text
        assert "still open" in text


class TestItIsSafeToScheduleNightly:
    def test_it_records_a_run_row(self) -> None:
        """Invariant 6, and how a silent failure becomes visible."""
        assert "run_job" in code_of(oc.run_outcomes)

    def test_it_takes_no_tuning_parameter(self) -> None:
        """The job must have nothing that could be adjusted toward a nicer
        answer. Its only argument is the engine."""
        import inspect

        params = list(inspect.signature(oc.run_outcomes).parameters)
        assert params == ["engine"]


class TestEveryLabelFamilyHasAWriter:
    """A label column with no writer is the `events.giveback` failure.

    That column was added by migration `699cb410d219` and stayed NULL on
    all 5.57M rows because nothing ever wrote it. ADR 175 nearly repeated
    it: `trough_ret_*` went into `features.LABEL_COLS`, and both the
    `path peak-labels` command and the `nightly` chain refreshed only the
    peak family. New events would have carried a NULL trough forever, and
    `build_training_frame` drops any row missing a label -- so the training
    set would have shrunk silently, with no error anywhere.
    """

    @staticmethod
    def _cli_source() -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[2] / "jobs" / "cli.py").read_text(encoding="utf-8")

    def test_the_cli_backfills_every_family(self) -> None:
        src = self._cli_source()
        assert "backfill_peak_labels" not in src, (
            "the peak-only writer refreshes one of two label families"
        )
        assert src.count("backfill_extremum_labels") >= 2, (
            "both the path command and the nightly chain must refresh labels"
        )
        assert src.count("for family in FAMILIES") >= 2

    def test_every_label_column_belongs_to_a_writable_family(self) -> None:
        """The real invariant: nothing in `LABEL_COLS` is unwritable."""
        from capitalscan.research import features as feat
        from capitalscan.research.peak_labels import FAMILIES

        writable = {
            template.format(h=h) for _, _, template in FAMILIES.values() for h in (1, 2, 3, 5, 10)
        } | {f"fwd_ret_{h}d" for h in (1, 2, 3, 5, 10)}

        for col in feat.LABEL_COLS:
            assert col in writable, f"{col} is a label no writer produces"


class TestCosmeticRowsStayOutOfThePathPipeline:
    """ADR 178's cosmetic rows are for display and nothing else.

    `--cosmetic` prices events in neither universe so the ticker page can
    show a number instead of "outside universe". Those rows must not enter
    `path`, because `path` feeds `peak_ret_*`/`trough_ret_*`, which feed
    `features.LABEL_COLS`, which feeds the model. A display concession that
    reached the training frame would be a silent population change.

    **Measured 2026-09-08:** 3,609,960 cosmetic rows took nightly's
    `path_capture` from a 97-second average to over an hour, walking events
    nothing reads. Both the ticker-list queries and the per-ticker query
    filter on `entry_price IS NOT NULL` alone, which is exactly why they
    swept the cosmetic rows in.
    """

    def test_the_per_ticker_query_filters_the_population(self) -> None:
        from capitalscan.research.path_backfill import _events_query_for_ticker

        query, _ = _events_query_for_ticker("AAPL", 10, True, "chash")
        assert "(in_trade OR in_watch)" in query

    def test_every_events_read_in_the_path_module_filters_it(self) -> None:
        """The two ticker-list queries are easy to miss: they select
        `DISTINCT ticker` rather than events, so a reader checking "does
        this read events" can skim past them."""
        from pathlib import Path

        from capitalscan.research import path_backfill

        src = Path(path_backfill.__file__).read_text(encoding="utf-8")
        reads = src.count("FROM events")
        guarded = src.count("(in_trade OR in_watch)")
        assert guarded >= reads, (
            f"{reads} reads of `events` in path_backfill but only {guarded} "
            "carry the population filter"
        )
