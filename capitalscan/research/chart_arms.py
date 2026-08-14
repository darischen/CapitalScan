"""The three-arm chart (Session 14, task 14.2).

Static SVG, generated with stdlib string formatting only. No plotting
dependency: `uv add` locks `.venv` files against any running job (CLAUDE.md),
this project always has one running, and a repo with no plotting dependency
today would be taking on a real supply-chain decision for what is, in the
end, three line series and a shaded band.

This module only **shapes** numbers computed and stored elsewhere. It never
simulates an arm and never recomputes a statistic:

- Equity-curve points come from the tidy `(date, arm, value)` frame
  `research.curves.curve_frame` produces, or the CSV it writes beside this
  chart (`equity_curves_<config_hash>_<split>.csv`). `research/curves.py`'s
  header states the row convention this module relies on: row `i` is equity
  at the **end** of trading day `i`, and the 1.0 base sits before row 1. So
  a curve's last row, minus 1.0, is that arm's `total_ret` — never
  `last / first - 1`, which only agrees for `buy_hold` (14.1's documented
  trap: `buy_hold`'s first row genuinely is 1.0 and both formulas agree
  there; `signal` can earn a day-0 return, so they diverge, and checking
  `buy_hold` alone would hide it).
- Summary-table numbers (total return, annualized, Sharpe, max drawdown,
  deployment fraction, capital efficiency) are read directly off the stored
  `benchmarks` rows for `buy_hold` and `signal`
  (`era='pooled', replication IS NULL`) — never recomputed from the curve,
  so the chart cannot disagree with the table beside it (session doc §6,
  "letting the chart recompute what `benchmarks` already stores").
- The verdict ("cleared the null's 97.5th percentile" or not) compares the
  signal arm's last equity value against `null_p97_5`'s last equity value,
  both already in the curve frame. `equity[-1] = 1 + total_ret` for every
  arm, so comparing endpoints is the same ordering `core.arms.exceeds_null`
  gets from comparing `total_ret` values directly against the stored null
  distribution — read off the curve instead of a second query.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as _esc

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.arms import ARM_BUY_HOLD, ARM_SIGNAL
from capitalscan.research.curves import (
    ARM_NULL_P2_5,
    ARM_NULL_P50,
    ARM_NULL_P97_5,
    CURVE_COLUMNS,
    curve_csv_path,
)

# The two arms the summary table reports. `null` has no single `benchmarks`
# row (it is a 200-replication distribution), so it is not in this tuple —
# its role on the chart is the shaded band and the `null_p50` line, both
# already in the curve frame.
SUMMARY_ARMS = (ARM_BUY_HOLD, ARM_SIGNAL)

# Layout constants. No magic numbers outside this module because this is
# rendering geometry, not a statistical threshold (invariant 9 is about
# thresholds, not pixel positions), but they are named rather than inlined
# so the layout is legible in one place.
_WIDTH = 900
_CHART_LEFT = 70.0
_CHART_RIGHT = 870.0
_CHART_TOP = 40.0
_CHART_BOTTOM = 420.0
_LEGEND_Y = 445.0
_VERDICT_Y = 470.0
_TABLE_TOP = 500.0
_TABLE_ROW_H = 20.0
_HEIGHT = 620

_ARM_COLORS = {
    ARM_BUY_HOLD: "#7a8699",
    ARM_SIGNAL: "#2f8f4e",
    ARM_NULL_P50: "#c9782f",
}
_BAND_COLOR = "#c9782f"

_ARM_LABELS = {
    ARM_BUY_HOLD: "buy and hold",
    ARM_SIGNAL: "signal",
    ARM_NULL_P50: "null median (200 reps)",
}


@dataclass(frozen=True)
class ArmSummary:
    """One `benchmarks` row's chart-table columns, read verbatim, never recomputed."""

    arm: str
    total_ret: float
    annualized_ret: float
    sharpe: float | None
    max_drawdown: float
    frac_deployed: float
    capital_efficiency: float


# ==========================================================================
# Reads: the curve CSV and the stored benchmarks rows
# ==========================================================================


def load_curve_frame(path: Path) -> pd.DataFrame:
    """Read a curves CSV as written by `research.curves.write_curve_csv`.

    `comment="#"` skips the provenance header lines
    (`# generated_at=...`, the two convention lines); everything else is the
    same tidy `(date, arm, value)` frame `curve_frame` builds in memory, so
    a chart built from the CSV and one built from the in-memory frame in the
    same process are the same input to `render_three_arm_svg`.
    """
    frame = pd.read_csv(path, comment="#")
    if list(frame.columns) != CURVE_COLUMNS:
        raise ValueError(f"{path}: expected columns {CURVE_COLUMNS}, got {list(frame.columns)}")
    return frame


