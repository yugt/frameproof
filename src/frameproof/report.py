"""Static HTML reports: open in any browser, no server.

A run directory (written by verify / bench) holds:
  verdict.json   decision, score, every check result with its times and brief source
  frames/        probe frames, t<sec>.png
  video.mp4      rendered by HyperFrames (optional; falls back to a frame player)
  index.html     the composition that was verified (optional)

`render_run` writes report.html into the run directory: the video with a check
timeline under it (click a marker to jump there and see what the probe measured),
the verdict, and the evidence table. `compare_to` adds a second, synced video
(e.g. the original next to a mutant). `render_index` writes the bench overview.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

_T = re.compile(r"\((\d+(?:\.\d+)?),")


def _frames(run: Path) -> list[dict]:
    out = []
    for p in sorted((run / "frames").glob("t*.png")):
        try:
            out.append({"t": float(p.stem[1:]), "src": f"frames/{p.name}"})
        except ValueError:
            pass
    return out


def _result_times(r: dict) -> list[float]:
    """Times a result points at: its own, or for generic checks the failing times in its detail."""
    if r.get("times"):
        return r["times"]
    if not r["passed"]:
        return sorted({float(t) for t in _T.findall(r.get("detail", ""))})[:12]
    return []


def _load(run: Path) -> dict:
    v = json.loads((run / "verdict.json").read_text())
    for r in v["results"]:
        r["marks"] = _result_times(r)
    return v


def _rel(path: Path, start: Path) -> str:
    return os.path.relpath(path, start).replace(os.sep, "/")


def render_run(run: Path, compare_to: Path | None = None, title: str | None = None,
               back_link: str | None = None) -> Path:
    run = Path(run)
    v = _load(run)
    meta = v.get("meta", {})
    data = {
        "title": title or meta.get("brief", run.name),
        "verdict": v,
        "duration": meta.get("duration") or max([f["t"] for f in _frames(run)] or [5.0]),
        "frames": _frames(run),
        "video": "video.mp4" if (run / "video.mp4").exists() else None,
        "video_kind": (meta.get("render") or {}).get("kind", ""),
        "back": back_link,
        "compare": None,
    }
    if compare_to:
        compare_to = Path(compare_to)
        cv = _load(compare_to)
        data["compare"] = {
            "label": cv.get("meta", {}).get("variant", compare_to.name),
            "decision": cv["decision"], "score": cv["score"],
            "video": _rel(compare_to / "video.mp4", run) if (compare_to / "video.mp4").exists() else None,
            "frames": [{"t": f["t"], "src": _rel(compare_to / f["src"], run)} for f in _frames(compare_to)],
        }
    out = run / "report.html"
    out.write_text(_PAGE.replace("__TITLE__", html.escape(str(data["title"])))
                   .replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return out


def render_index(bench_dir: Path, rows: list[dict], summary_md: str) -> Path:
    """Overview of a bench run: one card per variant, linking to its report."""
    bench_dir = Path(bench_dir)
    cards = []
    for r in rows:
        run = bench_dir / r["brief"] / r["variant"]
        frames = _frames(run)
        v = _load(run) if (run / "verdict.json").exists() else None
        fail_t = next((t for res in (v or {}).get("results", []) if not res["passed"] for t in res["marks"]), None)
        thumb = None
        if frames:
            target = fail_t if fail_t is not None else frames[len(frames) // 2]["t"]
            thumb = min(frames, key=lambda f: abs(f["t"] - target))["src"]
        cards.append({**r, "href": _rel(run / "report.html", bench_dir),
                      "thumb": _rel(run / thumb, bench_dir) if thumb else None,
                      "correct": r["decision"] == r["expected"]})
    muts = [c for c in cards if c["expected"] == "BLOCK"]
    orig = [c for c in cards if c["expected"] == "ACCEPT"]
    data = {
        "cards": cards,
        "caught": sum(c["decision"] == "BLOCK" for c in muts), "mutants": len(muts),
        "false_blocks": sum(c["decision"] == "BLOCK" for c in orig), "originals": len(orig),
        "name": bench_dir.name, "summary": summary_md,
    }
    out = bench_dir / "index.html"
    out.write_text(_INDEX.replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return out


# ------------------------------------------------------------------- templates

_CSS = r"""
:root {
  --bg: #0f1115; --panel: #171a21; --panel-2: #1e222b; --line: #2a2f3a; --text: #e8eaef;
  --muted: #9aa3b2; --pass: #2fbf71; --fail: #ff5d5d; --soft: #f0b429; --accent: #6ea8fe;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: light) {
  :root { --bg: #f6f7f9; --panel: #ffffff; --panel-2: #f0f2f5; --line: #dde1e7; --text: #161a22;
          --muted: #5d6675; --pass: #128a4c; --fail: #d93636; --soft: #b7791f; --accent: #2563eb; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header { display: flex; align-items: center; gap: 16px; padding: 14px 24px; border-bottom: 1px solid var(--line);
         background: var(--panel); position: sticky; top: 0; z-index: 5; flex-wrap: wrap; }
header h1 { font-size: 16px; margin: 0; font-weight: 650; }
.brand { font-weight: 700; letter-spacing: .3px; color: var(--muted); }
.badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
         font-weight: 700; font-size: 12px; letter-spacing: .4px; }
