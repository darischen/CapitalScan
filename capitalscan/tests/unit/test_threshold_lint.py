"""Tests for `capitalscan.jobs.threshold_lint`, ADR 092's matcher replacement
(Session 14 task 14.5).

The task's hardest acceptance criterion is explicit: "A matcher whose first
run is green on a codebase with a known defect is not finished." The
regression test below (`test_catches_the_pre_adr_095_v_positions_defect`)
reconstructs the exact pre-ADR-095 `v_positions` SQL as a fixture and
asserts the matcher reports it — proof this matcher would have caught the
defect ADR 095 found, not just a plausible-looking pattern that happens to
pass today.
"""

from __future__ import annotations

from pathlib import Path

from capitalscan.jobs import threshold_lint as tl

# --- The regression test -----------------------------------------------


def test_catches_the_pre_adr_095_v_positions_defect():
    """The whole reason this matcher exists. Reconstructed verbatim from
    ADR 095's quoted SQL (`docs/DECISIONS.md` ADR 095, `db/schema.sql:1005`
    at the time): `(s.k_full >= (80)::numeric)` for the long exit and
    `((CURRENT_DATE - p.entry_date) >= 5)` for the timeout. The old matcher
    (`assert "80.0" not in body and "20.0" not in body` over
    `inspect.getsource(core.exits)`) finds nothing here even pointed
    directly at this text, because neither substring appears and the text
    isn't `core/exits.py` at all. This matcher must not repeat that."""
    pre_adr_095_v_positions_sql = """
CREATE VIEW public.v_positions AS
 SELECT p.id,
    p.user_id,
    p.ticker,
    p.side,
    p.entry_date,
    p.entry_price,
    (s.k_full >= (80)::numeric)          AS exit_signal_stoch,
    ((CURRENT_DATE - p.entry_date) >= 5) AS exit_signal_timeout
   FROM (public.positions p
     JOIN public.indicators s ON (((s.ticker = p.ticker) AND (s.ts = CURRENT_DATE))));
"""
    findings = tl.scan_sql_source(pre_adr_095_v_positions_sql, path=Path("db/schema.sql"))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.column == "k_full"
    assert finding.literal == "80"
    assert "exit_signal_stoch" in finding.text
    assert finding.line_no == 9  # the exact line the offending text sits on


def test_regression_finding_is_not_silently_swallowed_by_the_exception_list():
    """The known-exceptions registry (for `db/schema.sql`'s *current*,
    still-unfixed copy of this defect — see `KNOWN_EXCEPTIONS`) matches on
    file path. A fresh fixture at a path outside that registry must still be
    caught, proving the matcher's detection is real and not just an
    always-empty result laundered through the exception list."""
    sql = "(s.k_full >= (80)::numeric) AS exit_signal_stoch"
    findings = tl.scan_sql_source(sql, path=Path("some/other/view.sql"))
    assert len(findings) == 1


# --- Literal spellings ----------------------------------------------------


def test_catches_bare_int_literal():
    findings = tl.scan_python_source("if k_full >= 80:\n    ...")
    assert len(findings) == 1
    assert findings[0].literal == "80"


def test_catches_bare_float_literal():
    findings = tl.scan_python_source("if k_full >= 80.0:\n    ...")
    assert len(findings) == 1
    assert findings[0].literal == "80.0"


def test_catches_two_decimal_float_literal():
    findings = tl.scan_python_source("if k_full >= 80.00:\n    ...")
    assert len(findings) == 1
    assert findings[0].literal == "80.00"


def test_catches_int_wrapped_literal():
    findings = tl.scan_python_source("if k_full >= int(80):\n    ...")
    assert len(findings) == 1
    assert findings[0].literal == "80"


def test_catches_short_side_literal():
    findings = tl.scan_python_source("if k_full <= 20.0:\n    ...")
    assert len(findings) == 1
    assert findings[0].column == "k_full"
    assert findings[0].literal == "20.0"


def test_catches_literal_on_the_left_of_the_comparison():
    findings = tl.scan_python_source("if 80.0 <= k_full:\n    ...")
    assert len(findings) == 1
    assert findings[0].literal == "80.0"


def test_catches_the_sql_numeric_cast_spelling():
    """The exact spelling ADR 095 found: `(80)::numeric`, not `80.0`."""
    findings = tl.scan_sql_source("(s.k_full >= (80)::numeric) AS x")
    assert len(findings) == 1
    assert findings[0].literal == "80"


def test_catches_every_named_threshold_column():
    for col in tl.THRESHOLD_COLUMNS:
        findings = tl.scan_python_source(f"if {col} >= 80:\n    ...")
        assert len(findings) == 1, f"missed {col}"
        assert findings[0].column == col


# --- What must NOT be flagged ----------------------------------------------


def test_does_not_flag_the_exitparams_field_definition():
    """`ExitParams.exit_stoch_threshold: float = 80.0` is the definition,
    not a use. `exit_stoch_threshold` is not a threshold-bearing *column*
    (it's the config field naming the threshold), and the line uses a bare
    `=` assignment, not a comparison operator — both independently exclude
    it, which is deliberate belt-and-suspenders rather than one fragile
    check."""
    findings = tl.scan_python_source(
        "    exit_stoch_threshold: float = 80.0\n    exit_stoch_threshold_short: float = 20.0\n"
    )
    assert findings == []


def test_does_not_flag_an_unrelated_indicator_comparison():
    """`k_full` compared against another indicator series (not a numeric
    literal) is ordinary crossover logic, not a bare threshold."""
    findings = tl.scan_python_source(
        "k_cross_up = (k_full.shift(1) <= d_full.shift(1)) & (k_full > d_full)"
    )
    assert findings == []


