"""Studio graph: unit tests for the pieces, plus one end-to-end replay run (browser)."""

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("google.adk")

from google.genai import types  # noqa: E402

from agent import deliver, graph  # noqa: E402
from agent.schemas import DeskAnswer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "tidepool-launch"


def test_parse_input_forms(tmp_path):
    b, ex = graph._parse_input("examples/tidepool-launch")
    assert b.id == "tidepool-launch" and ex == EXAMPLE
    b, ex = graph._parse_input('{"id": "x", "text": "A 4 s clip, palette #112233"}')
    assert (b.id, ex, b.palette) == ("x", None, ["#112233"])
    f = tmp_path / "briefs.json"
    f.write_text(json.dumps([{"id": "a", "text": "one"}, {"id": "b", "text": "two"}]))
    assert graph._parse_input(f"{f}#b")[0].text == "two"
    with pytest.raises(ValueError):
        graph._parse_input(str(f))  # two briefs, no id
    assert graph._parse_input("just some brief text")[1] is None


def test_answer_parsing_ignores_nulls():
    assert graph._answer({"approved": True, "ok": None, "note": None}) == DeskAnswer(approved=True)
    assert graph._answer("reject").approved is False
    assert graph._answer("approve").approved is True


def test_replay_sequence(monkeypatch):
    monkeypatch.setenv("FP_REPLAY", "typo,original")
    html1, n1 = graph._replay_html(EXAMPLE, 0)
    html2, n2 = graph._replay_html(EXAMPLE, 1)
    _, n3 = graph._replay_html(EXAMPLE, 5)  # past the end: keep the last
    assert (n1, n2, n3) == ("typo", "original", "original")
    assert html1 != html2 and html2 == (EXAMPLE / "index.html").read_text()
    monkeypatch.setenv("FP_REPLAY", "nope")
    with pytest.raises(ValueError):
        graph._replay_html(EXAMPLE, 0)


def test_pending_calls_skips_answered():
    class E:  # minimal event stand-in
        def __init__(self, part):
            self.content = types.Content(role="model", parts=[part])

    call = types.Part(function_call=types.FunctionCall(id="desk-1", name=deliver.REQUEST_INPUT, args={"payload": {}}))
    other = types.Part(function_call=types.FunctionCall(id="t-1", name="some_tool", args={}))
    resp = types.Part(function_response=types.FunctionResponse(id="desk-1", name=deliver.REQUEST_INPUT, response={}))
    assert [c.id for c in deliver.pending_calls([E(call), E(other)])] == ["desk-1"]
    assert deliver.pending_calls([E(call), E(resp)]) == []


def test_graph_shape():
    names = {getattr(n, "name", "") for e in graph.root_agent.graph.edges for n in (e.from_node, e.to_node)}
    assert {"intake", "reader_1", "reader_2", "reader_3", "join_readers", "panel", "proposer", "gate",
            "desk", "deliver", "give_up"} <= names


@pytest.mark.browser
def test_studio_end_to_end_replay(monkeypatch, tmp_path):
    """typo is blocked, its public feedback goes back, original is accepted and delivered."""
    monkeypatch.setenv("FP_REPLAY", "typo,original")
    monkeypatch.setenv("FP_PROPOSER", "replay")
    monkeypatch.setattr(graph, "_writer_models", lambda: [])  # force fixture checks
    monkeypatch.setattr(graph, "_voter_models", lambda: [])
    from agent.run import run

    events = asyncio.run(run(str(EXAMPLE)))
    texts = [p.text for e in events for p in (e.content.parts if e.content and e.content.parts else []) if p.text]
    assert any("Attempt 1 by replay:typo" in t for t in texts)
    assert any(t.startswith("Gate: BLOCK") and "REVISE" in t for t in texts)
    assert any("Attempt 2 by replay:original, after" in t for t in texts)
    delivered = [t for t in texts if t.startswith("DELIVERED")]
    assert delivered, texts[-3:]
    assert "Saved as artifact tidepool-launch.mp4" in delivered[0]
    rel = delivered[0].split("Run page: http://localhost:4600/")[1].split()[0].rstrip("/")
    run_dir = graph.SETTINGS.runs_dir / rel
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "DELIVERED" and summary["attempts"] == 2
    seen = json.loads((run_dir / "attempt-2" / "proposer.json").read_text())["feedback_seen"]
    checks = {c["id"]: c for c in json.loads((run_dir / "checks.json").read_text())}
    # the proposer only ever saw public checks
    assert seen and all(not checks[f.split(":")[0]]["hidden"] for f in seen)
    assert (run_dir / "index.html").exists() and (run_dir / "final.mp4").exists()


