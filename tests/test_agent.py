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