def load_arm_summaries(engine: Engine, config_hash: str, split_key: str) -> dict[str, ArmSummary]:
    """`buy_hold` and `signal`'s pooled summary rows, straight off `benchmarks`.

    `era = 'pooled'` and `replication IS NULL` is the single arm-level row
    per (config, split, arm) — the same filter `BenchmarkReport.arm_rows`
    uses. Raises if either row is missing rather than rendering a chart with
    a silently blank table cell.
    """
    sql = text(
        "SELECT arm, total_ret, annualized_ret, sharpe, max_drawdown, "
        "frac_deployed, capital_efficiency FROM benchmarks "
        "WHERE config_hash = :config_hash AND split_key = :split_key "
        "AND era = 'pooled' AND replication IS NULL "
        "AND arm IN ('buy_hold', 'signal') ORDER BY arm"
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(sql, {"config_hash": config_hash, "split_key": split_key}).mappings().all()
        )

    out: dict[str, ArmSummary] = {}
    for row in rows:
        out[str(row["arm"])] = ArmSummary(
            arm=str(row["arm"]),
            total_ret=float(row["total_ret"]),
            annualized_ret=float(row["annualized_ret"]),
            sharpe=None if row["sharpe"] is None else float(row["sharpe"]),
            max_drawdown=float(row["max_drawdown"]),
            frac_deployed=float(row["frac_deployed"]),
            capital_efficiency=float(row["capital_efficiency"]),
        )
    missing = set(SUMMARY_ARMS) - out.keys()
    if missing:
        raise ValueError(
            f"no pooled benchmarks row for {sorted(missing)} "
            f"(config_hash={config_hash!r}, split_key={split_key!r})"
        )
    return out


# ==========================================================================
# Scales
# ==========================================================================


def _series(frame: pd.DataFrame, arm: str) -> tuple[list[str], list[float]]:
    rows = frame.loc[frame["arm"] == arm].sort_values("date")
    return list(rows["date"]), [float(v) for v in rows["value"]]


def _log_scale(
    values: Sequence[float], top: float, bottom: float
) -> tuple[float, float, Callable[[float], float]]:
    """Log-scale mapping from an equity value to a pixel y-coordinate.

    Guards the acceptance-3 case directly: if every value shares one order
    of magnitude (the degenerate case is a perfectly flat arm), `log_hi -
    log_lo` is zero and dividing by it would raise `ZeroDivisionError`. A
    fixed half-decade of padding on each side keeps the mapping well-defined
    and draws a flat line as a flat line rather than crashing.
    """
    positive = [v for v in values if v > 0]
    if not positive:
        raise ValueError("no positive equity values to plot on a log scale")
    log_lo = math.log10(min(positive))
    log_hi = math.log10(max(positive))
    span = log_hi - log_lo
    if span <= 0:
        log_lo -= 0.5
        log_hi += 0.5
        span = log_hi - log_lo

    def y(value: float) -> float:
        v = value if value > 0 else min(positive) * 1e-6
        return bottom - (math.log10(v) - log_lo) / span * (bottom - top)

    return log_lo, log_hi, y


def _x_positions(n: int, left: float, right: float) -> list[float]:
    if n <= 1:
        return [(left + right) / 2.0] * max(n, 0)
    step = (right - left) / (n - 1)
    return [left + step * i for i in range(n)]


# ==========================================================================
# SVG fragments
# ==========================================================================


def _polyline(xs: Sequence[float], ys: Sequence[float], color: str) -> str:
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys, strict=True))
    return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>'


def _band_path(xs: Sequence[float], low_ys: Sequence[float], high_ys: Sequence[float]) -> str:
    """Filled path from `null_p2_5` to `null_p97_5`, the one band `<path>`.

    Forward along the low edge, then backward along the high edge, closed —
    the standard way to fill the region between two curves with a single
    path element (acceptance: "exactly ... one band `<path>`").
    """
    forward = list(zip(xs, low_ys, strict=True))
    backward = list(zip(reversed(xs), reversed(high_ys), strict=True))
    points = forward + backward
    d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z"
    return f'<path d="{d}" fill="{_BAND_COLOR}" fill-opacity="0.15" stroke="none"/>'


def _pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value * 100:.2f}%"


def _num(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.3f}"


def _y_ticks(log_lo: float, log_hi: float, n: int = 5) -> list[float]:
    if n <= 1:
        return [10**log_lo]
    step = (log_hi - log_lo) / (n - 1)
    return [10 ** (log_lo + step * i) for i in range(n)]


