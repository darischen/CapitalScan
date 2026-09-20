"""The floor comparison is spelled once (2026-09-20).

`_reset_sequences` discovers table and column from the catalog, so its
branch is a `format('...', r.col, r.tbl)` string. `predictions_max_id_sql`
knows its table by name, so it interpolates directly. Those are two
renderings of one comparison, and until this they were two hand-written
copies: reversing `WHERE id < floor` in the catalog branch would have left
`scripts/verify_id_floor.py` green, because the script executes the other
copy. That clause is the only thing keeping research's ids below the floor.

So both renderings come from one template, and these tests fail if a future
edit reintroduces a second spelling.
"""

from __future__ import annotations

from capitalscan.jobs import sync

FLOOR = 1_000_000_000


def _comparison(sql: str) -> str:
    """The part of a rendered SELECT that decides the floor semantics."""
    return sql.split("FROM", 1)[1] if "FROM" in sql else sql


class TestOneTemplatePerStore:
    def test_the_catalog_branch_renders_from_the_shared_template(self) -> None:
        rendered = sync._predictions_branch(serving=False, floor=FLOOR)
        assert "<" in _comparison(rendered)
        assert str(FLOOR) in rendered

    def test_serving_renders_greatest_on_both_paths(self) -> None:
        catalog = sync._predictions_branch(serving=True, floor=FLOOR)
        literal = sync.predictions_max_id_sql("predictions", FLOOR, serving=True)
        assert "greatest" in catalog
        assert "greatest" in literal

    def test_research_renders_the_same_direction_on_both_paths(self) -> None:
        """The sharp one. A reversed comparison in either rendering must show
        up here, because both are read out of one template."""
        catalog = sync._predictions_branch(serving=False, floor=FLOOR)
        literal = sync.predictions_max_id_sql("predictions", FLOOR, serving=False)
        assert "<" in _comparison(catalog)
        assert ">" not in _comparison(catalog)
        assert "<" in _comparison(literal)
        assert ">" not in _comparison(literal)

    def test_a_changed_template_moves_both_renderings(self, monkeypatch) -> None:  # noqa: ANN001
        """Single-source, proved by perturbation: swap the template and both
        renderings must follow. Two hand-written copies would fail this."""
        monkeypatch.setitem(
            sync._MAX_ID_TEMPLATES,
            "research",
            "SELECT coalesce(max({col}),0) FROM {tbl} WHERE {col} <> {floor}",
        )
        catalog = sync._predictions_branch(serving=False, floor=FLOOR)
        literal = sync.predictions_max_id_sql("predictions", FLOOR, serving=False)
        assert "<>" in catalog
        assert "<>" in literal


class TestTheCatalogRenderingStaysValidPlpgsql:
    def test_placeholder_arguments_follow_the_order_they_appear(self) -> None:
        """`format()` binds positionally, so a template edit that moves a
        placeholder must move its argument too. Asserted as the exact
        rendering, because "runs and reads the wrong column" is the failure
        this prevents and it raises nothing."""
        assert sync._predictions_branch(serving=False, floor=FLOOR) == (
            "      EXECUTE format('SELECT coalesce(max(%I),0) FROM %s WHERE %I < %L', "
            "r.col, r.tbl, r.col, 1000000000) INTO n;"
        )
        assert sync._predictions_branch(serving=True, floor=FLOOR) == (
            "      EXECUTE format('SELECT greatest(coalesce(max(%I),0), %L) FROM %s', "
            "r.col, 1000000000, r.tbl) INTO n;"
        )

    def test_both_stores_keep_the_zero_guard_reachable(self) -> None:
        """`setval(seq, 0)` errors in Postgres, so an empty research table
        must still return 0 for the `IF n > 0` guard to skip it."""
        assert "coalesce(max(" in sync._predictions_branch(serving=False, floor=FLOOR)
        assert "coalesce(max(" in sync._predictions_branch(serving=True, floor=FLOOR)
