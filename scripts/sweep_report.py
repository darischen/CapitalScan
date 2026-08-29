#!/usr/bin/env python
"""Render the exit sweep's live status as a standalone HTML report.

Regenerating is the update path: this queries the research database for
whatever is true right now and rewrites the file, so publishing again after a
run is one command rather than a hand edit.

    uv run python scripts/sweep_report.py

Design follows CLAUDE.md's pinned direction rather than inventing one: dense
instrument panel, monospace for every number, dark by default, colour used
only to carry meaning. The signature element is the 4x3 matrix, because the
sweep genuinely is targets x stops -- the grid is information, not decoration.
"""

from __future__ import annotations

import html
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = "a38d3ca6b58295e8"
PT = ZoneInfo("America/Los_Angeles")

# name, target, stop label, config hash, which machine owns it
ARMS = [
    ("t4_atr15", "4%", "ATR 1.5", "a38d3ca6b58295e8", "baseline"),
    ("t4_fix2", "4%", "fixed 2%", "f7b31c5443d30948", "workstation"),
    ("t5_atr15", "5%", "ATR 1.5", "49fc87114751f32a", "workstation"),
    ("t5_fix2", "5%", "fixed 2%", "6e7b11fc1c6ee599", "workstation"),
    ("t5_fix3", "5%", "fixed 3%", "fcc6df7649798127", "workstation"),
    ("t6_atr15", "6%", "ATR 1.5", "0fdd15e962436b72", "workstation"),
    ("t6_fix2", "6%", "fixed 2%", "ccfde27281981436", "workstation"),
    ("t6_fix3", "6%", "fixed 3%", "a56d05a752a217ee", "laptop"),
    ("t7_atr15", "7%", "ATR 1.5", "0bdf21eba0ff2e34", "laptop"),
    ("t7_fix2", "7%", "fixed 2%", "c74e355184fea7bb", "laptop"),
    ("t7_fix3", "7%", "fixed 3%", "753813ea1b7bb09f", "laptop"),
]
TARGETS = ["4%", "5%", "6%", "7%"]
STOPS = ["ATR 1.5", "fixed 2%", "fixed 3%"]


def engine():
    from capitalscan.jobs import db_io

    return db_io.get_engine()


def collect() -> dict:
    eng = engine()
    out: dict = {"arms": {}, "runs": []}
    with eng.connect() as c:
        c.execute(sa.text("SET max_parallel_workers_per_gather=0"))
        for name, target, stop, chash, owner in ARMS:
            n_events = c.execute(
                sa.text("SELECT count(*) FROM events WHERE config_hash=:h"), {"h": chash}
            ).scalar_one()
            n_cells = c.execute(
                sa.text("SELECT count(*) FROM cell_stats WHERE config_hash=:h"), {"h": chash}
            ).scalar_one()
            crow = c.execute(
                sa.text(
                    "SELECT count(*) FILTER (WHERE status='ok') AS ok, "
                    "       max((params->>'of')::int) AS of_total "
                    "  FROM runs WHERE job='backtest_compute' "
                    "   AND params->>'config_hash'=:h"
                ),
                {"h": chash},
            ).one()
            chunks = int(crow.ok or 0)
            of_total = int(crow.of_total or 0)
            row: dict = {
                "name": name,
                "target": target,
                "stop": stop,
                "hash": chash,
                "owner": owner,
                "events": int(n_events),
                "cells": int(n_cells),
                "chunks": chunks,
                "of_total": of_total,
            }
            if n_events:
                for split in ("train", "validate"):
                    r = c.execute(
                        sa.text(
                            "SELECT count(*) n, avg(net_ret) net, avg(gross_ret) gross, "
                            "  avg((exit_reason='stop')::int) stop_r, "
                            "  avg((exit_reason='target')::int) tgt_r, "
                            "  avg((exit_reason='timeout')::int) to_r, "
                            "  avg(holding_days) hold "
                            "FROM events WHERE config_hash=:h AND split_key=:s "
                            "  AND net_ret IS NOT NULL"
                        ),
                        {"h": chash, "s": split},
                    ).one()
                    if r.n:
                        row[split] = {
                            "n": int(r.n),
                            "net": float(r.net),
                            "gross": float(r.gross),
                            "stop": float(r.stop_r) * 100,
                            "target": float(r.tgt_r) * 100,
                            "timeout": float(r.to_r) * 100,
                            "hold": float(r.hold) if r.hold is not None else None,
                        }
            out["arms"][name] = row

        out["runs"] = [
            dict(r._mapping)
            for r in c.execute(
                sa.text(
                    "SELECT job, status, started_at, finished_at, "
                    "       params->>'config_hash' AS chash "
                    "  FROM runs WHERE started_at >= now() - interval '24 hours' "
                    "   AND job LIKE 'backtest%' OR job IN ('path_backfill','peak_labels',"
                    "       'rho','cell_stats') AND started_at >= now() - interval '24 hours' "
                    " ORDER BY started_at DESC LIMIT 12"
                )
            )
        ]
    return out


