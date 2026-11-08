"""Local Frameproof studio UI and streaming ADK workflow bridge."""
from __future__ import annotations

import base64
import json
import hashlib
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel

from frameproof.config import ROOT, SETTINGS, VENDORED_GSAP
from . import deliver
from .graph import root_agent

app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "apps" / "studio_ui"), name="static")
app.mount("/runs", StaticFiles(directory=SETTINGS.runs_dir), name="runs")
previews: dict[str, dict] = {}
active_runs: set[Path] = set()
session_runners: dict[Path, tuple] = {}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Submission(BaseModel):
    prompt: str
    reference_data: str | None = None
    reference_mime: str | None = None
    reference_mode: str = "inspiration"
    base_token: str | None = None


@app.get("/")
def index():
    return FileResponse(ROOT / "apps" / "studio_ui" / "index.html")


@app.get("/api/runs")
def saved_runs():
    """List saved studio compositions that can be used as a starting point."""
    folder = SETTINGS.runs_dir / "studio"
    runs = []
    for run_dir in sorted(folder.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []:
        if not run_dir.is_dir() or not (run_dir / "brief.json").is_file():
            continue
        try:
            brief = json.loads((run_dir / "brief.json").read_text())
            summary_path = run_dir / "summary.json"
            summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
            attempts = sorted(run_dir.glob("attempt-*/index.html"),
                              key=lambda p: int(p.parent.name.split("-")[-1]))
            final = summary.get("final") or {}
            current = run_dir / "composition" / "index.html"
            html = current.resolve() if current.is_file() else (Path(final["html"]).resolve() if final.get("html") else (attempts[-1].resolve() if attempts else None))
            if not html or not html.is_relative_to(run_dir.resolve()) or not html.is_file():
                continue
            ref_file = run_dir / "reference" / "reference.json"
            reference = json.loads(ref_file.read_text()) if ref_file.is_file() else None
            token = secrets.token_urlsafe(16)
            previews[token] = {"html": html, "brief": brief["text"], "reference": reference, "run_dir": run_dir.resolve(), "digest": _digest(html)}
            runs.append({"label": run_dir.name, "status": summary.get("status", "IN PROGRESS"),
                         "url": f"/preview/{token}/index.html", "token": token,
                         "report": f"/runs/studio/{run_dir.name}/index.html"})
            if len(runs) >= 50:
                break
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return runs


def _run_activity(run_dir: Path, seen: set[Path] | None = None, *, include_parent: bool = True) -> list[dict]:
    """Read only this run and its recorded revision ancestors, oldest first."""
    seen = seen if seen is not None else set()
    root = (SETTINGS.runs_dir / "studio").resolve()
    run_dir = run_dir.resolve()
    if not run_dir.is_relative_to(root) or run_dir in seen or len(seen) >= 100:
        return []
    seen.add(run_dir)
    result = []
    parent_file = run_dir / "activity-parent.json"
    if include_parent and parent_file.is_file():
        try:
            parent_name = json.loads(parent_file.read_text())["run"]
            if isinstance(parent_name, str) and Path(parent_name).name == parent_name:
                result.extend(_run_activity(root / parent_name, seen))
        except (ValueError, OSError, KeyError, TypeError):
            pass
    activity_file = run_dir / "activity.jsonl"
    if activity_file.is_file():
        for raw in activity_file.read_text().splitlines():
            try:
                entry = json.loads(raw)
                if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                    result.append({"text": entry["text"], "error": bool(entry.get("error"))})
            except ValueError:
                continue
    else:
        # Earlier runs did not record the live stream. Show only facts saved with that run.
        result.append({"text": "Earlier live activity was not recorded; showing saved run details.", "error": False})
        try:
            brief = json.loads((run_dir / "brief.json").read_text())
            result.append({"text": "Brief: " + brief.get("text", ""), "error": False})
            summary_path = run_dir / "summary.json"
            if summary_path.is_file():
                summary = json.loads(summary_path.read_text())
                final = summary.get("final") or {}
                if final.get("proposer"):
                    result.append({"text": "Worker: " + str(final["proposer"]), "error": False})
                if final.get("decision"):
                    result.append({"text": "Gate: " + str(final["decision"]), "error": False})
                if summary.get("status"):
                    result.append({"text": "Status: " + str(summary["status"]), "error": False})
        except (ValueError, OSError, TypeError):
            pass
    return result


@app.get("/api/activity/{token}")
def activity(token: str):
    record = previews.get(token)
    if not record:
        raise HTTPException(404, "The selected run is no longer available")
    return _run_activity(record["run_dir"])


def _record_activity(run_dir: Path | None, text: str, error: bool = False) -> None:
    if run_dir:
        with (run_dir / "activity.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "error": error}, ensure_ascii=False) + "\n")


def event_line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False) + "\n"


@app.post("/api/run")
async def run(request: Submission):
    if not request.prompt.strip():
        raise HTTPException(400, "Enter a brief")
    if request.reference_mode not in ("inspiration", "asset"):
        raise HTTPException(400, "Invalid reference mode")
    previous = previews.get(request.base_token) if request.base_token else None
    if request.base_token and not previous:
        raise HTTPException(404, "The previous preview is no longer available. Start a new project.")
    if previous:
        current = previous["html"]
        if not current.is_file() or _digest(current) != previous.get("digest"):
            raise HTTPException(409, "This preview has changed. Reopen the current session before editing.")
        saved_brief = previous["run_dir"] / "brief.json"
        if saved_brief.exists() and json.loads(saved_brief.read_text()).get("text") != previous["brief"]:
            raise HTTPException(409, "This session has changed. Reopen it before editing.")
        if previous["run_dir"] in active_runs:
            raise HTTPException(409, "This session already has a revision in progress.")
    payload = {"text": request.prompt, "reference_mode": request.reference_mode}
    if previous:
        payload["revision"] = {"base_html": str(previous["html"]), "brief": previous["brief"],
                               "reference": previous.get("reference"), "run_dir": str(previous["run_dir"])}
    parts = [types.Part(text=json.dumps(payload))] if previous else [types.Part(text=request.prompt)]
    if request.reference_data:
        try:
            raw = base64.b64decode(request.reference_data, validate=True)
        except ValueError:
            raise HTTPException(400, "Invalid reference encoding") from None
        if len(raw) > 50 * 1024 * 1024:
            raise HTTPException(400, "Reference exceeds 50 MB")
        parts = [types.Part(text=json.dumps(payload)),
                 types.Part.from_bytes(data=raw, mime_type=request.reference_mime or "")]

    locked_dir = previous["run_dir"] if previous else None
    if locked_dir:
        active_runs.add(locked_dir)

    async def stream():
        nonlocal locked_dir
        existing_session = session_runners.get(locked_dir) if locked_dir else None
        if existing_session:
            runner, user, session_id = existing_session
        else:
            runner = InMemoryRunner(node=root_agent, app_name="studio_ui")
            user = secrets.token_urlsafe(12)
            session = await runner.session_service.create_session(app_name="studio_ui", user_id=user)
            session_id = session.id
        message = types.Content(role="user", parts=parts)
        events = []
        answered = set()
        current_brief = ""
        current_reference = None
        current_run_dir = None
        try:
            while True:
                async for event in runner.run_async(user_id=user, session_id=session_id, new_message=message):
                    events.append(event)
                    delta = event.actions.state_delta if event.actions else {}
                    if delta.get("brief"):
                        current_brief = delta["brief"]["text"]
                    if "reference" in delta:
                        current_reference = delta["reference"]
                    if delta.get("run_dir"):
                        current_run_dir = Path(delta["run_dir"]).resolve()
                        if not locked_dir:
                            locked_dir = current_run_dir
                            active_runs.add(locked_dir)
                        session_runners[current_run_dir] = (runner, user, session_id)
                        # Seed pre-logging sessions with their saved facts before appending
                        # the first new activity; their original live stream is unavailable.
                        if previous and not (current_run_dir / "activity.jsonl").exists():
                            for entry in _run_activity(current_run_dir, include_parent=False):
                                _record_activity(current_run_dir, entry["text"], entry["error"])
                        _record_activity(current_run_dir, ("Change requested: " if previous else "Brief: ") + request.prompt)
                    if delta.get("html"):
                        token = secrets.token_urlsafe(16)
                        previews[token] = {"html": Path(delta["html"]).resolve(),
                                           "brief": current_brief, "reference": current_reference, "run_dir": current_run_dir,
                                           "digest": _digest(Path(delta["html"]))}
                        yield event_line({"type": "preview", "url": f"/preview/{token}/index.html", "token": token})
                    if delta.get("run_dir"):
                        rel = Path(delta["run_dir"]).resolve().relative_to(SETTINGS.runs_dir.resolve())
                        yield event_line({"type": "run", "url": f"/runs/{rel.as_posix()}/index.html"})
                    for part in event.content.parts if event.content and event.content.parts else []:
                        if part.text:
                            _record_activity(current_run_dir, part.text)
                            yield event_line({"type": "message", "text": part.text})
                    if event.actions and event.actions.route:
                        yield event_line({"type": "route", "route": event.actions.route})
                calls = [call for call in deliver.pending_calls(events) if call.id not in answered]
                if not calls:
                    break
                call = calls[0]
                message = deliver.wait_and_answer(call)
                answered.add(call.id)
            yield event_line({"type": "done"})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _record_activity(current_run_dir, error, error=True)
            yield event_line({"type": "error", "text": error})
        finally:
            if locked_dir:
                active_runs.discard(locked_dir)
    return StreamingResponse(stream(), media_type="application/x-ndjson")


PLAYER = r"""<script>
window.addEventListener('load', () => {
 const root = document.querySelector('[data-composition-id]'); if (!root) return;
 const w = Number(root.dataset.width) || root.offsetWidth || 1920;
 const h = Number(root.dataset.height) || root.offsetHeight || 1080;
 const duration = Number(root.dataset.duration) || 5;
 document.documentElement.style.cssText = 'margin:0;width:100%;height:100%;overflow:hidden;background:#090b10';
 document.body.style.cssText = 'margin:0;width:100%;height:100%;overflow:hidden';
 root.style.transformOrigin = 'top left'; root.style.position = 'absolute';
 function size() { const s = Math.min(innerWidth/w, innerHeight/h); root.style.transform = `scale(${s})`; root.style.left = `${(innerWidth-w*s)/2}px`; root.style.top = `${(innerHeight-h*s)/2}px`; }
 addEventListener('resize', size); size();
 const clips = [...root.querySelectorAll('[data-start]')];
 const byId = Object.fromEntries(clips.filter(e => e.id).map(e => [e.id,e]));
 function start(e, seen = new Set()) { if (seen.has(e)) return 0; seen.add(e); const raw=e.dataset.start||'0'; const n=Number(raw); if (Number.isFinite(n)) return n; const m=raw.match(/^([\w-]+)\s*(?:([+-])\s*([\d.]+))?$/); const ref=m && byId[m[1]]; return ref ? start(ref,seen)+(Number(ref.dataset.duration)||0)+(m[2] ? (m[2]==='-'?-1:1)*Number(m[3]):0) : 0; }
 let origin=performance.now();
 function tick(now) { const t=((now-origin)/1000)%duration; clips.forEach(e => { if(e.tagName==='AUDIO') return; const s=start(e), d=e.dataset.duration===undefined ? Infinity : Number(e.dataset.duration); e.style.visibility=t>=s && t<s+d ? '' : 'hidden'; }); Object.values(window.__timelines||{}).forEach(tl => { try { tl.pause(); tl.seek(t,false); } catch(e) {} }); requestAnimationFrame(tick); }
 requestAnimationFrame(tick);
});
</script>"""


@app.get("/vendor/gsap.min.js")
def gsap():
    if not VENDORED_GSAP.exists():
        raise HTTPException(404, "Install npm dependencies to preview GSAP")
    return FileResponse(VENDORED_GSAP, media_type="application/javascript")


@app.get("/preview/{token}/{asset:path}")
def preview(token: str, asset: str):
    record = previews.get(token)
    if not record:
        raise HTTPException(404)
    path = record["html"]
    target = (path.parent / asset).resolve()
    if not target.is_relative_to(path.parent) or not target.is_file():
        raise HTTPException(404)
    if target == path:
        import re
        html = target.read_text()
        html = re.sub(r'https://cdn\.jsdelivr\.net/npm/gsap@[^/]+/dist/gsap\.min\.js',
                      '/vendor/gsap.min.js', html)
        html = html.replace("</body>", PLAYER + "</body>") if "</body>" in html else html + PLAYER
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})
    return FileResponse(target)


if __name__ == "__main__":
    import uvicorn
    os.environ["FP_STUDIO_HTML_ONLY"] = "1"
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FP_STUDIO_PORT", "8090")))
