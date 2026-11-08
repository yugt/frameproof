"""The run page: <run>/index.html, one card per attempt, the final video on top."""

from __future__ import annotations

import json
import os
from pathlib import Path

from frameproof.report import _CSS

_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Studio run · frameproof</title>
<style>""" + _CSS + r"""
.brief { padding: 16px 18px; white-space: pre-wrap; color: var(--muted); }
.final { padding: 14px; margin: 16px 0 24px; }
.final video { width: 100%; max-height: 70vh; background: #000; border-radius: 8px; display: block; }
.steps { display: grid; gap: 14px; }
.step { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 16px; padding: 14px; color: inherit; }
.step:hover { border-color: var(--accent); text-decoration: none; }
.step img { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 6px; background: #000; }
@media (max-width: 700px) { .step { grid-template-columns: minmax(0, 1fr); } }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
ul { margin: 8px 0 0; padding-left: 18px; }
li { font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.arrow { color: var(--muted); text-align: center; font-size: 12px; }
.status { font-weight: 800; }
</style></head>
<body>
<header><span class="brand">frameproof studio</span><h1 id="title"></h1><span id="status" class="badge"></span></header>
<main>
  <div class="panel brief" id="brief"></div>
  <div id="reference"></div>
  <div id="final"></div>
  <h2 class="muted" style="font-size:15px;margin:22px 0 12px">Attempts: the proposer only ever sees public failures</h2>
  <div class="steps" id="steps"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
document.getElementById('title').textContent = D.brief.id;
const st = document.getElementById('status'); st.textContent = D.status;
st.classList.add(D.status === 'DELIVERED' || D.status === 'ACCEPT' ? 'ACCEPT' : (D.status === 'REVISE' ? '' : 'BLOCK'));
document.getElementById('brief').textContent = D.brief.text;
if (D.panel && D.panel.label) {
  const c = el('div', 'panel brief', 'Checks: ' + D.panel.label + (D.panel.why ? ' - ' + D.panel.why : '') + (D.panel.note ? ' ' + D.panel.note : ''));
  if (D.panel.mode === 'generic') c.style.borderColor = 'var(--soft)';
  c.style.marginTop = '10px'; document.getElementById('brief').after(c);
}
if (D.reference_frames && D.reference_frames.length) {
  const r = el('div', 'panel final'); r.append(el('div', 'muted', 'Reference · ' + D.reference_kind + ' · ' + D.reference_mode));
  const row = el('div', 'row');
  D.reference_frames.forEach((frame, i) => {
    const item = el('div'); item.style.width = '200px';
    const im = el('img'); im.src = frame; im.style.width = '100%'; item.append(im);
    item.append(el('div', 'muted', D.reference_times[i] + ' s')); row.append(item);
  });
  r.append(row); document.getElementById('reference').append(r);
}
if (D.final_video) {
  const f = el('div', 'panel final'); f.append(el('div', 'muted', 'Delivered video' + (D.final_kind ? ' · ' + D.final_kind : '')));
  const v = document.createElement('video'); v.src = D.final_video; v.controls = true; v.muted = true; v.playsInline = true;
  f.append(v); document.getElementById('final').append(f);
}
const steps = document.getElementById('steps');
D.history.forEach((h, i) => {
  if (i) steps.append(el('div', 'arrow', D.history[i - 1].decision === 'BLOCK' ? `↓ ${D.history[i - 1].public_feedback.length} public failures sent back` +
    (D.history[i - 1].hidden_failures ? ` · ${D.history[i - 1].hidden_failures} hidden failures withheld` : '') : '↓ User revision'));
  const a = el('a', 'panel step'); a.href = h.report_rel;
  if (h.thumb) { const im = el('img'); im.src = h.thumb; a.append(im); } else a.append(el('div'));
  const b = el('div');
  const r = el('div', 'row'); r.append(el('b', null, 'Attempt ' + h.attempt)); r.append(el('span', 'badge ' + h.decision, h.decision));
  r.append(el('span', 'tag', h.proposer)); r.append(el('span', 'muted', 'score ' + h.score.toFixed(2))); b.append(r);
  if (h.judge && h.judge.scores) { const j = el('div', 'muted', 'judge ' + h.judge.model + ': ' +
    Object.entries(h.judge.scores).map(([k, v]) => k.replace('_', ' ') + ' ' + v).join(' · ')); b.append(j); }
  else if (h.judge && h.judge.error) b.append(el('div', 'muted', 'judge error: ' + h.judge.error));
  const ul = el('ul'); (h.reasons.length ? h.reasons.slice(0, 4) : ['all checks passed']).forEach(x => ul.append(el('li', null, x)));
  if (h.reasons.length > 4) ul.append(el('li', 'muted', `… ${h.reasons.length - 4} more`));
  b.append(ul); a.append(b); steps.append(a);
});
</script></body></html>
"""


def _thumb(attempt_dir: Path, run_dir: Path) -> str | None:
    frames = sorted((attempt_dir / "frames").glob("t*.png"))
    if not frames:
        return None
    return os.path.relpath(frames[len(frames) // 2], run_dir).replace(os.sep, "/")


def write_studio_index(run_dir: Path, brief: dict, history: list[dict], status: str,
                       final_video: Path | None = None) -> Path:
    run_dir = Path(run_dir)
    hist = []
    for h in history:
        ad = Path(h["run_dir"])
        hist.append({**h, "report_rel": os.path.relpath(ad / "report.html", run_dir).replace(os.sep, "/"),
                     "thumb": _thumb(ad, run_dir)})
    kind = ""
    if (run_dir / "desk.json").exists():
        kind = {"hyperframes": "HyperFrames render", "stand-in": "stand-in (probe preview)"}.get(
            json.loads((run_dir / "desk.json").read_text()).get("kind", ""), "")
    panel = {}
    if (run_dir / "panel.json").exists():
        pj = json.loads((run_dir / "panel.json").read_text())
        readers = pj.get("readers") or {}
        label = {"panel": "checker panel: " + (", ".join(f"{m} ({h})" for m, h in readers.items()) or "?"),
                 "fixture": "the example's hand-written checks (fixture; no checker produced checks)",
                 "generic": "generic checks only"}.get(pj.get("mode"), pj.get("mode", ""))
        panel = {"mode": pj.get("mode"), "label": label, "why": pj.get("why", ""),
                 "note": pj.get("note", "") if pj.get("mode") == "generic" else ""}
    ref_path = run_dir / "reference" / "reference.json"
    ref = json.loads(ref_path.read_text()) if ref_path.exists() else {}
    data = {"reference_frames": [os.path.relpath(p, run_dir) for p in ref.get("frames", [])],
            "reference_kind": ref.get("kind"), "reference_mode": ref.get("mode"),
            "reference_times": ref.get("times", []), "brief": brief, "history": hist, "status": status, "final_kind": kind, "panel": panel,
            "final_video": os.path.relpath(final_video, run_dir) if final_video else None}
    out = run_dir / "index.html"
    out.write_text(_PAGE.replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return out


