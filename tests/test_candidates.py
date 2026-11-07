"""Candidate generator for decision-model readers (offline; Snap's votes are faked)."""

from pathlib import Path

from frameproof import candidates
from frameproof.brief import load

BRIEF = load(Path(__file__).resolve().parents[1] / "examples" / "tidepool-launch" / "brief.json")[0]


def _has(cs, kind, **params):
    return any(c.kind == kind and all(c.params.get(k) == v for k, v in params.items()) for c in cs)


def test_palette_and_scenes():
    assert candidates.palette(BRIEF) == [("deep navy", "#0B1F3A", "background"), ("white", "#FFFFFF", "type"),
                                         ("coral", "#FF6B35", "accent")]
    assert [(s.a, s.b, s.mid()) for s in candidates.scenes(BRIEF)] == [(0, 2, 1.2), (2, 4.5, 3.5), (4.5, 6, 5.4)]


def test_generates_the_brief_requirements_and_some_wrong_ones():
    cs = candidates.generate(BRIEF)
    # what the brief asks for
    assert _has(cs, "text_visible", text="Notes that think", at=1.2)
    assert _has(cs, "text_color", text="Notes that think", color="#FFFFFF")
    assert _has(cs, "text_region", text="Auto-link every idea", region="left")
    assert _has(cs, "text_hidden", text="Try Tidepool free", at=1.2)
    assert _has(cs, "bg_color", color="#0B1F3A", min_fraction=0.5)
    assert _has(cs, "order", first="Auto-link every idea", then="Try Tidepool free")
    assert _has(cs, "still") and _has(cs, "motion") and _has(cs, "palette")
    # plausible but wrong: left for the voter to reject
    assert _has(cs, "text_region", text="Auto-link every idea", region="right")
    # type colour applies to every quote; "a coral button reading ..." is not coral text
    assert _has(cs, "text_color", text="Try Tidepool free", color="#FFFFFF")
    assert not _has(cs, "text_color", text="Try Tidepool free", color="#FF6B35")
    assert not _has(cs, "bg_color", color="#FFFFFF")  # type is too thin for a fill check
    assert len({c.id for c in cs}) == len(cs) and len(cs) <= 128  # one Snap call


def test_snap_reader_keeps_votes_above_threshold(monkeypatch):
    import frameproof.compile as compile_mod

    def fake_vote(brief, checks, model):
        return {c.id: (0.1 if c.params.get("region") == "right" else 0.6 if c.kind == "palette" else 0.9) for c in checks}

    monkeypatch.setattr(compile_mod, "vote", fake_vote)
    kept, report = candidates.snap_reader(BRIEF, "subq:subq-snap-1.1")
    assert not _has(kept, "text_region", region="right")
    assert all(c.origin == ["subq:subq-snap-1.1"] for c in kept)
    assert next(c for c in kept if c.kind == "palette").severity == "soft"
    assert next(c for c in kept if c.kind == "text_visible").severity == "hard"
    assert len(report["candidates"]) == len(candidates.generate(BRIEF))


def test_brief_without_scenes_or_quotes():
    from frameproof.brief import Brief

    cs = candidates.generate(Brief(id="x", text="A calm 4 s logo sting on #101010 background, slow zoom."))
    assert _has(cs, "bg_color", color="#101010") and _has(cs, "motion")


def test_explicit_colour_on_words_is_kept():
    from frameproof.brief import Brief

    b = Brief(id="y", text='Palette: black #000000 background, gold #D4AF37 accent. 0-3 s: "Grand opening" in gold.')
    cs = candidates.generate(b)
    assert _has(cs, "text_color", text="Grand opening", color="#D4AF37")