.badge.ACCEPT { background: color-mix(in srgb, var(--pass) 18%, transparent); color: var(--pass); }
.badge.BLOCK { background: color-mix(in srgb, var(--fail) 18%, transparent); color: var(--fail); }
.muted { color: var(--muted); }
main { padding: 20px 24px 60px; max-width: 1500px; margin: 0 auto; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; }
"""

_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · frameproof</title>
<style>""" + _CSS + r"""
.stage { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr); }
.stage.two { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
@media (max-width: 900px) { .stage.two { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 700px) { main { padding: 14px 16px 40px; } header { padding: 12px 16px; }
  th:nth-child(3), td:nth-child(3) { display: none; } .axes { grid-template-columns: repeat(3, 1fr); } }
.screen { padding: 12px; }
.screen .label { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; gap: 8px; }
.media { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #000; border-radius: 8px; overflow: hidden; }
.media video, .media img { width: 100%; height: 100%; object-fit: contain; display: block; }
.controls { display: flex; align-items: center; gap: 12px; margin: 14px 0 6px; flex-wrap: wrap; }
button { background: var(--panel-2); color: var(--text); border: 1px solid var(--line); border-radius: 8px;
         padding: 6px 12px; font: inherit; cursor: pointer; }
button:hover { border-color: var(--accent); }
.clock { font-family: var(--mono); color: var(--muted); min-width: 120px; }
.timeline { position: relative; height: 64px; margin: 4px 10px 18px; cursor: pointer; }
.track { position: absolute; left: 0; right: 0; top: 30px; height: 4px; background: var(--line); border-radius: 2px; }
.played { position: absolute; left: 0; top: 30px; height: 4px; background: var(--accent); border-radius: 2px; }
.head { position: absolute; top: 18px; width: 2px; height: 28px; background: var(--accent); transform: translateX(-1px); }
.tick { position: absolute; top: 50px; font: 11px var(--mono); color: var(--muted); transform: translateX(-50%); }
.mark { position: absolute; width: 14px; height: 14px; border-radius: 50%; transform: translate(-50%, 0);
        border: 2px solid var(--panel); box-shadow: 0 0 0 1px var(--line); }
.mark.pass { background: var(--pass); }
.mark.fail { background: var(--fail); }
.mark.soft { border-radius: 3px; }
.mark.hidden { outline: 2px dashed var(--muted); outline-offset: 2px; }
.mark.hot { box-shadow: 0 0 0 3px var(--accent); z-index: 3; }
.grid2 { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; }
@media (max-width: 1100px) { .grid2 { grid-template-columns: minmax(0, 1fr); } }
.verdict { padding: 16px 18px; }
.verdict .big { font-size: 28px; font-weight: 800; letter-spacing: .5px; }
.verdict.ACCEPT .big { color: var(--pass); }
.verdict.BLOCK .big { color: var(--fail); }
.reasons { margin: 10px 0 0; padding-left: 18px; }
.axes { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-top: 12px; }
.axis { background: var(--panel-2); border-radius: 8px; padding: 8px; text-align: center; }
.axis b { display: block; font-size: 18px; }
.probe { padding: 12px; }
.probe img { width: 100%; border-radius: 6px; display: block; background: #000; }
.probe .detail { margin-top: 8px; font-family: var(--mono); font-size: 12px; white-space: pre-wrap; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 12px; color: var(--muted); font-weight: 600; position: sticky; top: 0; background: var(--panel); }
tr.row { cursor: pointer; }
tr.row:hover, tr.row.hot { background: var(--panel-2); }
.ok { color: var(--pass); font-weight: 700; }
.no { color: var(--fail); font-weight: 700; }
.pill { font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
.src { color: var(--muted); font-style: italic; }
.det { font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
td { overflow-wrap: anywhere; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend .mark { position: static; transform: none; display: inline-block; }
.filters { display: flex; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.filters button.on { border-color: var(--accent); color: var(--accent); }
</style></head>
<body>
<header>
  <span class="brand">frameproof</span>
  <h1 id="title"></h1>
  <span id="badge" class="badge"></span>
  <span class="muted" id="score"></span>
  <span style="flex:1"></span>
  <a id="back" hidden>← all variants</a>
</header>
<main>
  <section class="stage" id="stage"></section>
  <div class="controls">
    <button id="play">▶ Play</button>
    <span class="clock" id="clock">0.00 s</span>
    <div class="legend">
      <span><i class="mark pass"></i> pass</span><span><i class="mark fail"></i> fail</span>
      <span><i class="mark soft pass"></i> soft check</span><span><i class="mark hidden pass"></i> hidden from implementer</span>
    </div>
  </div>
  <div class="timeline" id="timeline"><div class="track"></div><div class="played" id="played"></div><div class="head" id="head"></div></div>

  <div class="grid2">
    <div class="panel">
      <div class="filters">
        <button data-f="all" class="on">All</button><button data-f="fail">Failed</button>
        <button data-f="hard">Hard</button><button data-f="hidden">Hidden</button>
      </div>
      <div style="max-height: 620px; overflow: auto;">
        <table><thead><tr><th></th><th>Check</th><th>From the brief</th><th>Measured</th></tr></thead>
        <tbody id="rows"></tbody></table>
      </div>
    </div>
    <div style="display:grid; gap:16px; align-content:start;">
      <div class="panel verdict" id="verdict"></div>
      <div class="panel probe">
        <div class="label muted" id="probe-label">What the probe measured: click a marker or a row</div>
        <img id="probe-img" alt="probe frame">
        <div class="detail" id="probe-detail"></div>
      </div>
    </div>
  </div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const V = D.verdict, DUR = D.duration;
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

$('title').textContent = D.title;
$('badge').textContent = V.decision; $('badge').classList.add(V.decision);
$('score').textContent = 'score ' + V.score.toFixed(2);
if (D.back) { $('back').href = D.back; $('back').hidden = false; }

// ---- players: a real <video> when rendered, else a frame player over probe frames
function makePlayer(spec, label, decision) {
  const box = el('div', 'panel screen');
  const lab = el('div', 'label');
  lab.append(el('b', null, label));
  if (decision) { const b = el('span', 'badge ' + decision, decision); lab.append(b); }
  box.append(lab);
  const media = el('div', 'media'); box.append(media);
  let api;
  if (spec.video) {
    const v = document.createElement('video'); v.src = spec.video; v.muted = true; v.playsInline = true; v.preload = 'auto';
    media.append(v);
    api = { el: v, get t() { return v.currentTime; }, seek: t => { v.currentTime = t; }, play: () => v.play(), pause: () => v.pause(),
            get paused() { return v.paused; } };
  } else {
    const img = el('img'); media.append(img);
    const fr = spec.frames.slice().sort((a, b) => a.t - b.t);
    let t = 0, timer = null, last = 0;
    const show = () => { if (!fr.length) return; let best = fr[0]; for (const f of fr) if (f.t <= t + 1e-6) best = f; img.src = best.src; };
    const tick = now => { if (!timer) return; t += (now - last) / 1000; last = now; if (t >= DUR) { t = DUR; api.pause(); } show(); timer = requestAnimationFrame(tick); };
    api = { el: img, get t() { return t; }, seek: x => { t = Math.max(0, Math.min(DUR, x)); show(); },
            play: () => { if (t >= DUR) t = 0; last = performance.now(); timer = requestAnimationFrame(tick); },
            pause: () => { cancelAnimationFrame(timer); timer = null; }, get paused() { return !timer; } };
    show();
    lab.append(el('span', 'pill', 'frame player (no render)'));
  }
  return { box, api };
}

const players = [];
const kindLabel = { 'probe-preview': 'probe preview', 'hyperframes': 'HyperFrames render' }[D.video_kind] || '';
const main = makePlayer({ video: D.video, frames: D.frames }, (D.compare ? 'This variant' : 'Video') + (kindLabel ? ' · ' + kindLabel : ''), D.compare ? V.decision : null);
players.push(main.api); $('stage').append(main.box);
if (D.compare) {
  const c = makePlayer(D.compare, 'Compared with: ' + D.compare.label, D.compare.decision);
  players.push(c.api); $('stage').prepend(c.box); $('stage').classList.add('two');
}
const seekAll = t => players.forEach(p => p.seek(t));
$('play').onclick = () => {
  if (main.api.paused) { if (main.api.t >= DUR - 0.05) seekAll(0); players.forEach(p => p.play()); $('play').textContent = '❚❚ Pause'; }
  else { players.forEach(p => p.pause()); $('play').textContent = '▶ Play'; }
};
if (main.api.el.tagName === 'VIDEO') main.api.el.addEventListener('ended', () => { $('play').textContent = '▶ Play'; });

// ---- timeline
const tl = $('timeline');
const pct = t => (100 * t / DUR) + '%';
for (let s = 0; s <= Math.floor(DUR); s++) { const k = el('div', 'tick', s + 's'); k.style.left = pct(s); tl.append(k); }
const marks = [];
V.results.forEach((r, i) => {
  r.marks.forEach(t => {
    const m = el('div', 'mark ' + (r.passed ? 'pass' : 'fail') + (r.severity === 'soft' ? ' soft' : '') + (r.hidden ? ' hidden' : ''));
    m.style.left = pct(Math.min(t, DUR)); m.style.top = '2px';
    m.title = `${r.id} @ ${t}s: ${r.detail}`;
    m.onclick = ev => { ev.stopPropagation(); focus(i, t); };
    tl.append(m); marks.push({ m, i });
  });
});
tl.onclick = ev => { const r = tl.getBoundingClientRect(); seekAll(DUR * (ev.clientX - r.left) / r.width); };
function frameAt(t) { let best = D.frames[0]; for (const f of D.frames) if (Math.abs(f.t - t) < Math.abs(best.t - t)) best = f; return best; }
(function loop() {
  const t = main.api.t; $('played').style.width = pct(t); $('head').style.left = pct(t);
  $('clock').textContent = t.toFixed(2) + ' / ' + DUR.toFixed(2) + ' s';
  if (!main.api.paused) players.forEach(p => { if (p !== main.api && Math.abs(p.t - t) > 0.12) p.seek(t); });
  requestAnimationFrame(loop);
})();

// ---- verdict panel
const vp = $('verdict'); vp.classList.add(V.decision);
vp.append(el('div', 'big', V.decision));
const passed = V.results.filter(r => r.passed).length;
vp.append(el('div', 'muted', `${passed}/${V.results.length} checks passed · score ${V.score.toFixed(2)}` +
  (V.meta && V.meta.variant ? ` · variant: ${V.meta.variant}` : '')));
const ul = el('ul', 'reasons');
(V.reasons.length ? V.reasons : ['all checks passed']).forEach(x => ul.append(el('li', null, x)));
vp.append(ul);
if (V.judge) {
  const ax = el('div', 'axes');
  Object.entries(V.judge.scores).forEach(([k, s]) => { const a = el('div', 'axis'); a.append(el('b', null, s)); a.append(el('span', 'muted', k.replace('_', ' '))); ax.append(a); });
  vp.append(ax); vp.append(el('div', 'muted', 'Judge ' + V.judge.model + ': ' + (V.judge.notes || '')));
}

// ---- evidence table
const rowsEl = $('rows'); const rowEls = [];
V.results.forEach((r, i) => {
  const tr = el('tr', 'row'); tr.dataset.i = i;
  tr.append(el('td', r.passed ? 'ok' : 'no', r.passed ? '✓' : '✗'));
  const c = el('td'); c.append(el('div', null, r.id));
  const tags = el('div'); tags.append(el('span', 'pill', r.kind)); tags.append(document.createTextNode(' '));
  tags.append(el('span', 'pill', r.severity)); if (r.hidden) { tags.append(document.createTextNode(' ')); tags.append(el('span', 'pill', 'hidden')); }
  c.append(tags); tr.append(c);
  tr.append(el('td', 'src', r.source || '—'));
  tr.append(el('td', 'det', r.detail));
  tr.onclick = () => focus(i, r.marks.length ? r.marks[0] : null);
  rowsEl.append(tr); rowEls.push(tr);
});
let filter = 'all';
document.querySelectorAll('.filters button').forEach(b => b.onclick = () => {
  filter = b.dataset.f; document.querySelectorAll('.filters button').forEach(x => x.classList.toggle('on', x === b));
  V.results.forEach((r, i) => {
    const show = filter === 'all' || (filter === 'fail' && !r.passed) || (filter === 'hard' && r.severity === 'hard') || (filter === 'hidden' && r.hidden);
    rowEls[i].style.display = show ? '' : 'none';
  });
});

function focus(i, t) {
  const r = V.results[i];
  rowEls.forEach((x, k) => x.classList.toggle('hot', k === i));
  marks.forEach(({ m, i: k }) => m.classList.toggle('hot', k === i));
  rowEls[i].scrollIntoView({ block: 'nearest' });
  if (t != null) {
    players.forEach(p => p.pause()); $('play').textContent = '▶ Play'; seekAll(t);
    const f = frameAt(t); if (f) $('probe-img').src = f.src;
    $('probe-label').textContent = `Probe frame at ${f ? f.t : t}s · ${r.id}`;
  } else {
    $('probe-label').textContent = `${r.id} · whole video`;
  }
  $('probe-detail').textContent = (r.passed ? 'PASS  ' : 'FAIL  ') + r.detail + (r.params && Object.keys(r.params).length ? '\nparams ' + JSON.stringify(r.params) : '');
}
const firstFail = V.results.findIndex(r => !r.passed && r.marks.length);
if (firstFail >= 0) focus(firstFail, V.results[firstFail].marks[0]);
else if (D.frames.length) { $('probe-img').src = D.frames[Math.floor(D.frames.length / 2)].src; }
</script>
</body></html>
"""

