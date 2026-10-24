"""Export a finished scan as one self-contained HTML report (pure, no Qt).

The page has inline CSS, no scripts, no web fonts and no external files,
so it opens in any browser and prints to PDF. On screen it uses the
app's HUD palette; printed, it switches to dark text on white.

    computer_name()          this PC's name
    default_report_name()    SysCheck_<computer>_<YYYY-MM-DD>.html
    render_html_report()     results dict -> HTML string
"""

from __future__ import annotations

import os
import platform
import re
from datetime import datetime
from html import escape
from typing import Dict, List, Optional

from . import __version__, config
from .diskhealth.describe import (SOURCE_LABELS, DriveAssessment, assess,
                                  condition, detail_rows, no_table_reason,
                                  size_words, smart_rows)
from .scoring import (STATUS_WORDS, UNSCORED, ComponentScore,
                      _band_from_status, build_report, headline_for,
                      subhead_for)


def computer_name() -> str:
    """This PC's name (``COMPUTERNAME`` on Windows), or "PC"."""
    name = os.environ.get("COMPUTERNAME") or platform.node() or ""
    return name.strip() or "PC"


def default_report_name(computer: str, when: datetime) -> str:
    """``SysCheck_<computer>_<YYYY-MM-DD>.html``, safe as a file name."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", computer).strip("-.") or "PC"
    return f"SysCheck_{safe}_{when:%Y-%m-%d}.html"


# ─────────────────────────────────────────────────────────────────────
# Page pieces
# ─────────────────────────────────────────────────────────────────────
_GRADE_CLASS = {"A": "pass", "B": "blue", "C": "warn", "F": "fail",
                "N/A": "dim"}
_STATUS_CLASS = {"pass": "pass", "warn": "warn", "fail": "fail",
                 "inconclusive": "warn", "skip": "dim"}
_ROW_CLASS = {"ok": "pass", "info": "blue", "warn": "warn", "fail": "fail"}


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _band(pct: Optional[float]) -> str:
    """CSS class for a Health %, on the score's bands."""
    return "dim" if pct is None else _band_from_status(pct)