# ==========================================================================
# The renderer
# ==========================================================================


def render_three_arm_svg(
    curve_frame: pd.DataFrame,
    summaries: Mapping[str, ArmSummary],
    config_hash: str,
    split_key: str,
) -> str:
    """Build the SVG text. Pure: same inputs, same bytes, every call.

    No timestamp anywhere in the output (unlike the CSV's provenance
    comment) — that is what makes 14.2's "two runs, identical bytes"
    acceptance unconditional rather than needing a stripped-header
    comparison the way the CSV's does.
    """
    missing_summary = set(SUMMARY_ARMS) - set(summaries.keys())
    if missing_summary:
        raise ValueError(
            f"no pooled benchmarks row for {sorted(missing_summary)} "
            f"(config_hash={config_hash!r}, split_key={split_key!r})"
        )

    dates, buy_hold_y = _series(curve_frame, ARM_BUY_HOLD)
    _, signal_y = _series(curve_frame, ARM_SIGNAL)
    _, null_p50_y = _series(curve_frame, ARM_NULL_P50)
    _, null_lo_y = _series(curve_frame, ARM_NULL_P2_5)
    _, null_hi_y = _series(curve_frame, ARM_NULL_P97_5)

    n = len(dates)
    if not (
        len(buy_hold_y) == len(signal_y) == len(null_p50_y) == len(null_lo_y) == len(null_hi_y) == n
    ):
        raise ValueError("arm series in curve_frame have mismatched lengths")

    all_values = buy_hold_y + signal_y + null_p50_y + null_lo_y + null_hi_y
    log_lo, log_hi, y = _log_scale(all_values, _CHART_TOP, _CHART_BOTTOM)
    xs = _x_positions(n, _CHART_LEFT, _CHART_RIGHT)

    # --- verdict: signal's endpoint against null_p97_5's endpoint --------
    signal_end = signal_y[-1] if signal_y else float("nan")
    null_p97_5_end = null_hi_y[-1] if null_hi_y else float("nan")
    cleared = signal_end > null_p97_5_end
    verdict_text = _esc(
        f"Verdict: signal ({_pct(signal_end - 1.0)} total return) "
        f"{'CLEARED' if cleared else 'did NOT clear'} "
        f"the null's 97.5th percentile ({_pct(null_p97_5_end - 1.0)}) on {split_key}."
    )

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}" '
        f'width="{_WIDTH}" height="{_HEIGHT}" font-family="monospace" font-size="12">'
    )
    parts.append(f'<rect x="0" y="0" width="{_WIDTH}" height="{_HEIGHT}" fill="#ffffff"/>')
    parts.append(
        f'<text x="{_CHART_LEFT}" y="20" font-size="15" font-weight="bold">'
        f"Three-arm equity curve — config {_esc(config_hash)}, split {_esc(split_key)}</text>"
    )

    # --- y-axis: log-scale ticks and gridlines ----------------------------
    for tick in _y_ticks(log_lo, log_hi):
        ty = y(tick)
        parts.append(
            f'<line x1="{_CHART_LEFT}" y1="{ty:.2f}" x2="{_CHART_RIGHT}" y2="{ty:.2f}" '
            f'stroke="#e2e2e2" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{_CHART_LEFT - 8:.2f}" y="{ty + 4:.2f}" text-anchor="end">{tick:.2f}x</text>'
        )
    parts.append(
        f'<text x="20" y="{(_CHART_TOP + _CHART_BOTTOM) / 2:.2f}" '
        f'transform="rotate(-90 20 {(_CHART_TOP + _CHART_BOTTOM) / 2:.2f})" '
        f'text-anchor="middle">Equity multiple (log scale)</text>'
    )

    # --- x-axis: a handful of date ticks -----------------------------------
    n_xticks = min(6, n) if n else 0
    if n_xticks:
        tick_idx = (
            sorted({round(i * (n - 1) / (n_xticks - 1)) for i in range(n_xticks)})
            if n_xticks > 1
            else [0]
        )
        for i in tick_idx:
            parts.append(
                f'<text x="{xs[i]:.2f}" y="{_CHART_BOTTOM + 16:.2f}" text-anchor="middle">'
                f"{dates[i]}</text>"
            )
    parts.append(
        f'<text x="{(_CHART_LEFT + _CHART_RIGHT) / 2:.2f}" y="{_CHART_BOTTOM + 34:.2f}" '
        f'text-anchor="middle">Trading day</text>'
    )
    parts.append(
        f'<rect x="{_CHART_LEFT}" y="{_CHART_TOP}" width="{_CHART_RIGHT - _CHART_LEFT}" '
        f'height="{_CHART_BOTTOM - _CHART_TOP}" fill="none" stroke="#999999"/>'
    )

    # --- the band, then the three lines (band first so lines draw on top) -
    parts.append(_band_path(xs, [y(v) for v in null_lo_y], [y(v) for v in null_hi_y]))
    parts.append(_polyline(xs, [y(v) for v in buy_hold_y], _ARM_COLORS[ARM_BUY_HOLD]))
    parts.append(_polyline(xs, [y(v) for v in null_p50_y], _ARM_COLORS[ARM_NULL_P50]))
    parts.append(_polyline(xs, [y(v) for v in signal_y], _ARM_COLORS[ARM_SIGNAL]))

    # --- legend -------------------------------------------------------------
    legend_x = _CHART_LEFT
    for arm in (ARM_BUY_HOLD, ARM_SIGNAL, ARM_NULL_P50):
        parts.append(
            f'<line x1="{legend_x:.2f}" y1="{_LEGEND_Y:.2f}" x2="{legend_x + 20:.2f}" '
            f'y2="{_LEGEND_Y:.2f}" stroke="{_ARM_COLORS[arm]}" stroke-width="3"/>'
        )
        parts.append(
            f'<text x="{legend_x + 26:.2f}" y="{_LEGEND_Y + 4:.2f}">{_ARM_LABELS[arm]}</text>'
        )
        legend_x += 26 + 9 * (len(_ARM_LABELS[arm]) + 3)
    parts.append(
        f'<rect x="{legend_x:.2f}" y="{_LEGEND_Y - 8:.2f}" width="20" height="10" '
        f'fill="{_BAND_COLOR}" fill-opacity="0.15"/>'
    )
    parts.append(
        f'<text x="{legend_x + 26:.2f}" y="{_LEGEND_Y + 4:.2f}">null 2.5th-97.5th pct band</text>'
    )

    # --- verdict, in words ---------------------------------------------------
    parts.append(
        f'<text x="{_CHART_LEFT}" y="{_VERDICT_Y}" font-weight="bold">{verdict_text}</text>'
    )

    # --- summary table, read verbatim off `benchmarks`, never recomputed ---
    columns = [
        ("arm", 90),
        ("total_ret", 110),
        ("annualized", 110),
        ("sharpe", 90),
        ("max_dd", 90),
        ("deploy_frac", 110),
        ("cap_eff", 90),
    ]
    header_y = _TABLE_TOP
    x = _CHART_LEFT
    for label, width in columns:
        parts.append(f'<text x="{x:.2f}" y="{header_y:.2f}" font-weight="bold">{label}</text>')
        x += width
    for row_i, arm in enumerate(SUMMARY_ARMS, start=1):
        row = summaries[arm]
        row_y = header_y + _TABLE_ROW_H * row_i
        values = [
            arm,
            _pct(row.total_ret),
            _pct(row.annualized_ret),
            _num(row.sharpe),
            _pct(row.max_drawdown),
            _pct(row.frac_deployed),
            _num(row.capital_efficiency),
        ]
        x = _CHART_LEFT
        for value, (_, width) in zip(values, columns, strict=True):
            parts.append(f'<text x="{x:.2f}" y="{row_y:.2f}">{value}</text>')
            x += width

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# ==========================================================================
# The entry point: read, render, write
# ==========================================================================


def chart_svg_path(
    config_hash: str, split_key: str, output_dir: Path = Path("reports/phase4")
) -> Path:
    """`reports/phase4/three_arms_<config_hash>_<split>.svg` (14.2 output rule)."""
    return output_dir / f"three_arms_{config_hash}_{split_key}.svg"


def export_three_arm_chart(
    engine: Engine,
    config_hash: str,
    split_key: str,
    curve_frame: pd.DataFrame | None = None,
    output_dir: Path = Path("reports/phase4"),
) -> Path:
    """Read the curve CSV (unless handed one) and the `benchmarks` rows, render, write.

    `curve_frame` lets a caller that already holds the frame in memory (e.g.
    right after `research.curves.export_curves`) skip the CSV round-trip;
    the default reads the CSV `curves.export_curves` already wrote, since
    14.2 depends on 14.1's output rather than on being called in the same
    process.
    """
    if curve_frame is None:
        curve_frame = load_curve_frame(curve_csv_path(config_hash, split_key, output_dir))
    summaries = load_arm_summaries(engine, config_hash, split_key)
    svg = render_three_arm_svg(curve_frame, summaries, config_hash, split_key)
    path = chart_svg_path(config_hash, split_key, output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg)
    return path