def test_intake_fails_early_without_a_proposer(monkeypatch):
    monkeypatch.setattr(graph, "_implementer_ready", lambda: False)
    with pytest.raises(RuntimeError, match="free-text brief.*examples/tidepool-launch"):
        list(graph.intake("video a minion dancing"))
    events = list(graph.intake("examples/tidepool-launch"))  # an example replays without a key
    assert events[0].actions.state_delta["example_dir"].endswith("tidepool-launch")


def _panel(node_input, brief_text, run_dir, example_dir=""):
    async def go():
        return [e async for e in graph.panel(node_input, {"id": "b", "text": brief_text, "duration": None},
                                             str(run_dir), example_dir)]
    return asyncio.run(go())[0]


def test_panel_falls_back_to_generic_checks(tmp_path):
    skipped = {f"reader_{i}": {"slot": i, "skipped": True} for i in (1, 2, 3)}
    ev = _panel(skipped, "video a minion dancing", tmp_path)
    checks = ev.actions.state_delta["checks"]
    kinds = {c["kind"] for c in checks}
    assert {"renders", "in_frame", "no_overflow", "no_unexpected_text", "motion"} <= kinds
    assert not any(c["hidden"] for c in checks)
    assert json.loads((tmp_path / "panel.json").read_text())["mode"] == "generic"
    assert "No checker model is configured" in ev.content.parts[0].text


def test_panel_explains_an_empty_snap_reader(tmp_path):
    snap = {"slot": 0, "model": "subq:subq-snap-1.1", "how": "snap", "checks": [], "votes": {"candidates": []}}
    ev = _panel({"reader_1": snap}, "video a minion dancing", tmp_path)
    assert "had nothing to vote on" in ev.content.parts[0].text


def test_reference_message_and_proposer(monkeypatch, tmp_path):
    import io
    from PIL import Image
    from frameproof import generate as gen
    from frameproof.llm import Reply

    buf = io.BytesIO()
    Image.new("RGB", (32, 20), "red").save(buf, format="JPEG")
    content = types.Content(role="user", parts=[
        types.Part(text=json.dumps({"text": "Animate this image", "reference_mode": "asset"})),
        types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
    ])
    monkeypatch.setattr(graph, "_implementer_ready", lambda: True)
    from dataclasses import replace
    monkeypatch.setattr(graph, "SETTINGS", replace(graph.SETTINGS, runs_dir=tmp_path))
    ev = list(graph.intake(content))[0]
    ref = ev.actions.state_delta["reference"]
    assert ref["mode"] == "asset" and Path(ref["frames"][0]).is_file()
    calls = []
    monkeypatch.setattr(gen.llm, "complete", lambda *a, **kw: calls.append((a, kw)) or Reply("<html></html>", "fake"))
    out = tmp_path / "attempt"
    gen.generate(graph._brief(ev.actions.state_delta["brief"]), out, model="fake:model", reference=ref)
    assert (out / "reference.png").read_bytes() == Path(ref["asset"]).read_bytes()
    assert calls[0][1]["images"][0].startswith(b"\x89PNG")
    assert 'reference.png' in calls[0][0][2]


def test_video_reference_sampling(tmp_path):
    import subprocess
    from agent.reference import prepare

    video = tmp_path / "sample.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x48:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    ref = prepare(video.read_bytes(), "video/mp4", tmp_path / "run")
    assert len(ref["frames"]) == 4 and all(Path(p).is_file() for p in ref["frames"])
    assert ref["times"] == sorted(ref["times"])
    with pytest.raises(ValueError, match="not supported"):
        prepare(video.read_bytes(), "video/mp4", tmp_path / "other", "asset")