def _css() -> str:
    c = config
    return f"""
:root {{
  --bg: {c.COL_BG}; --panel: {c.COL_PANEL}; --panel-hi: {c.COL_PANEL_HI};
  --grid: {c.COL_GRID}; --text: {c.COL_TEXT}; --dim: {c.COL_TEXT_DIM};
  --blue: {c.COL_BLUE}; --green: {c.COL_GREEN}; --amber: {c.COL_AMBER};
  --red: {c.COL_RED}; --well: #070808;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 Consolas, "Courier New", monospace;
}}
main {{ max-width: 1040px; margin: 0 auto; padding: 24px 16px 40px; }}
h1, h2, h3, h4 {{ margin: 0; letter-spacing: 1px; }}
h1 {{ color: var(--blue); font-size: 24px; letter-spacing: 2px; }}
h2 {{ font-size: 17px; margin: 28px 0 10px; }}
h3 {{ font-size: 15px; }}
h4 {{ font-size: 13px; color: var(--dim); margin: 16px 0 6px; }}
p {{ margin: 6px 0; }}
.dim {{ color: var(--dim); }}
.pass {{ color: var(--green); }} .warn {{ color: var(--amber); }}
.fail {{ color: var(--red); }} .blue {{ color: var(--blue); }}
.panel {{
  background: var(--panel); border: 1px solid var(--grid);
  border-radius: 6px; padding: 14px 16px; margin: 10px 0;
}}
.accent {{ border-color: var(--blue); }}
.meta {{ display: flex; flex-wrap: wrap; gap: 4px 28px; margin-top: 6px; }}
.label {{ color: var(--dim); font-size: 12px; letter-spacing: 1px; }}
.verdict {{ display: flex; gap: 20px; align-items: center; }}
.grade {{
  flex: none; width: 110px; height: 110px; border-radius: 50%;
  border: 8px solid currentColor; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}}
.grade b {{ font-size: 34px; line-height: 1; color: var(--text); }}
.grade span {{ font-size: 12px; color: var(--dim); }}
.verdict h2 {{ margin: 0 0 4px; font-size: 20px; }}
.head {{
  display: flex; justify-content: space-between; align-items: baseline;
  gap: 12px; flex-wrap: wrap; border-bottom: 1px solid var(--grid);
  padding-bottom: 6px; margin-bottom: 8px;
}}
.status {{ font-weight: bold; }}
ul {{ margin: 4px 0; padding-left: 18px; }}
li {{ margin: 2px 0; }}
ul.plain {{ list-style: none; padding-left: 0; }}
ul.tech {{ list-style: none; padding-left: 0; color: var(--green);
  font-size: 12px; }}
.health {{ display: flex; align-items: center; gap: 12px; margin: 8px 0; }}
.bar {{
  flex: 1; height: 28px; padding: 4px; background: var(--well);
  border: 1px solid var(--grid);
}}
.fill {{
  height: 100%; background: repeating-linear-gradient(90deg,
    currentColor 0 16px, transparent 16px 20px);
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}}
.pct {{ font-size: 24px; font-weight: bold; min-width: 72px;
  text-align: right; }}
.word {{ font-weight: bold; letter-spacing: 1px; min-width: 90px; }}
.scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
table.details td {{ padding: 3px 10px 3px 0; vertical-align: top; }}
table.details td.label {{ white-space: nowrap; }}
table.smart {{ background: var(--well); border: 1px solid var(--grid); }}
table.smart th {{
  background: var(--panel-hi); color: var(--blue); text-align: left;
  padding: 5px 8px; border-bottom: 1px solid var(--grid);
  letter-spacing: 1px;
}}
table.smart td {{ padding: 4px 8px; white-space: nowrap; }}
table.smart tr:nth-child(even) td {{ background: var(--panel); }}
table.smart .num {{ text-align: center; }}
footer {{ color: var(--dim); font-size: 12px; margin-top: 28px;
  border-top: 1px solid var(--grid); padding-top: 10px; }}
@media (max-width: 640px) {{
  .verdict {{ flex-direction: column; align-items: flex-start; }}
  .health {{ flex-wrap: wrap; }}
  .bar {{ flex-basis: 100%; }}
}}
@media print {{
  :root {{
    --bg: #fff; --panel: #fff; --panel-hi: #eef2f3; --grid: #b9c2c6;
    --text: #111; --dim: #555; --blue: #006b7d; --green: #1c7a00;
    --amber: #a35f00; --red: #c00000; --well: #fff;
  }}
  main {{ max-width: none; padding: 0; }}
  .panel, tr {{ break-inside: avoid; }}
  h2, h4 {{ break-after: avoid; page-break-after: avoid; }}
  table.smart tr:nth-child(even) td {{ background: #f4f6f7; }}
}}
"""


def _verdict(report: Dict[str, object]) -> str:
    overall = report["overall"]
    grade = overall["grade"]
    score = overall["score"]
    return f"""
<section class="panel accent verdict">
  <div class="grade {_GRADE_CLASS.get(grade, 'dim')}">
    <b>{_e(grade)}</b>
    <span>{f"{score:.0f}/100" if score is not None else "NO DATA"}</span>
  </div>
  <div>
    <div class="label">DIAGNOSTIC VERDICT</div>
    <h2 class="{_GRADE_CLASS.get(grade, 'dim')}">{_e(headline_for(grade))}</h2>
    <p class="dim">{_e(subhead_for(report["components"]))}</p>
  </div>
</section>"""