_INDEX = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Defect benchmark · frameproof</title>
<style>""" + _CSS + r"""
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 22px; }
.stat { padding: 16px 18px; }
.stat .n { font-size: 34px; font-weight: 800; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.card { overflow: hidden; display: flex; flex-direction: column; color: inherit; }
.card:hover { border-color: var(--accent); text-decoration: none; }
.card img { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; display: block; background: #000; }
.card .body { padding: 12px 14px; display: grid; gap: 6px; }
.card .top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.card .why { font-size: 12px; color: var(--muted); font-family: var(--mono);
             display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
.wrong { outline: 2px solid var(--fail); }
h2 { font-size: 15px; margin: 28px 0 12px; color: var(--muted); font-weight: 600; }
</style></head>
<body>
<header><span class="brand">frameproof</span><h1>Planted-defect benchmark</h1><span class="muted" id="name"></span></header>
<main>
  <div class="stats">
    <div class="panel stat"><div class="muted">Defects caught</div><div class="n" id="caught"></div><div class="muted" id="caught2"></div></div>
    <div class="panel stat"><div class="muted">False blocks on good videos</div><div class="n" id="fb"></div><div class="muted">should be 0</div></div>
    <div class="panel stat"><div class="muted">How to read</div><div>Each card is one variant of a good composition with one planted defect. The verifier must BLOCK every mutant and ACCEPT the original. Open a card to see the video, the check timeline and the evidence.</div></div>
  </div>
  <div id="groups"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
document.getElementById('name').textContent = D.name;
document.getElementById('caught').textContent = `${D.caught}/${D.mutants}`;
document.getElementById('caught2').textContent = D.mutants ? Math.round(100 * D.caught / D.mutants) + '% of planted defects blocked' : '';
document.getElementById('fb').textContent = `${D.false_blocks}/${D.originals}`;
const byBrief = {};
D.cards.forEach(c => (byBrief[c.brief] = byBrief[c.brief] || []).push(c));
for (const [brief, cards] of Object.entries(byBrief)) {
  document.getElementById('groups').append(el('h2', null, 'Brief: ' + brief));
  const grid = el('div', 'cards');
  cards.forEach(c => {
    const a = el('a', 'panel card' + (c.correct ? '' : ' wrong')); a.href = c.href;
    if (c.thumb) { const im = el('img'); im.src = c.thumb; im.loading = 'lazy'; a.append(im); }
    const b = el('div', 'body');
    const top = el('div', 'top'); top.append(el('b', null, c.variant)); top.append(el('span', 'badge ' + c.decision, c.decision)); b.append(top);
    const meta = el('div'); meta.append(el('span', 'tag', c.category)); meta.append(document.createTextNode(' '));
    meta.append(el('span', 'tag', 'expected ' + c.expected + (c.correct ? ' ✓' : ' ✗'))); b.append(meta);
    b.append(el('div', 'why', (c.reasons && c.reasons[0]) || 'all checks passed'));
    a.append(b); grid.append(a);
  });
  document.getElementById('groups').append(grid);
}
</script>
</body></html>
"""
