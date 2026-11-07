import pytest

from frameproof import compile as compile_mod
from frameproof import llm
from frameproof.brief import Brief
from frameproof.checks import Check
from frameproof.config import PROVIDERS, load_secrets


def C(kind, **params):
    return Check("x", kind, params, "hard", "src")


def test_merge_agreement_sets_severity():
    llama = [C("text_visible", text="Notes that think", at=1.2), C("bg_color", at=1.0, color="#0B1F3A"),
             C("motion", t0=0.1, t1=0.9)]
    gemini = [C("text_visible", text="notes that think", at=1.6), C("bg_color", at=1.4, color="#0C203B"),
              C("text_region", text="Try Tidepool free", at=5.4, region="center")]
    merged = compile_mod.merge({"meta:l": llama, "google:g": gemini}, agree_min=2)
    by_kind = {c.kind: c for c in merged}
    assert len(merged) == 4
    assert by_kind["text_visible"].severity == "hard" and by_kind["text_visible"].origin == ["meta:l", "google:g"]
    assert by_kind["bg_color"].severity == "hard"
    assert by_kind["motion"].severity == "soft" and by_kind["text_region"].severity == "soft"


def test_merge_does_not_pair_conflicting_checks():
    a = [C("text_color", text="Go", at=1, color="#FFFFFF"), C("text_region", text="Go", at=1, region="left")]
    b = [C("text_color", text="Go", at=1, color="#000000"), C("text_region", text="Go", at=1, region="right")]
    merged = compile_mod.merge({"a:1": a, "b:1": b})
    assert len(merged) == 4 and all(c.severity == "soft" for c in merged)


def test_single_checker_keeps_its_severity():
    merged = compile_mod.merge({"a:1": [Check("x", "palette", {"colors": ["#000000"]}, "soft")]})
    assert merged[0].severity == "soft"


def test_panel_survives_one_failing_checker(monkeypatch):
    def fake_propose(brief, model):
        if model == "bad:1":
            raise RuntimeError("down")
        c = C("text_visible", text="Hello", at=1.0)
        c.origin = [model]
        return [c]

    monkeypatch.setattr(compile_mod, "propose", fake_propose)
    checks, report = compile_mod.compile_brief(Brief("b", "Hello, about 3 s"), ["good:1", "bad:1"])
    assert report["errors"].keys() == {"bad:1"} and report["checkers"] == {"good:1": 1}
    assert any(c.kind == "text_visible" for c in checks) and any(c.kind == "renders" for c in checks)


def test_family_and_providers(monkeypatch):
    assert llm.family("anthropic:claude-opus-5-5") == "claude"
    assert llm.family("google:gemini-3.5-flash") == "gemini"
    assert llm.family("meta:Llama-4-Maverick-17B-128E-Instruct-FP8") == "llama"
    assert llm.family("subq:subq-preview") == "subq"
    monkeypatch.delenv("SUBQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUBQ_API_KEY"):
        llm.openai_client("subq")
    monkeypatch.setenv("FP_SUBQ_BASE_URL", "http://localhost:9/v1")
    assert PROVIDERS["subq"].url() == "http://localhost:9/v1"


def test_load_secrets(tmp_path, monkeypatch):
    f = tmp_path / "secrets.env"
    f.write_text("# comment\nexport FP_TEST_A='one'\nFP_TEST_B=two\nFP_TEST_C=\n")
    monkeypatch.setenv("FP_TEST_B", "shell-wins")
    monkeypatch.delenv("FP_TEST_A", raising=False)
    loaded = load_secrets(f)
    import os

    assert os.environ["FP_TEST_A"] == "one" and os.environ["FP_TEST_B"] == "shell-wins"
    assert "FP_TEST_A" in loaded and "FP_TEST_B" not in loaded
    monkeypatch.delenv("FP_TEST_A", raising=False)
    monkeypatch.delenv("FP_TEST_C", raising=False)