def _specs(specs: object) -> str:
    if not isinstance(specs, dict) or not specs:
        return ""
    cpu = specs.get("cpu") or {}
    ram = specs.get("ram") or {}
    board = specs.get("board") or {}
    rows = [
        ("CPU", f"{cpu.get('name') or '?'} — {cpu.get('cores_physical') or '?'}"
                f" cores / {cpu.get('cores_logical') or '?'} threads"),
        ("Memory", " ".join(str(v) for v in (
            f"{ram.get('total_gb') or '?'} GB", ram.get("type"),
            f"{ram.get('speed_mhz')} MHz" if ram.get("speed_mhz") else None)
            if v)),
    ]
    for gpu in specs.get("gpus") or []:
        vram = (f" ({gpu['vram_mb']:.0f} MB)" if gpu.get("vram_mb") else "")
        rows.append(("Graphics", f"{gpu.get('name') or '?'}{vram}"))
    rows.append(("Board", f"{board.get('board_mfr') or '?'} "
                          f"{board.get('board_model') or ''}".strip()))
    rows.append(("BIOS", f"{board.get('bios_version') or '?'} "
                         f"({board.get('bios_date') or '?'})"))
    body = "".join(f"<tr><td class='label'>{_e(k.upper())}</td>"
                   f"<td>{_e(v)}</td></tr>" for k, v in rows)
    return (f"<h2>SYSTEM</h2><section class='panel'>"
            f"<table class='details'>{body}</table></section>")


def _component(c: ComponentScore) -> str:
    cls = _STATUS_CLASS.get(c.status, "dim")
    score = "" if c.status in UNSCORED else f"{c.score:.0f}/100 · "
    details = "".join(f"<li>{_e(d)}</li>" for d in c.details)
    tech = ""
    if c.technical:
        tech = ("<h4>TECHNICAL DETAILS</h4><ul class='tech'>"
                + "".join(f"<li>{_e(t)}</li>" for t in c.technical)
                + "</ul>")
    return f"""
<section class="panel">
  <div class="head">
    <h3>{_e(c.label.upper())}</h3>
    <span class="status {cls}">{score}{_e(STATUS_WORDS.get(c.status, c.status))}</span>
  </div>
  <p>{_e(c.note)}</p>
  {f'<ul class="dim">{details}</ul>' if details else ''}
  {tech}
</section>"""


def _detail_table(rows: List[tuple]) -> str:
    half = (len(rows) + 1) // 2
    left, right = rows[:half], rows[half:]
    out = []
    for i, (k, v) in enumerate(left):
        cells = f"<td class='label'>{_e(k.upper())}</td><td>{_e(v)}</td>"
        if i < len(right):
            rk, rv = right[i]
            cells += (f"<td class='label'>{_e(rk.upper())}</td>"
                      f"<td>{_e(rv)}</td>")
        out.append(f"<tr>{cells}</tr>")
    return f"<table class='details'>{''.join(out)}</table>"