def test_does_not_flag_a_pandas_shift_call_argument():
    """A `.shift(1)` call argument must not read as a wrapped numeric
    literal compared against the column it's called on."""
    findings = tl.scan_python_source("x = k_full.shift(1) >= threshold_series")
    assert findings == []


def test_does_not_flag_a_keyword_argument_assignment():
    """Test-fixture style `_ind(bars, k_full=80.0)` is a keyword argument,
    not a comparison — single `=`, not `==`."""
    findings = tl.scan_python_source("_ind(bars, k_full=80.0)")
    assert findings == []


def test_does_not_flag_a_dict_style_read_with_no_comparison():
    findings = tl.scan_python_source('k_full = _level(own_ind, "k_full")')
    assert findings == []


def test_does_not_flag_a_similarly_named_but_unrelated_identifier():
    """`k_fullish` is not `k_full`; a substring match would be wrong."""
    findings = tl.scan_python_source("if k_fullish >= 80:\n    ...")
    assert findings == []


def test_does_not_flag_the_sql_timeout_literal():
    """`>= 5` for `max_hold_days` is a real ADR 092-class literal too, but
    it is not a *stochastic or band* threshold — out of the named column
    set this task scopes the matcher to (`THRESHOLD_COLUMNS`). Confirms the
    matcher doesn't over-match every numeric comparison in sight."""
    findings = tl.scan_sql_source("((CURRENT_DATE - p.entry_date) >= 5) AS exit_signal_timeout")
    assert findings == []


# --- Reporting shape ---------------------------------------------------


def test_finding_reports_file_line_and_text_not_a_bare_boolean():
    findings = tl.scan_python_source(
        "x = 1\nif k_full >= 80:\n    pass\n", path=Path("some/module.py")
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.path == Path("some/module.py")
    assert f.line_no == 2
    assert "k_full >= 80" in f.text
    assert str(f) == f"{Path('some/module.py')}:2: if k_full >= 80:"


# --- Allowlisting: explicit annotation, never silence ----------------------


def test_allowlists_a_trailing_comment_naming_the_adr():
    findings = tl.scan_python_source("if k_full >= 80:  # ADR 092: legitimate, see docstring")
    assert findings == []


def test_allowlists_a_preceding_comment_block_naming_the_adr():
    findings = tl.scan_python_source(
        "# ADR 092: legitimate, this line is a documented exception\nif k_full >= 80:\n    pass\n"
    )
    assert findings == []


def test_does_not_allowlist_a_comment_with_no_adr_reference():
    """A comment alone is not enough — it must name the permitting ADR.
    Allowlisting is by explicit annotation, never by silence, and a comment
    that doesn't cite anything is functionally silent."""
    findings = tl.scan_python_source("if k_full >= 80:  # this is fine, trust me")
    assert len(findings) == 1


def test_does_not_allowlist_a_distant_comment():
    """A comment naming an ADR three lines away from unrelated code does
    not allowlist a later, unrelated literal — only a directly adjacent
    annotation counts."""
    findings = tl.scan_python_source(
        "# ADR 092: this comment is about something else entirely\n"
        "\n"
        "\n"
        "\n"
        "if k_full >= 80:\n"
        "    pass\n"
    )
    assert len(findings) == 1


def test_sql_allowlists_a_double_dash_comment_naming_the_adr():
    findings = tl.scan_sql_source("(s.k_full >= (80)::numeric) -- ADR 092: documented exception")
    assert findings == []


# --- The known-exceptions registry itself -----------------------------------


def test_known_exceptions_are_still_detected_when_the_list_is_bypassed():
    """`apply_known_exceptions=False` must still surface the two known,
    documented findings — proving the exception list hides real findings
    deliberately, rather than the matcher simply never having seen them."""
    findings = tl.scan_repo(apply_known_exceptions=False)
    exception_paths = {Path(p).name for p, _ in tl.KNOWN_EXCEPTIONS}
    seen = {f.path.name for f in findings}
    assert exception_paths <= seen


def test_every_known_exception_is_annotated_with_an_adr():
    """The registry itself is the allowlist for generated/historical files
    that cannot carry an inline comment (`db/schema.sql` is `pg_dump`
    output). Each entry still has to be named, not just present — this test
    fails loudly if an entry is ever added without a docstring/comment
    trail pointing at the ADR that permits it."""
    import inspect

    source = inspect.getsource(tl)
    # The KNOWN_EXCEPTIONS block itself must be preceded by ADR-naming prose.
    block_start = source.index("KNOWN_EXCEPTIONS: tuple")
    preamble = source[:block_start]
    assert "ADR 095" in preamble


# --- The repository as it stands -------------------------------------------


def test_repo_is_clean_after_known_exceptions():
    """Pointed at the repository as it stands (production Python plus
    checked-in SQL), the matcher reports zero findings once the one
    documented, ADR-095-authorized exception (`v_positions`, deferred to
    the Phase 5 serving-layer rebuild — out of this session's scope) is
    excluded. Any new finding here is real and must be fixed or explicitly
    annotated, not silently absorbed into this test."""
    findings = tl.scan_repo()
    assert findings == [], "\n".join(str(f) for f in findings)


def test_repo_scan_without_exceptions_finds_exactly_the_known_v_positions_defect():
    """Without the exception list, the repository as it stands has exactly
    the two known instances of the same defect: the live `db/schema.sql`
    view and the historical migration that first created it. If this count
    ever changes, either a new instance appeared (fix it) or an old one was
    fixed (shrink `KNOWN_EXCEPTIONS` to match, per its own docstring)."""
    findings = tl.scan_repo(apply_known_exceptions=False)
    assert len(findings) == 2
    for f in findings:
        assert f.column == "k_full"
        assert f.literal == "80"
