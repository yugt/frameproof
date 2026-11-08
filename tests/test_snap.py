"""Decision-model (SubQ Snap) wiring: request shape, judge, and check voting. No network."""

import base64
import io
import json

import pytest
from PIL import Image

from frameproof import compile as compile_mod
from frameproof import judge as judge_mod
from frameproof import llm
from frameproof.brief import Brief
from frameproof.checks import Check, Frame, Observation

SNAP = "subq:subq-snap-1.1"


def png(rgb=(11, 31, 58), size=(1920, 1080)):
    b = io.BytesIO()
    Image.new("RGB", size, rgb).save(b, "PNG")
    return b.getvalue()


def C(cid, kind, **params):
    return Check(cid, kind, params, "hard", "src", origin=["meta:l"])


def test_is_decision():
    assert llm.is_decision(SNAP) and llm.is_decision("subq:subq-snap-latest")
    assert not llm.is_decision("subq:subq-preview") and not llm.is_decision("google:gemini-x")
    assert not llm.is_decision("")
    with pytest.raises(RuntimeError, match="decision model"):
        llm.complete(SNAP, "s", "p")


def test_decide_request_shape(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"answers": {"q": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 5}}).encode()

    def fake_urlopen(req, timeout, context):
        seen["url"], seen["body"], seen["auth"] = req.full_url, json.loads(req.data), req.get_header("Authorization")
        assert context is not None
        return Resp()

    monkeypatch.setenv("SUBQ_API_KEY", "k")
    monkeypatch.delenv("FP_SUBQ_BASE_URL", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = llm.decide(SNAP, "state", {"q": {"type": "noul", "instructions": "?"}}, images=[png()])
    assert r["answers"]["q"]["noul"] == 0.9
    assert seen["url"] == "https://api.subq.ai/v1/systemone" and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "subq-snap-1.1" and seen["body"]["state"] == "state"
    img = seen["body"]["images"][0]
    assert img["mediaType"] == "image/jpeg" and not img["base64"].startswith("data:")
    assert Image.open(io.BytesIO(base64.b64decode(img["base64"]))).width == 960  # downscaled
    with pytest.raises(ValueError):
        llm.decide(SNAP, "s", {"q": {}}, images=[png()] * 5)


def test_pick_frames_spreads_over_clip():
    frames = {t / 4: Frame(t / 4, [], b"") for t in range(25)}  # 0..6 s every 0.25
    o = Observation(1920, 1080, 6.0, True, [], frames)
    assert judge_mod.pick_frames(o, 4) == [0.75, 2.25, 3.75, 5.25]


def test_decision_judge(monkeypatch):
    def fake_decide(spec, state, questions, images):
        assert spec == SNAP and len(images) == 4 and set(questions) == set(judge_mod.AXES)
        assert all(q["type"] == "score" and q["criteria"] == judge_mod.LEVELS for q in questions.values())
        assert "Reviewer" not in state and "<brief>" in state
        return {"answers": {a: {"type": "score", "score": 4.0 if a != "temporal" else 2.0, "confidence": 0.8}
                            for a in questions}, "usage": {"input_tokens": 100}}

    monkeypatch.setattr(llm, "decide", fake_decide)
    frames = {t / 2: Frame(t / 2, [], png()) for t in range(13)}
    o = Observation(1920, 1080, 6.0, True, [], frames)
    j = judge_mod.judge(Brief("b", "Hello, about 6 s"), o, model=SNAP)
    assert j["scores"]["craft"] == 10.0 and j["scores"]["temporal"] == 5.5
    assert j["mean"] == pytest.approx((4 * 1.0 + 0.5) / 5)
    assert "temporal" in j["notes"] and len(j["frames"]) == 4


def test_describe_every_brief_kind():
    cs = [C("a", "text_visible", text="Go", at=1), C("b", "text_hidden", text="Go", at=0.2),
          C("c", "text_color", text="Go", at=1, color="#FFFFFF"), C("d", "text_region", text="Go", at=1, region="left"),
          C("e", "bg_color", at=1, color="#000000"), C("f", "palette", colors=["#000000"]),
          C("g", "motion", t0=0, t1=1), C("h", "still", t0=5, t1=6), C("i", "order", first="A", then="B")]
    texts = [compile_mod.describe(c) for c in cs]
    assert all(t.endswith(".") and "{" not in t for t in texts)
    assert texts[3] == 'At 1 s, the words "Go" are in the left of the frame.'


def test_votes_make_hard_or_soft():
    checks = [C("b1", "text_visible", text="Go", at=1), C("b2", "text_visible", text="Stop", at=1)]
    checks[1].severity = "hard"
    compile_mod.apply_votes(checks, {"b1": {SNAP: 0.93}, "b2": {SNAP: 0.08}}, agree_min=2, panel=2)
    assert checks[0].severity == "hard" and checks[0].origin == ["meta:l", SNAP]
    assert checks[1].severity == "soft" and checks[1].origin == ["meta:l"]


def test_panel_routes_decision_models_to_voting(monkeypatch):
    def fake_propose(brief, model):
        assert not llm.is_decision(model)
        return [C("x", "text_visible", text="Hello", at=1.0), C("y", "text_visible", text="Invented", at=1.0)]

    def fake_vote(brief, checks, model):
        assert model == SNAP
        return {c.id: (0.9 if c.params["text"] == "Hello" else 0.1) for c in checks}

    monkeypatch.setattr(compile_mod, "propose", fake_propose)
    monkeypatch.setattr(compile_mod, "vote", fake_vote)
    checks, report = compile_mod.compile_brief(Brief("b", "Hello, about 3 s"), ["meta:l", SNAP])
    by_text = {c.params.get("text"): c for c in checks if c.kind == "text_visible"}
    assert by_text["Hello"].severity == "hard" and by_text["Invented"].severity == "soft"
    assert report["voters"] == [SNAP] and report["checkers"] == {"meta:l": 2} and report["agreed"] == 1
    with pytest.raises(RuntimeError, match="only vote"):
        compile_mod.compile_brief(Brief("b", "Hello"), [SNAP])


def test_vote_chunks_and_maps_ids(monkeypatch):
    calls = []

    def fake_decide(spec, state, questions):
        calls.append(len(questions))
        assert all(q["type"] == "noul" for q in questions.values()) and "<brief>" in state
        return {"answers": {k: {"type": "noul", "noul": 0.7} for k in questions}}

    monkeypatch.setattr(llm, "decide", fake_decide)
    checks = [C(f"b{i}", "motion", t0=0, t1=1) for i in range(130)]
    out = compile_mod.vote(Brief("b", "x"), checks, SNAP)
    assert calls == [128, 2] and len(out) == 130 and out["b129"] == 0.7