def _smart_table(a: DriveAssessment) -> str:
    rows = smart_rows(a.drive)
    if not rows:
        return f"<p class='dim'>{_e(no_table_reason(a.drive))}</p>"
    head = "".join(f"<th>{h}</th>" for h in (
        "ID", "ATTRIBUTE", "VALUE", "WORST", "THRESH", "RAW", "STATUS"))
    body = []
    for r in rows:
        cls = _ROW_CLASS[r.severity]
        name_cls = f" class='{cls}'" if r.severity in ("warn", "fail") else ""
        body.append(
            f"<tr><td class='num'>{_e(r.id)}</td><td{name_cls}>{_e(r.name)}"
            f"</td><td class='num'>{_e(r.value)}</td><td class='num'>"
            f"{_e(r.worst)}</td><td class='num'>{_e(r.thresh)}</td>"
            f"<td>{_e(r.raw)}</td><td class='{cls}'>{_e(r.status)}</td></tr>")
    return (f"<div class='scroll'><table class='smart'><thead><tr>{head}"
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def _drive(a: DriveAssessment, hide_serial: bool) -> str:
    pct = a.health.health_pct
    band = _band(pct)
    life_cls = ("fail" if a.life.replace_soon
                else "dim" if a.life.days is None else "")
    life = a.life.text + (" (rough: power-on time estimated)"
                          if a.life.rough else "")
    tech = "".join(f"<li>{_e(t)}</li>" for t in a.health.technical())
    source = SOURCE_LABELS.get(a.drive.get("source"), "?")
    kind_size = f"{a.drive.get('kind')} · {size_words(a.drive.get('size_gb'))}"
    return f"""
<section class="panel accent">
  <div class="head">
    <h3>{_e(a.name.upper())}</h3>
    <span class="dim">{_e(kind_size)}</span>
  </div>
  <div class="health">
    <span class="label">HEALTH</span>
    <div class="bar"><div class="fill {band}" style="width:{pct or 0}%"></div></div>
    <span class="pct {band}">{f"{pct}%" if pct is not None else "N/A"}</span>
    <span class="word {band}">{_e(condition(pct))}</span>
  </div>
  <p>{_e(a.summary)}</p>
  <p><span class="label">ESTIMATED REMAINING LIFE</span>
     <strong class="{life_cls}">{_e(life)}</strong></p>
  <h4>DRIVE DETAILS</h4>
  {_detail_table(detail_rows(a.drive, hide_serial=hide_serial))}
  <h4>S.M.A.R.T. ATTRIBUTES <span class="dim">· read by {_e(source)}</span></h4>
  {_smart_table(a)}
  <h4>HOW THE HEALTH % WAS WORKED OUT</h4>
  <ul class="tech">{tech}</ul>
</section>"""


def _storage(storage: object, hide_serials: bool) -> str:
    drives = (storage or {}).get("drives") if isinstance(storage, dict) else None
    if not drives:
        body = "<section class='panel dim'>No drive data was collected.</section>"
    else:
        body = "".join(_drive(assess(d), hide_serials) for d in drives)
    return f"<h2>STORAGE</h2>{body}"


# ─────────────────────────────────────────────────────────────────────
# Whole page
# ─────────────────────────────────────────────────────────────────────
def render_html_report(results: Dict[str, object], *, computer: str,
                       generated: datetime, hide_serials: bool = False,
                       admin: Optional[bool] = None) -> str:
    """The full report: verdict, system, every subsystem, then storage
    with each drive's S.M.A.R.T. table. ``hide_serials`` replaces drive
    serial numbers with "Hidden"."""
    results = results or {}
    report = build_report(results)
    admin_text = {True: "Yes", False: "No"}.get(admin, "Unknown")
    title = f"SysCheck report — {computer} — {generated:%Y-%m-%d}"
    components = "".join(_component(c) for c in report["components"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{_css()}</style>
</head>
<body>
<main>
<header>
  <h1>SYSCHECK // LAPTOP DIAGNOSTIC REPORT</h1>
  <div class="meta dim">
    <span>COMPUTER <b>{_e(computer)}</b></span>
    <span>GENERATED <b>{generated:%Y-%m-%d %H:%M}</b></span>
    <span>RUN AS ADMINISTRATOR <b>{admin_text}</b></span>
    <span>SYSCHECK <b>{_e(__version__)}</b></span>
    {"<span>SERIAL NUMBERS <b>hidden</b></span>" if hide_serials else ""}
  </div>
</header>
{_verdict(report)}
{_specs(results.get("specs"))}
<h2>SUBSYSTEM RESULTS</h2>
{components}
{_storage(results.get("storage"), hide_serials)}
<footer>
  <p>Drive Health % and remaining life are estimates worked out from each
  drive's own S.M.A.R.T. records. They are not a guarantee: any drive can
  fail without warning, so keep backups.</p>
  <p>Drive data is read with smartctl from smartmontools
  (https://www.smartmontools.org/, GPL v2 or later) where it can run,
  otherwise from Windows' own storage counters.</p>
</footer>
</main>
</body>
</html>
"""