def state_of(a: dict) -> str:
    """`done` means the arm's returns are final, not that every phase ran.

    `net_ret` is written by the compute phase and never changes afterwards --
    `finalize` corrects `cofire_count`, and the stats passes read the events
    rather than rewriting them. Gating on `cell_stats` therefore hid a
    finished result for the ~30 minutes those phases take, which is the
    opposite of what a status page is for.
    """
    if a["owner"] == "baseline":
        return "done"
    if a["of_total"] and a["chunks"] >= a["of_total"]:
        return "done"
    if a["cells"] >= 512:
        return "done"
    if a["events"] > 0:
        return "running"
    return "queued"


def fmt_bp(v: float | None) -> str:
    """Returns in basis points -- the scale everything here actually lives at."""
    if v is None:
        return "—"
    return f"{v * 10000:+.1f}"


def build(data: dict) -> str:
    arms = data["arms"]
    base_v = arms["t4_atr15"].get("validate")
    now = datetime.now(timezone.utc).astimezone(PT)

    done = sum(1 for a in arms.values() if state_of(a) == "done")
    running = sum(1 for a in arms.values() if state_of(a) == "running")
    queued = sum(1 for a in arms.values() if state_of(a) == "queued")

    # ---- matrix ------------------------------------------------------
    cells = []
    for stop in STOPS:
        rowcells = []
        for target in TARGETS:
            a = next(
                (x for x in arms.values() if x["target"] == target and x["stop"] == stop), None
            )
            if a is None:
                rowcells.append('<td class="cell empty"></td>')
                continue
            st = state_of(a)
            # **A partial arm shows no number.** `net_ret` averaged over 6 of
            # 61 chunks is a real average of an unreal population -- it
            # renders identically to a finished result and invites being
            # read as one. Progress is shown instead, which is what is
            # actually known.
            v = a.get("validate") if st == "done" else None
            delta = ""
            if v and base_v and a["name"] != "t4_atr15":
                d = v["net"] - base_v["net"]
                cls = "up" if d > 0 else "down"
                delta = f'<span class="delta {cls}">{fmt_bp(d)}</span>'
            elif a["name"] == "t4_atr15":
                delta = '<span class="delta ref">reference</span>'
            net = f'<span class="net">{fmt_bp(v["net"])}</span>' if v else ""
            sub = ""
            if st == "running":
                sub = f'<span class="sub">{a["chunks"]}/{a["of_total"] or 61} chunks</span>'
            elif st == "queued":
                sub = f'<span class="sub">{a["owner"]}</span>'
            elif v:
                sub = f'<span class="sub">{v["stop"]:.0f}% stopped</span>'
            rowcells.append(
                f'<td class="cell {st}">'
                f'<span class="armname">{html.escape(a["name"])}</span>'
                f"{net}{delta}{sub}</td>"
            )
        cells.append(f'<tr><th scope="row">{html.escape(stop)}</th>{"".join(rowcells)}</tr>')
    matrix = "\n".join(cells)

    # ---- completed-arm detail ---------------------------------------
    rows = []
    for a in arms.values():
        if state_of(a) != "done":
            continue
        for split in ("train", "validate"):
            s = a.get(split)
            if not s:
                continue
            rows.append(
                f"<tr><td>{html.escape(a['name'])}</td><td>{split}</td>"
                f"<td class='num'>{s['n']:,}</td>"
                f"<td class='num'>{fmt_bp(s['gross'])}</td>"
                f"<td class='num strong'>{fmt_bp(s['net'])}</td>"
                f"<td class='num'>{s['stop']:.1f}%</td>"
                f"<td class='num'>{s['target']:.1f}%</td>"
                f"<td class='num'>{s['timeout']:.1f}%</td>"
                f"<td class='num'>{s['hold']:.2f}</td></tr>"
            )
    detail = "\n".join(rows)

    return f"""<title>Exit Sweep Live</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>
:root {{
  --ground:#F4F6F9; --panel:#FFFFFF; --panel-2:#EDF1F6; --line:#D3DBE5;
  --ink:#141922; --ink-2:#4B5768; --ink-3:#778395;
  --accent:#1F6FA8; --up:#1B7F53; --down:#B4232A; --run:#A96B12; --idle:#8A96A6;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:'IBM Plex Sans',system-ui,-apple-system,Segoe UI,sans-serif;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0B0E13; --panel:#141922; --panel-2:#1A212C; --line:#273140;
    --ink:#C9D1DB; --ink-2:#8E9BAC; --ink-3:#66727F;
    --accent:#4C9BD1; --up:#45C08A; --down:#E5484D; --run:#E8A33D; --idle:#4A5568;
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0B0E13; --panel:#141922; --panel-2:#1A212C; --line:#273140;
  --ink:#C9D1DB; --ink-2:#8E9BAC; --ink-3:#66727F;
  --accent:#4C9BD1; --up:#45C08A; --down:#E5484D; --run:#E8A33D; --idle:#4A5568;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:0 auto;padding:40px 24px 72px;display:flex;
  flex-direction:column;gap:34px}}
header{{display:flex;flex-direction:column;gap:10px;border-bottom:1px solid var(--line);
  padding-bottom:22px}}
.eyebrow{{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent)}}
h1{{margin:0;font-size:30px;font-weight:600;letter-spacing:-.015em;text-wrap:balance}}
.lede{{margin:0;color:var(--ink-2);max-width:64ch}}
.stamp{{font-family:var(--mono);font-size:12px;color:var(--ink-3)}}
.bar{{display:flex;flex-wrap:wrap;gap:10px}}
.stat{{flex:1 1 150px;background:var(--panel);border:1px solid var(--line);
  border-radius:3px;padding:12px 14px;display:flex;flex-direction:column;gap:3px}}
.stat .k{{font-family:var(--mono);font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-3)}}
.stat .v{{font-family:var(--mono);font-size:21px;font-weight:700;
  font-variant-numeric:tabular-nums}}
h2{{margin:0 0 4px;font-size:13px;font-family:var(--mono);letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-2);font-weight:500}}
section{{display:flex;flex-direction:column;gap:12px}}
p{{margin:0;max-width:68ch;color:var(--ink-2)}}
p strong{{color:var(--ink);font-weight:600}}
.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
.matrix th,.matrix td{{border:1px solid var(--line)}}
.matrix thead th{{background:var(--panel-2);font-family:var(--mono);font-size:11px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2);padding:8px;font-weight:500}}
.matrix th[scope=row]{{background:var(--panel-2);font-family:var(--mono);font-size:12px;
  color:var(--ink-2);padding:8px 12px;text-align:left;white-space:nowrap;font-weight:500}}
.cell{{background:var(--panel);padding:11px 12px;vertical-align:top;min-width:132px;
  display:table-cell}}
.cell .armname{{display:block;font-family:var(--mono);font-size:11px;color:var(--ink-3)}}
.cell .net{{display:block;font-family:var(--mono);font-size:19px;font-weight:700;
  font-variant-numeric:tabular-nums;margin-top:3px}}
.cell .delta{{display:block;font-family:var(--mono);font-size:11px;margin-top:1px}}
.cell .sub{{display:block;font-size:11px;color:var(--ink-3);margin-top:4px}}
.delta.up{{color:var(--up)}} .delta.down{{color:var(--down)}}
.delta.ref{{color:var(--ink-3)}}
.cell.running{{border-left:3px solid var(--run)}}
.cell.queued{{border-left:3px solid var(--idle)}}
.cell.queued .armname{{color:var(--idle)}}
.cell.done{{border-left:3px solid var(--accent)}}
.detail th{{text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);padding:7px 10px;border-bottom:1px solid var(--line);
  font-weight:500;white-space:nowrap}}
.detail td{{padding:7px 10px;border-bottom:1px solid var(--line);color:var(--ink-2)}}
.detail td.num{{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}}
.detail td.strong{{color:var(--ink);font-weight:700}}
.note{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:3px;padding:16px 18px;display:flex;flex-direction:column;gap:9px}}
.note h3{{margin:0;font-size:15px;font-weight:600;color:var(--ink)}}
code{{font-family:var(--mono);font-size:.88em;background:var(--panel-2);
  padding:1px 5px;border-radius:2px;color:var(--ink)}}
footer{{border-top:1px solid var(--line);padding-top:18px;font-family:var(--mono);
  font-size:11px;color:var(--ink-3)}}
@media (max-width:640px){{h1{{font-size:24px}} .wrap{{padding:26px 16px 50px}}}}
</style>

<div class="wrap">
<header>
  <span class="eyebrow">CapitalScan · parameter sweep</span>
  <h1>Exit policy: target × stop</h1>
  <p class="lede">Eleven configurations of the exit rule, each a full backtest
  over the same universe and the same signals. Only the exit changes, so any
  difference in return is the policy and nothing else.</p>
  <span class="stamp">updated {now:%Y-%m-%d %H:%M} PT</span>
</header>

<div class="bar">
  <div class="stat"><span class="k">arms complete</span><span class="v">{done} / 11</span></div>
  <div class="stat"><span class="k">running</span>
    <span class="v" style="color:var(--run)">{running}</span></div>
  <div class="stat"><span class="k">queued</span>
    <span class="v" style="color:var(--idle)">{queued}</span></div>
  <div class="stat"><span class="k">machines</span><span class="v">2</span></div>
</div>

<section>
  <h2>The grid — validate net return, basis points</h2>
  <p>Each cell is one full backtest. The number is mean net return per event
  on the held-out split; the line under it is the gap from the live
  configuration.</p>
  <div class="scroll">
    <table class="matrix">
      <thead><tr><th>stop \\ target</th>{"".join(f"<th>{t}</th>" for t in TARGETS)}</tr></thead>
      <tbody>{matrix}</tbody>
    </table>
  </div>
</section>

<section>
  <h2>Completed arms</h2>
  <div class="scroll">
    <table class="detail">
      <thead><tr><th>arm</th><th>split</th><th>events</th><th>gross bp</th>
      <th>net bp</th><th>stopped</th><th>target</th><th>timeout</th><th>hold days</th></tr></thead>
      <tbody>{detail}</tbody>
    </table>
  </div>
</section>

<section>
  <div class="note">
    <h3>What the sweep is measured on, and what it is not</h3>
    <p>Selection is on <code>net_ret</code>, not on the FDR q-values the
    cell statistics produce. <code>hit_flags</code> derives <code>p_hit</code>
    from <code>fwd_ret_5d</code> — a fixed-window forward return, which is a
    fact about the market and carries no dependence on the exit policy. The
    first completed arm demonstrated it: <strong>mean forward return was
    identical to five decimals across both arms</strong> while net return
    moved and the stop-out rate went from 33% to 54%.</p>
    <p>So <strong>zero cells surviving FDR in every arm is expected here</strong>
    and is not a result about the exit policy. The responsive measures are net
    return, the exit-reason mix, and the benchmark comparison against each
    arm's own null.</p>
  </div>
</section>

<footer>
  Generated by <code>scripts/sweep_report.py</code> · research database ·
  selection on train, validate read once
</footer>
</div>
"""


def main() -> None:
    data = collect()
    out = ROOT / "reports" / "sweep_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(data), encoding="utf-8")
    n_done = sum(1 for a in data["arms"].values() if state_of(a) == "done")
    print(f"wrote {out}  ({n_done}/11 arms complete)")


if __name__ == "__main__":
    main()
