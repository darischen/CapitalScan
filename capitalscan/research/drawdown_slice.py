"""Drawdown-slice chart (Session 14, task 14.3).

ADR 015 calls this project's central claim "edge versus drawdown bucket,"
and this module renders exactly that: a static SVG, plus the CSV beside it
that the chart's numbers are checkable against (session doc §0).

**No recomputation.** Every number this module draws is read verbatim from
a stored `cell_stats` row (`edge`, `ci_low`, `ci_high`, `baseline_empirical`,
`n_eff`, `suppressed`, `suppress_reason`). This file calls no statistical
function from `core/stats.py` and no simulation of any kind — it only
queries, shapes, and formats (invariant 2's chart-side analogue: a chart
that recomputes what the table already stores is the one place a table and
its own picture can quietly disagree).

**The edge-space interval is a subtraction, not a new statistic.**
`cell_stats.ci_low`/`ci_high` are the Wilson interval on `p_hit`
(`research/cell_stats.py`), and `edge = p_hit - baseline_empirical`. So the
edge-space bound is `ci_low - baseline_empirical` / `ci_high -
baseline_empirical` — the same subtraction that produced `edge` itself,
applied to the two stored endpoints instead of the stored point estimate.
`build_slice_rows` performs exactly that arithmetic and nothing else; the
stored `ci_low`/`ci_high` are recoverable exactly by adding
`baseline_empirical` back (14.3 acceptance: "every rendered interval
matches its cell_stats ci_low/ci_high exactly").

**The two suppressed buckets have no `cell_stats` row to read, under any
config.** `research.cell_stats.select_grid_events` drops every `20-35` and
`35+` event before a single statistic is computed (ADR 101,
`core/cells.py::HEADLINE_BUCKET_COUNT = 2`) — this is structural, not a
per-cell suppression the way a thin `0-10`/`10-20` cell can be. There is
therefore nothing in `cell_stats`, under `697f3ae71428d392` or any other
config, to select for those two buckets. `ADR_101_SUPPRESSED` below is
ADR 101's own measured table, cited rather than recomputed — the same
"reuse the stored measurement" rule this module follows for the rendered
buckets, applied to the one bucket-pair whose stored measurement lives in
an ADR rather than a table row.
"""

from __future__ import annotations

import xml.sax.saxutils as xml_escape
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence, cast

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.cells import headline_grid
from capitalscan.core.config import StatsParams

# Matches `db/schema.sql`'s `v_screen`: the target and horizon the serving
# layer actually reads (`c.horizon_days = 5`, `c.target_pct = 0.03`). Reused
# here rather than re-chosen, since the chart's job is to show the same
# headline number the system already surfaces, not a different slice of the
# ladder DESIGN §6.12 also stores.
DEFAULT_TARGET_PCT = 0.03
DEFAULT_HORIZON_DAYS = 5

ADR_015_HYPOTHESIS = (
    "ADR 015 hypothesis: edge is positive and stable in the 0-10% and "
    "10-20% drawdown buckets, and turns negative past 35% drawdown -- "
    "because a deep mega-cap drawdown is usually a thesis break, not a "
    "routine dip. If it holds, the rule is: trade shallow dips, stand "
    "down on deep ones."
)

CELL_STATS_QUERY_COLUMNS = [
    "signal_type",
    "side",
    "dd_bucket",
    "n_events",
    "n_eff",
    "edge",
    "ci_low",
    "ci_high",
    "baseline_empirical",
    "suppressed",
    "suppress_reason",
]


@dataclass(frozen=True)
class SuppressedBucket:
    """One row of ADR 101's measured table (`docs/DECISIONS.md` ## 101).

    `best_n_eff_observed` and `cells_clearing_floor` are copied verbatim
    from the ADR's table, measured under `config_hash = 1835688bf7d760ba`
    on the train split, 59 cells across three eras, two splits, and both
    sides (2026-08-11). They are not re-derived from the current
    `config_hash` because there is nothing to derive them from: the bucket
    is excluded from event selection before `cell_stats` computes anything
    (see module docstring).
    """

    dd_bucket: str
    best_n_eff_observed: float
    cells_clearing_floor: int
    cells_measured_in_study: int