def test_adk_workflow_receives_inline_attachment(monkeypatch, tmp_path):
    import io
    from dataclasses import replace
    from PIL import Image
    from google.adk.runners import InMemoryRunner

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buf, format="PNG")
    monkeypatch.setattr(graph, "_implementer_ready", lambda: True)
    monkeypatch.setattr(graph, "SETTINGS", replace(graph.SETTINGS, runs_dir=tmp_path))

    async def go():
        runner = InMemoryRunner(node=graph.root_agent, app_name="ref_test")
        session = await runner.session_service.create_session(app_name="ref_test", user_id="test")
        message = types.Content(role="user", parts=[types.Part(text="Animate this image"),
                    types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")])
        async for event in runner.run_async(user_id="test", session_id=session.id, new_message=message):
            ref = event.actions.state_delta.get("reference") if event.actions else None
            if ref:
                return ref
        return None

    ref = asyncio.run(go())
    assert ref and ref["kind"] == "image" and Path(ref["frames"][0]).is_file()


def test_html_desk_delivers_composition_without_render(monkeypatch, tmp_path):
    from agent import desk
    monkeypatch.setenv("FP_STUDIO_HTML_ONLY", "1")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "index.html").write_text("<html></html>")
    receipt = desk.start(tmp_path, attempt, "job")
    assert receipt.kind == "html" and receipt.video.endswith("index.html")
    assert desk.status(receipt) == "done"
    assert not (tmp_path / "final.mp4").exists()


def test_revision_uses_previous_html_and_carries_brief(monkeypatch, tmp_path):
    from dataclasses import replace
    from frameproof import generate as gen
    from frameproof.llm import Reply
    from google.genai import types

    monkeypatch.setattr(graph, "SETTINGS", replace(graph.SETTINGS, runs_dir=tmp_path))
    monkeypatch.setattr(graph, "_implementer_ready", lambda: True)
    old = tmp_path / "studio" / "old" / "attempt-1"
    old.mkdir(parents=True)
    (old / "index.html").write_text('<html><div>Original title</div></html>')
    (old / "reference.png").write_bytes(b"original image")
    (old.parent / "brief.json").write_text(json.dumps({"id": "old", "text": "A video with Original title"}))
    request = {"text": "Make the title blue", "revision": {
        "base_html": str(old / "index.html"), "brief": "A video with Original title", "run_dir": str(old.parent)}}
    event = list(graph.intake(types.Content(role="user", parts=[types.Part(text=json.dumps(request))])))[0]
    state = event.actions.state_delta
    assert state["base_html"] == str(old / "index.html")
    assert state["run_dir"] == str(old.parent) and state["round_start_attempt"] == 1
    assert "A video with Original title" in state["brief"]["text"]
    assert "Make the title blue" in state["brief"]["text"]
    captured = []
    monkeypatch.setattr(gen.llm, "complete", lambda *a, **kw: captured.append(a[2]) or Reply("<html>updated</html>", "fake"))
    out = tmp_path / "updated"
    gen.generate(graph._brief(state["brief"]), out, model="fake:model", base_html=old / "index.html")
    assert "<existing_html>" in captured[0] and "Original title" in captured[0]
    assert "Make the title blue" in captured[0]
    assert (out / "reference.png").read_bytes() == b"original image"
    assert (old / "index.html").read_text() == '<html><div>Original title</div></html>'


def test_revision_rejects_unknown_base(monkeypatch, tmp_path):
    from dataclasses import replace
    monkeypatch.setattr(graph, "SETTINGS", replace(graph.SETTINGS, runs_dir=tmp_path / "runs"))
    outside = tmp_path / "index.html"
    outside.write_text("outside")
    with pytest.raises(ValueError, match="existing studio composition"):
        list(graph.intake(json.dumps({"text": "change it", "revision": {"base_html": str(outside), "brief": "original"}})))


def test_retry_uses_last_attempt_as_source(monkeypatch, tmp_path):
    monkeypatch.setattr(graph, "_implementer_ready", lambda: True)
    monkeypatch.delenv("FP_PROPOSER", raising=False)
    prior = tmp_path / "attempt-1"
    prior.mkdir()
    (prior / "index.html").write_text("previous attempt")
    seen = []
    def fake_generate(brief, out_dir, **kwargs):
        seen.append(kwargs)
        (out_dir / "index.html").write_text("updated")
    monkeypatch.setattr(graph, "generate", fake_generate)
    async def run():
        return [e async for e in graph.proposer({"id": "b", "text": "brief"}, 1, str(tmp_path), ["fix it"])]
    asyncio.run(run())
    assert seen[0]["base_html"] == prior / "index.html"
    assert seen[0]["feedback"] == ["fix it"]


def test_saved_runs_recover_revision_source(monkeypatch, tmp_path):
    from dataclasses import replace
    from agent import web
    monkeypatch.setattr(web, "SETTINGS", replace(web.SETTINGS, runs_dir=tmp_path))
    run = tmp_path / "studio" / "saved"
    attempt = run / "attempt-1"
    attempt.mkdir(parents=True)
    html = attempt / "index.html"
    html.write_text("<html>saved</html>")
    (run / "brief.json").write_text(json.dumps({"id": "b", "text": "Keep this title"}))
    (run / "summary.json").write_text(json.dumps({"status": "DELIVERED", "final": {"html": str(html)}}))
    results = web.saved_runs()
    assert len(results) == 1 and results[0]["status"] == "DELIVERED"
    saved = web.previews[results[0]["token"]]
    assert saved["html"] == html and saved["brief"] == "Keep this title"


def test_activity_tracks_selected_run_and_revision_ancestry(monkeypatch, tmp_path):
    from dataclasses import replace
    from agent import web
    monkeypatch.setattr(web, "SETTINGS", replace(web.SETTINGS, runs_dir=tmp_path))
    root = tmp_path / "studio"
    first, revision, other = (root / name for name in ("first", "revision", "other"))
    for path in (first, revision, other):
        path.mkdir(parents=True)
    web._record_activity(first, "Brief: original")
    web._record_activity(first, "original accepted")
    (revision / "activity-parent.json").write_text(json.dumps({"run": "first"}))
    web._record_activity(revision, "Change requested: blue")
    web._record_activity(revision, "revision failed", error=True)
    web._record_activity(other, "unrelated activity")
    web.previews["selected-test"] = {"run_dir": revision}
    activity = web.activity("selected-test")
    assert [entry["text"] for entry in activity] == ["Brief: original", "original accepted",
                                                      "Change requested: blue", "revision failed"]
    assert activity[-1]["error"] is True
    assert "unrelated activity" not in str(activity)
    (first / "activity-parent.json").write_text(json.dumps({"run": "revision"}))
    assert len(web.activity("selected-test")) == 4  # cycles cannot repeat or recurse forever


def test_legacy_activity_only_reports_saved_facts(monkeypatch, tmp_path):
    from dataclasses import replace
    from agent import web
    monkeypatch.setattr(web, "SETTINGS", replace(web.SETTINGS, runs_dir=tmp_path))
    run = tmp_path / "studio" / "legacy"
    run.mkdir(parents=True)
    (run / "brief.json").write_text(json.dumps({"text": "legacy brief"}))
    (run / "summary.json").write_text(json.dumps({"status": "DELIVERED", "final": {"proposer": "legacy-worker", "decision": "ACCEPT"}}))
    entries = web._run_activity(run)
    assert "not recorded" in entries[0]["text"]
    assert [e["text"] for e in entries[1:]] == ["Brief: legacy brief", "Worker: legacy-worker", "Gate: ACCEPT", "Status: DELIVERED"]


def test_revision_publishes_same_composition_and_keeps_attempts(monkeypatch, tmp_path):
    from dataclasses import replace
    monkeypatch.setattr(graph, "SETTINGS", replace(graph.SETTINGS, runs_dir=tmp_path))
    monkeypatch.setattr(graph, "_implementer_ready", lambda: True)
    monkeypatch.delenv("FP_PROPOSER", raising=False)
    run = tmp_path / "studio" / "session"
    attempt = run / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "index.html").write_text("original html")
    (run / "brief.json").write_text(json.dumps({"id": "first", "text": "original brief"}))
    seen = []
    def fake_generate(brief, out_dir, **kwargs):
        seen.append(Path(kwargs["base_html"]).read_text())
        (out_dir / "index.html").write_text(seen[-1] + " updated")
    monkeypatch.setattr(graph, "generate", fake_generate)
    request = {"text": "update", "revision": {"base_html": str(attempt / "index.html"),
               "brief": "original brief", "run_dir": str(run)}}
    state = list(graph.intake(json.dumps(request)))[0].actions.state_delta
    async def generate():
        return [e async for e in graph.proposer(state["brief"], state["attempt"], str(run), [],
                    base_html=state["base_html"], round_start_attempt=state["round_start_attempt"])]
    output = asyncio.run(generate())[0].actions.state_delta
    assert output["html"] == str(run / "composition" / "index.html")
    assert Path(output["html"]).read_text() == "original html updated"
    assert (run / "attempt-1" / "index.html").read_text() == "original html"
    assert (run / "attempt-2" / "index.html").read_text() == "original html updated"
    assert (json.loads((run / "brief.json").read_text()))["text"].endswith("update")


def test_session_rejects_stale_or_concurrent_revision(monkeypatch, tmp_path):
    from dataclasses import replace
    from fastapi import HTTPException
    from agent import web
    monkeypatch.setattr(web, "SETTINGS", replace(web.SETTINGS, runs_dir=tmp_path))
    run = tmp_path / "studio" / "current"
    run.mkdir(parents=True)
    html = run / "index.html"
    html.write_text("first")
    (run / "brief.json").write_text(json.dumps({"text": "first brief"}))
    web.previews["revision-lock-test"] = {"html": html, "run_dir": run, "brief": "first brief", "digest": web._digest(html)}
    request = web.Submission(prompt="change", base_token="revision-lock-test")
    web.active_runs.add(run)
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(web.run(request))
        assert exc.value.status_code == 409
    finally:
        web.active_runs.discard(run)
    html.write_text("changed in another tab")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(web.run(request))
    assert exc.value.status_code == 409
