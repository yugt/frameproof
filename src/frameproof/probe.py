"""Open a HyperFrames composition in headless Chromium, seek it to given times,
and record what a viewer would see: visible text with boxes and colours, plus a
screenshot of the frame.

Seeking follows the HyperFrames contract (paused GSAP timelines registered on
window.__timelines, clips timed by data-start / data-duration). The probe
emulates clip visibility itself, so it does not need the HyperFrames runtime;
`render_gate()` runs the real CLI (lint, and optionally a render) when it is
installed, as the second opinion on "does it render".

Known gaps: nested compositions loaded via data-composition-src are not
expanded; media clips (<video>) are not seeked.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .checks import Frame, Observation, TextBox
from .config import SETTINGS, VENDORED_GSAP

_SETUP_JS = r"""
() => {
  const style = document.createElement('style');
  style.textContent = '[data-fp-hidden]{visibility:hidden !important}';
  document.head.appendChild(style);

  const root = document.querySelector('[data-composition-id]');
  const byId = {};
  root.querySelectorAll('[data-start]').forEach(el => { if (el.id) byId[el.id] = el; });
  const memo = new Map();
  function start(el) {
    if (memo.has(el)) return memo.get(el);
    memo.set(el, 0);  // cycle guard
    const raw = (el.dataset.start || '0').trim();
    let s = parseFloat(raw);
    if (isNaN(s)) {
      const m = raw.match(/^([\w-]+)\s*(?:([+-])\s*([\d.]+))?$/);
      const ref = m && byId[m[1]];
      s = ref ? start(ref) + parseFloat(ref.dataset.duration || '0') : 0;
      if (m && m[2]) s += (m[2] === '-' ? -1 : 1) * parseFloat(m[3]);
    }
    memo.set(el, s);
    return s;
  }
  window.__fp = {
    root,
    seek(t) {
      root.querySelectorAll('[data-start]').forEach(el => {
        if (el === root || el.tagName === 'AUDIO') return;
        const s = start(el);
        const d = el.dataset.duration !== undefined ? parseFloat(el.dataset.duration) : Infinity;
        if (t >= s && t < s + d) el.removeAttribute('data-fp-hidden');
        else el.setAttribute('data-fp-hidden', '');
      });
      const tls = window.__timelines || {};
      for (const [key, tl] of Object.entries(tls)) {
        const host = document.querySelector(`[data-composition-id="${key}"]`);
        const off = host && host !== root ? start(host) : 0;
        try { tl.seek(Math.max(0, t - off), false); } catch (e) {}
      }
    },
  };
  return {
    id: root.dataset.compositionId,
    width: parseFloat(root.dataset.width || root.offsetWidth),
    height: parseFloat(root.dataset.height || root.offsetHeight),
    duration: root.dataset.duration !== undefined ? parseFloat(root.dataset.duration) : null,
    noTimeline: root.hasAttribute('data-no-timeline'),
  };
}
"""

_TEXT_JS = r"""
() => {
  const root = window.__fp.root;
  const rr = root.getBoundingClientRect();
  const hex = c => {
    const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/);
    if (!m) return '#000000';
    return '#' + [1, 2, 3].map(i => Math.round(+m[i]).toString(16).padStart(2, '0')).join('').toUpperCase();
  };
  const out = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  for (let el = walker.currentNode; el; el = walker.nextNode()) {
    const own = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim());
    if (!own.length) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    let op = 1;
    for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.display === 'none') { op = 0; break; }
      op *= parseFloat(s.opacity);
    }
    // Text colour is often transparent with a gradient fill; skip invisible ink.
    const alpha = (cs.color.match(/rgba\([^)]*,\s*([\d.]+)\)/) || [0, 1])[1];
    if (+alpha < 0.1 && !cs.backgroundClip.includes('text')) continue;
    const range = document.createRange();
    range.setStartBefore(own[0]); range.setEndAfter(own[own.length - 1]);
    const r = range.getBoundingClientRect();
    const block = cs.display !== 'inline';
    const overflow = block && (cs.overflow !== 'visible' || cs.textOverflow === 'ellipsis')
      ? (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2)
      : (block && el.clientWidth > 0 && r.width > el.getBoundingClientRect().width + 4);
    out.push({
      text: own.map(n => n.textContent).join(' ').replace(/\s+/g, ' ').trim(),
      x: r.left - rr.left, y: r.top - rr.top, w: r.width, h: r.height,
      color: hex(cs.color), opacity: op, font_px: parseFloat(cs.fontSize), overflow,
    });
  }
  return out;
}
"""


def sample_times(duration: float, step: float, extra: list[float] = ()) -> list[float]:
    end = max(0.0, duration - 1 / 30)
    n = int(end / step)
    ts = {round(i * step, 3) for i in range(n + 1)} | {round(end, 3)}
    ts |= {round(min(max(0.0, t), end), 3) for t in extra}
    return sorted(ts)


def probe(html_path: str | Path, times: list[float] | None = None, step: float | None = None,
          out_dir: Path | None = None) -> Observation:
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    step = step or SETTINGS.sample_step
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        _route(page)
        page.goto(html_path.as_uri(), wait_until="load")
        meta = page.evaluate(_SETUP_JS)
        W, H = int(meta["width"]), int(meta["height"])
        if (W, H) != (1920, 1080):
            page.set_viewport_size({"width": W, "height": H})
        timeline_ok = meta["noTimeline"]
        if not timeline_ok:
            try:
                page.wait_for_function(
                    "id => !!(window.__timelines && window.__timelines[id])", arg=meta["id"],
                    timeout=SETTINGS.timeline_wait_s * 1000)
                timeline_ok = True
            except Exception:
                timeline_ok = False
        page.evaluate("document.fonts ? document.fonts.ready : null")
        duration = meta["duration"]
        if times is None:
            times = sample_times(duration or 5.0, step)
        frames: dict[float, Frame] = {}
        root = page.locator("[data-composition-id]").first
        for t in times:
            page.evaluate("t => window.__fp.seek(t)", t)
            texts = [TextBox(**d) for d in page.evaluate(_TEXT_JS)]
            png = root.screenshot(animations="disabled")
            frames[t] = Frame(t=t, texts=texts, png=png)
            if out_dir:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"t{t:06.2f}.png").write_bytes(png)
        browser.close()
    return Observation(width=W, height=H, duration=duration, timeline_ok=timeline_ok,
                       errors=errors, frames=frames)


def _route(page) -> None:
    """Serve GSAP from node_modules when present; optionally block other network."""
    def handler(route):
        url = route.request.url
        if VENDORED_GSAP.exists() and "gsap" in url and url.endswith(".js") and "gsap.min.js" in url:
            return route.fulfill(path=str(VENDORED_GSAP), content_type="application/javascript")
        if SETTINGS.offline and not url.startswith("file://"):
            return route.abort()
        return route.continue_()

    page.route("**/*", handler)


def render_gate(project_dir: str | Path, render: bool = False) -> dict:
    """Run the real HyperFrames CLI if it is installed. Never raises."""
    project_dir = Path(project_dir)
    npx = shutil.which("npx")
    if not npx:
        return {"status": "skipped", "reason": "npx not on PATH"}
    result: dict = {}
    cmds = [("lint", [npx, "--no-install", "hyperframes", "lint", "--json"])]
    if render:
        cmds.append(("render", [npx, "--no-install", "hyperframes", "render", "--quality", "draft",
                                "--output", "renders/verify.mp4"]))
    for name, cmd in cmds:
        try:
            p = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, timeout=600)
        except Exception as e:
            result[name] = {"ok": False, "error": str(e)}
            continue
        entry = {"ok": p.returncode == 0, "stdout": p.stdout[-4000:], "stderr": p.stderr[-2000:]}
        try:
            entry["json"] = json.loads(p.stdout)
        except Exception:
            pass
        result[name] = entry
    result["status"] = "ok" if all(v.get("ok") for v in result.values() if isinstance(v, dict)) else "failed"
    return result