# ADR 101's table, both suppressed rows.
ADR_101_SUPPRESSED: tuple[SuppressedBucket, ...] = (
    SuppressedBucket("20-35", 13.6, 0, 59),
    SuppressedBucket("35+", 1.7, 0, 59),
)
ADR_101_SUPPRESS_REASON = (
    "permanently suppressed (ADR 101): 20-35 and 35+ cleared "
    "min_n_eff=30 in zero of the cells measured for the study"
)


@dataclass(frozen=True)
class SliceRow:
    """One headline-grid cell's drawdown-slice entry, in `headline_grid`'s
    stable order. `edge`/`edge_ci_low`/`edge_ci_high` are `None` on a
    suppressed cell (invariant 4: never a substituted zero)."""

    signal_type: str
    side: str
    dd_bucket: str
    rendered: bool
    n_events: int | None
    n_eff: float | None
    edge: float | None
    edge_ci_low: float | None
    edge_ci_high: float | None
    suppress_reason: str | None


def load_headline_cells(
    engine: Engine,
    config_hash: str,
    split_key: str,
    target_pct: float = DEFAULT_TARGET_PCT,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> pd.DataFrame:
    """The pooled-era headline-grid rows from stored `cell_stats`.

    `era IS NULL` is ADR 103's pooled shape -- the same row `v_screen`
    reads. This function issues one SELECT and nothing else; all shaping
    happens in `build_slice_rows`.
    """
    sql = text(
        """
        SELECT signal_type, side, dd_bucket, n_events, n_eff,
               edge, ci_low, ci_high, baseline_empirical,
               suppressed, suppress_reason
        FROM cell_stats
        WHERE config_hash = :config_hash
          AND split_key = :split_key
          AND arm = 'signal'
          AND era IS NULL
          AND target_pct = :target_pct
          AND horizon_days = :horizon_days
        """
    )
    with engine.connect() as conn:
        result = conn.execute(
            sql,
            {
                "config_hash": config_hash,
                "split_key": split_key,
                "target_pct": target_pct,
                "horizon_days": horizon_days,
            },
        )
        rows = [dict(r) for r in result.mappings().all()]
    return pd.DataFrame(rows, columns=CELL_STATS_QUERY_COLUMNS)


def build_slice_rows(
    cell_stats_df: pd.DataFrame, sp: StatsParams = StatsParams()
) -> tuple[SliceRow, ...]:
    """One `SliceRow` per `headline_grid(sp)` cell, in its stable order.

    Raises if `cell_stats_df` is missing a headline cell -- a silent gap
    here is exactly the failure task 14.3 exists to prevent (the deep
    buckets get shown as suppressed; a *headline* cell must never just be
    absent from the frame).
    """
    index = {(r.signal_type, r.side, r.dd_bucket): r for r in cell_stats_df.itertuples(index=False)}

    out: list[SliceRow] = []
    for spec in headline_grid(sp):
        key = (spec.signal_type, spec.side, spec.dd_bucket)
        if key not in index:
            raise ValueError(f"cell_stats is missing headline cell {key}")
        r = index[key]

        rendered = not bool(r.suppressed)
        edge: float | None = None
        edge_ci_low: float | None = None
        edge_ci_high: float | None = None
        if rendered:
            # `cast` rather than a bare `float()`: pandas' itertuples
            # attributes are typed as a wide scalar union, and mypy cannot
            # narrow them from the `rendered` guard above.
            baseline = float(cast("float", r.baseline_empirical))
            edge = float(cast("float", r.edge))
            edge_ci_low = float(cast("float", r.ci_low)) - baseline
            edge_ci_high = float(cast("float", r.ci_high)) - baseline

        out.append(
            SliceRow(
                signal_type=spec.signal_type,
                side=spec.side,
                dd_bucket=spec.dd_bucket,
                rendered=rendered,
                n_events=None if pd.isna(r.n_events) else int(cast("int", r.n_events)),
                n_eff=None if pd.isna(r.n_eff) else float(cast("float", r.n_eff)),
                edge=edge,
                edge_ci_low=edge_ci_low,
                edge_ci_high=edge_ci_high,
                suppress_reason=None if rendered else str(r.suppress_reason),
            )
        )
    return tuple(out)


def adr_015_verdict(rows: Sequence[SliceRow], sp: StatsParams = StatsParams()) -> str:
    """The measured answer to `ADR_015_HYPOTHESIS`, from `rows` alone.

    Counts intervals against zero (already in edge space -- no new
    statistic). The claim's second half, "turns negative past 35%," is
    reported as untestable under this grid rather than silently skipped:
    ADR 101 measured exactly that population and every such cell fell
    short of `min_n_eff`.
    """
    rendered = [r for r in rows if r.rendered]
    if not rendered:
        return "UNTESTABLE: no rendered 0-10/10-20 cell on this split."

    above = sum(1 for r in rendered if r.edge_ci_low is not None and r.edge_ci_low > 0)
    below = sum(1 for r in rendered if r.edge_ci_high is not None and r.edge_ci_high < 0)
    crosses = len(rendered) - above - below

    return (
        f"MEASURED, MIXED: of {len(rendered)} rendered 0-10/10-20 cells, "
        f"{above} have an edge interval entirely above zero, {below} entirely "
        f"below, {crosses} cross it. The shallow half of the hypothesis is not "
        f"uniformly confirmed. The deep half (20-35/35+ turning negative) is "
        f"UNTESTABLE under this grid: ADR 101 measured that population directly "
        f"and every such cell fell short of min_n_eff={sp.min_n_eff}."
    )


def drawdown_slice_svg_path(
    config_hash: str, split_key: str, output_dir: Path = Path("reports/phase4")
) -> Path:
    return output_dir / f"drawdown_slice_{config_hash}_{split_key}.svg"


def drawdown_slice_csv_path(
    config_hash: str, split_key: str, output_dir: Path = Path("reports/phase4")
) -> Path:
    return output_dir / f"drawdown_slice_{config_hash}_{split_key}.csv"


# --------------------------------------------------------------------------
# CSV: the numbers the SVG is checkable against
# --------------------------------------------------------------------------

CSV_COLUMNS = [
    "signal_type",
    "side",
    "dd_bucket",
    "rendered",
    "n_events",
    "n_eff",
    "edge",
    "edge_ci_low",
    "edge_ci_high",
    "suppress_reason",
]


def slice_csv_frame(
    rows: Sequence[SliceRow], deep: Sequence[SuppressedBucket] = ADR_101_SUPPRESSED
) -> pd.DataFrame:
    records: list[dict[str, object]] = [
        {
            "signal_type": r.signal_type,
            "side": r.side,
            "dd_bucket": r.dd_bucket,
            "rendered": r.rendered,
            "n_events": r.n_events,
            "n_eff": r.n_eff,
            "edge": r.edge,
            "edge_ci_low": r.edge_ci_low,
            "edge_ci_high": r.edge_ci_high,
            "suppress_reason": r.suppress_reason,
        }
        for r in rows
    ]
    for bucket in deep:
        records.append(
            {
                "signal_type": "ALL",
                "side": "ALL",
                "dd_bucket": bucket.dd_bucket,
                "rendered": False,
                "n_events": None,
                "n_eff": bucket.best_n_eff_observed,
                "edge": None,
                "edge_ci_low": None,
                "edge_ci_high": None,
                "suppress_reason": (
                    f"{ADR_101_SUPPRESS_REASON}; best n_eff observed "
                    f"{bucket.best_n_eff_observed:.1f}, "
                    f"{bucket.cells_clearing_floor}/{bucket.cells_measured_in_study} "
                    "cells cleared the floor"
                ),
            }
        )
    return pd.DataFrame(records, columns=CSV_COLUMNS)


def write_slice_csv(frame: pd.DataFrame, path: Path) -> Path:
    """Byte-identical across two runs except the provenance line (mirrors
    `research/curves.py::write_curve_csv`'s convention)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# generated_at={generated_at}\n")
        fh.write(
            "# edge_ci_low/edge_ci_high = stored cell_stats.ci_low/ci_high minus "
            "stored baseline_empirical (no new statistic; see module docstring)\n"
        )
        frame.to_csv(fh, index=False, columns=CSV_COLUMNS, lineterminator="\n")
    return path


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------

_WIDTH = 1040
_HEIGHT = 900
_MARGIN_L = 70
_MARGIN_R = 40
_PLOT_TOP = 210
_PLOT_BOTTOM = 640
_PLOT_LEFT = _MARGIN_L
_PLOT_RIGHT = _WIDTH - _MARGIN_R

_LONG_COLOR = "#1f6f43"  # bucket 0-10, long
_LONG_COLOR_DIM = "#5a9c78"  # bucket 10-20, long
_SHORT_COLOR = "#8a2e2e"  # bucket 0-10, short
_SHORT_COLOR_DIM = "#bf7070"  # bucket 10-20, short
_SUPPRESSED_COLOR = "#8a8a8a"
_ZERO_LINE_COLOR = "#333333"
_TEXT_COLOR = "#111111"
_GRID_COLOR = "#dddddd"
_MONO = "font-family:'DejaVu Sans Mono','Consolas',monospace"


def _fmt(x: float, digits: int = 4) -> str:
    return f"{x:.{digits}f}"


def _esc(s: str) -> str:
    return xml_escape.escape(s)


def _bucket_color(dd_bucket: str, side: str, dim: bool) -> str:
    if side == "long":
        return _LONG_COLOR_DIM if dim else _LONG_COLOR
    return _SHORT_COLOR_DIM if dim else _SHORT_COLOR


def render_svg(
    rows: Sequence[SliceRow],
    config_hash: str,
    split_key: str,
    deep: Sequence[SuppressedBucket] = ADR_101_SUPPRESSED,
    sp: StatsParams = StatsParams(),
) -> str:
    """The static SVG. Deterministic: fixed formatting, fixed row order (the
    order `rows` already carries from `build_slice_rows`), no timestamp, no
    randomness. Two calls on the same `rows` produce identical output."""

    signal_order: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r.side, r.signal_type)
        if key not in seen:
            seen.add(key)
            signal_order.append(key)

    n_families = len(signal_order)
    categories = n_families + 2  # + 20-35, 35+ (one suppressed slot each)
    col_width = (_PLOT_RIGHT - _PLOT_LEFT) / categories
    family_x = {key: _PLOT_LEFT + col_width * (i + 0.5) for i, key in enumerate(signal_order)}
    deep_x = {
        "20-35": _PLOT_LEFT + col_width * (n_families + 0.5),
        "35+": _PLOT_LEFT + col_width * (n_families + 1.5),
    }

    finite_bounds = [
        v
        for r in rows
        if r.rendered
        for v in (r.edge_ci_low, r.edge_ci_high, r.edge)
        if v is not None
    ]
    lo = min([0.0] + finite_bounds)
    hi = max([0.0] + finite_bounds)
    pad = max((hi - lo) * 0.15, 0.01)
    y_lo, y_hi = lo - pad, hi + pad

    def y(v: float) -> float:
        frac = (v - y_lo) / (y_hi - y_lo)
        return _PLOT_BOTTOM - frac * (_PLOT_BOTTOM - _PLOT_TOP)

    zero_y = y(0.0)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}" '
        f'width="{_WIDTH}" height="{_HEIGHT}">'
    )
    parts.append(f'<rect x="0" y="0" width="{_WIDTH}" height="{_HEIGHT}" fill="#ffffff"/>')

    # Title + metadata
    parts.append(
        f'<text x="{_MARGIN_L}" y="32" font-size="20" font-weight="bold" '
        f'fill="{_TEXT_COLOR}" style="{_MONO}">'
        "Drawdown slice -- edge vs. drawdown bucket (ADR 015)</text>"
    )
    parts.append(
        f'<text x="{_MARGIN_L}" y="54" font-size="13" fill="{_TEXT_COLOR}" style="{_MONO}">'
        f"config_hash={_esc(config_hash)}  split={_esc(split_key)}  "
        f"target_pct={_fmt(DEFAULT_TARGET_PCT, 2)}  horizon_days={DEFAULT_HORIZON_DAYS}</text>"
    )

    # Hypothesis + verdict, wrapped
    hyp_lines = _wrap(ADR_015_HYPOTHESIS, 108)
    yy = 80
    for line in hyp_lines:
        parts.append(
            f'<text x="{_MARGIN_L}" y="{yy}" font-size="12" '
            f'fill="{_TEXT_COLOR}" style="{_MONO}">{_esc(line)}</text>'
        )
        yy += 15

    verdict = adr_015_verdict(rows, sp)
    verdict_lines = _wrap(f"Measured answer: {verdict}", 108)
    yy += 4
    for line in verdict_lines:
        parts.append(
            f'<text x="{_MARGIN_L}" y="{yy}" font-size="12" font-weight="bold" '
            f'fill="{_TEXT_COLOR}" style="{_MONO}">{_esc(line)}</text>'
        )
        yy += 15

    # Plot frame + gridlines
    parts.append(
        f'<rect x="{_PLOT_LEFT}" y="{_PLOT_TOP}" width="{_PLOT_RIGHT - _PLOT_LEFT}" '
        f'height="{_PLOT_BOTTOM - _PLOT_TOP}" fill="none" stroke="{_GRID_COLOR}"/>'
    )
    # Zero line -- the whole question is whether the interval crosses it.
    parts.append(
        f'<line x1="{_PLOT_LEFT}" y1="{zero_y:.2f}" x2="{_PLOT_RIGHT}" y2="{zero_y:.2f}" '
        f'stroke="{_ZERO_LINE_COLOR}" stroke-width="1.5"/>'
    )
    parts.append(
        f'<text x="{_PLOT_RIGHT + 4}" y="{zero_y + 4:.2f}" font-size="11" '
        f'fill="{_ZERO_LINE_COLOR}" style="{_MONO}">0</text>'
    )
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = y_lo + frac * (y_hi - y_lo)
        yv = y(v)
        parts.append(
            f'<line x1="{_PLOT_LEFT}" y1="{yv:.2f}" x2="{_PLOT_RIGHT}" y2="{yv:.2f}" '
            f'stroke="{_GRID_COLOR}" stroke-dasharray="2,2"/>'
        )
        parts.append(
            f'<text x="{_PLOT_LEFT - 6}" y="{yv + 4:.2f}" font-size="10" text-anchor="end" '
            f'fill="{_TEXT_COLOR}" style="{_MONO}">{_fmt(v, 3)}</text>'
        )

    # Rendered cells: 0-10 and 10-20, grouped by signal family
    dot_r = 4.0
    offset = col_width * 0.16
    for key in signal_order:
        side, signal_type = key
        cx = family_x[key]
        cell_rows = [r for r in rows if (r.side, r.signal_type) == key]
        for i, bucket in enumerate(("0-10", "10-20")):
            row = next((rr for rr in cell_rows if rr.dd_bucket == bucket), None)
            if row is None:
                continue
            x = cx + (offset if i == 1 else -offset)
            dim = i == 1
            color = _bucket_color(bucket, side, dim)
            if (
                row.rendered
                and row.edge is not None
                and row.edge_ci_low is not None
                and row.edge_ci_high is not None
            ):
                ylo = y(row.edge_ci_low)
                yhi = y(row.edge_ci_high)
                yv = y(row.edge)
                parts.append(
                    f'<line x1="{x:.2f}" y1="{ylo:.2f}" x2="{x:.2f}" y2="{yhi:.2f}" '
                    f'stroke="{color}" stroke-width="2"/>'
                )
                parts.append(f'<circle cx="{x:.2f}" cy="{yv:.2f}" r="{dot_r}" fill="{color}"/>')
            else:
                yv = zero_y
                parts.append(
                    f'<text x="{x:.2f}" y="{yv - 8:.2f}" font-size="14" text-anchor="middle" '
                    f'fill="{_SUPPRESSED_COLOR}" style="{_MONO}">&#215;</text>'
                )
                neff = "n/a" if row.n_eff is None else f"{row.n_eff:.1f}"
                parts.append(
                    f'<text x="{x:.2f}" y="{yv + 18:.2f}" font-size="8" text-anchor="middle" '
                    f'fill="{_SUPPRESSED_COLOR}" style="{_MONO}" '
                    f'transform="rotate(90 {x:.2f} {yv + 18:.2f})">'
                    f"suppressed n_eff={neff}</text>"
                )
        label_lines = _wrap(f"{signal_type}", 14)
        ly = _PLOT_BOTTOM + 16
        for line in label_lines:
            parts.append(
                f'<text x="{cx:.2f}" y="{ly}" font-size="9" text-anchor="middle" '
                f'fill="{_TEXT_COLOR}" style="{_MONO}">{_esc(line)}</text>'
            )
            ly += 11
        parts.append(
            f'<text x="{cx:.2f}" y="{ly}" font-size="9" text-anchor="middle" '
            f'fill="{_TEXT_COLOR}" style="{_MONO}">({_esc(side)})</text>'
        )

    # Structurally-suppressed deep buckets -- shown, never omitted
    deep_by_bucket = {d.dd_bucket: d for d in deep}
    for bucket_label, cx in deep_x.items():
        d = deep_by_bucket.get(bucket_label)
        box_h = _PLOT_BOTTOM - _PLOT_TOP
        parts.append(
            f'<rect x="{cx - col_width * 0.4:.2f}" y="{_PLOT_TOP}" width="{col_width * 0.8:.2f}" '
            f'height="{box_h}" fill="#f2f2f2" stroke="{_GRID_COLOR}" stroke-dasharray="4,3"/>'
        )
        mid_y = (_PLOT_TOP + _PLOT_BOTTOM) / 2
        parts.append(
            f'<text x="{cx:.2f}" y="{mid_y - 10:.2f}" font-size="12" font-weight="bold" '
            f'text-anchor="middle" fill="{_SUPPRESSED_COLOR}" style="{_MONO}">SUPPRESSED</text>'
        )
        parts.append(
            f'<text x="{cx:.2f}" y="{mid_y + 8:.2f}" font-size="10" text-anchor="middle" '
            f'fill="{_SUPPRESSED_COLOR}" style="{_MONO}">(ADR 101)</text>'
        )
        if d is not None:
            parts.append(
                f'<text x="{cx:.2f}" y="{mid_y + 26:.2f}" font-size="9" text-anchor="middle" '
                f'fill="{_SUPPRESSED_COLOR}" style="{_MONO}">'
                f"best n_eff={_fmt(d.best_n_eff_observed, 1)}</text>"
            )
            parts.append(
                f'<text x="{cx:.2f}" y="{mid_y + 40:.2f}" font-size="9" text-anchor="middle" '
                f'fill="{_SUPPRESSED_COLOR}" style="{_MONO}">'
                f"{d.cells_clearing_floor}/{d.cells_measured_in_study} cells cleared floor</text>"
            )
        parts.append(
            f'<text x="{cx:.2f}" y="{_PLOT_BOTTOM + 16}" font-size="9" text-anchor="middle" '
            f'fill="{_TEXT_COLOR}" style="{_MONO}">{_esc(bucket_label)}</text>'
        )

    # Legend
    ly = _PLOT_TOP - 12
    legend_x = _PLOT_LEFT
    for label, color in (
        ("long 0-10", _LONG_COLOR),
        ("long 10-20", _LONG_COLOR_DIM),
        ("short 0-10", _SHORT_COLOR),
        ("short 10-20", _SHORT_COLOR_DIM),
    ):
        parts.append(f'<circle cx="{legend_x + 5}" cy="{ly - 4}" r="4" fill="{color}"/>')
        parts.append(
            f'<text x="{legend_x + 14}" y="{ly}" font-size="10" fill="{_TEXT_COLOR}" '
            f'style="{_MONO}">{_esc(label)}</text>'
        )
        legend_x += 110

    parts.append(
        f'<text x="{_MARGIN_L}" y="{_HEIGHT - 12}" font-size="9" fill="#666666" style="{_MONO}">'
        "edge_ci_low/edge_ci_high = stored cell_stats.ci_low/ci_high "
        "minus stored baseline_empirical"
        "</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _wrap(text_value: str, width: int) -> list[str]:
    words = text_value.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = f"{cur} {w}".strip()
        if len(candidate) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines


def write_drawdown_slice_svg(svg: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8", newline="\n")
    return path


def export_drawdown_slice(
    engine: Engine,
    config_hash: str,
    split_key: str,
    output_dir: Path = Path("reports/phase4"),
    sp: StatsParams = StatsParams(),
) -> tuple[Path, Path]:
    """Load, shape, and write both artifacts for one (config, split). The
    one entry point a future CLI hook (14.6) calls."""
    cell_stats_df = load_headline_cells(engine, config_hash, split_key)
    rows = build_slice_rows(cell_stats_df, sp)

    svg = render_svg(rows, config_hash, split_key, sp=sp)
    svg_path = write_drawdown_slice_svg(
        svg, drawdown_slice_svg_path(config_hash, split_key, output_dir)
    )

    frame = slice_csv_frame(rows)
    csv_path = write_slice_csv(frame, drawdown_slice_csv_path(config_hash, split_key, output_dir))

    return svg_path, csv_path


if __name__ == "__main__":
    import sys

    from capitalscan.jobs import db_io

    _config_hash = sys.argv[1] if len(sys.argv) > 1 else "697f3ae71428d392"
    _split_keys = sys.argv[2:] if len(sys.argv) > 2 else ["train", "validate"]

    _engine = db_io.get_engine()
    for _split in _split_keys:
        _svg_path, _csv_path = export_drawdown_slice(_engine, _config_hash, _split)
        print(f"{_split}: {_svg_path}")
        print(f"{_split}: {_csv_path}")
