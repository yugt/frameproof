"""Candidate checks from a brief by plain parsing, for readers that cannot write text.

A decision model (SubQ Snap) answers questions with probabilities. It cannot
write checks, but it can decide which ones the brief asks for. So this module
over-generates: every quoted on-screen phrase, every palette colour mentioned
near it, every region word, every scene's motion or hold, the order of the
scenes. `snap_reader` then has Snap vote on each candidate and keeps the yeses.

No generative model is involved, so the reader shares nothing with the
proposer: the candidates come from the brief's text and the selection from a
model that only outputs probabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .brief import Brief
from .checks import Check

_SCENE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s\s*)?[-–]\s*(\d+(?:\.\d+)?)\s*s\b")
_QUOTE = re.compile(r"[\"“”]([^\"“”]{2,80})[\"“”]")
_PALETTE = re.compile(r"((?:[A-Za-z]+ ){0,2}?[A-Za-z]+)\s*\(?\s*(#[0-9A-Fa-f]{6})\b\)?\s*([A-Za-z]+)?")
_REGIONS = {"left": "left", "right": "right", "top": "top", "bottom": "bottom", "centre": "center",
            "center": "center", "middle": "center", "centred": "center", "centered": "center"}
_MOTION = re.compile(r"\b(ris\w*|slid\w*|pop\w*|mov\w*|zoom\w*|connect\w*|fad\w*|spin\w*|grow\w*|draw\w*|"
                     r"typ\w*|bounc\w*|sweep\w*|reveal\w*|scal\w*|rotat\w*|flip\w*|drop\w*|word by word)\b", re.I)
_HOLD = re.compile(r"\b(holds? still|holds?\b|static end|freez\w*|rests?\b|settles?\b)", re.I)
_BACKGROUND_ROLES = {"background", "bg", "backdrop"}
_TYPE_ROLES = {"type", "text", "copy", "font", "typography", "headline", "headlines", "letters"}
_NOT_NAMES = {"palette", "and", "with", "a", "the", "colour", "color", "colours", "colors"}


@dataclass
class Scene:
    a: float
    b: float
    text: str

    def mid(self) -> float:
        """A time safely inside the scene: never its edges (brief timings are approximate)."""
        t = self.a + max(0.6, 0.6 * (self.b - self.a))
        return round(min(t, self.b - 0.1), 2)


def scenes(brief: Brief) -> list[Scene]:
    ms = list(_SCENE.finditer(brief.text))
    out = []
    for i, m in enumerate(ms):
        a, b = float(m.group(1)), float(m.group(2))
        if b <= a:
            continue
        end = ms[i + 1].start() if i + 1 < len(ms) else len(brief.text)
        out.append(Scene(a, b, brief.text[m.end():end]))
    if not out:
        out.append(Scene(0.0, brief.duration or 5.0, brief.text))
    return out


def palette(brief: Brief) -> list[tuple[str, str, str]]:
    """(name, #HEX, role) for each palette colour, e.g. ('deep navy', '#0B1F3A', 'background')."""
    out = []
    for m in _PALETTE.finditer(brief.text):
        words = [w for w in m.group(1).lower().split() if w not in _NOT_NAMES]
        out.append((" ".join(words), m.group(2).upper(), (m.group(3) or "").lower()))
    return out


def _named(text: str, pal: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    t = text.lower()
    return [p for p in pal if p[0] and re.search(r"\b" + re.escape(p[0].split()[-1]) + r"\b", t)]


def _quote_context(seg: str, q: str, width: int = 70) -> str:
    i = seg.find(q)
    return seg[max(0, i - width): i + len(q) + width].strip(" ,.;-")


def generate(brief: Brief) -> list[Check]:
    """Over-generate candidate checks. Ids are c1, c2, ... in a stable order."""
    pal = palette(brief)
    sc = scenes(brief)
    out: list[Check] = []

    def add(kind: str, params: dict, source: str, severity: str = "hard"):
        out.append(Check(f"c{len(out) + 1}", kind, params, severity, source.strip()[:160]))

    firsts: list[tuple[str, Scene]] = []
    for k, s in enumerate(sc):
        quotes = _QUOTE.findall(s.text)
        regions = sorted({_REGIONS[w] for w in re.findall(r"\b(" + "|".join(_REGIONS) + r")\b", s.text.lower())})
        named = _named(s.text, pal)
        for q in quotes:
            ctx = _quote_context(s.text, q)
            add("text_visible", {"text": q, "at": s.mid()}, f"{s.a:g}-{s.b:g} s: {ctx}")
            if k > 0:
                add("text_hidden", {"text": q, "at": sc[0].mid()}, f"appears only from {s.a:g} s")
            # Text colour: the palette's type colour applies to all copy; another colour
            # only when the brief puts it on the words ("in coral", "coral text"), not
            # when it names a thing near them ("a coral button reading ...").
            for name, hx, role in pal:
                if role in _TYPE_ROLES:
                    add("text_color", {"text": q, "at": s.mid(), "color": hx}, f"{name} {hx} {role}: {ctx}")
            for name, hx, role in named:
                last = re.escape(name.split()[-1])
                on_words = re.search(rf"\bin {last}\b|\b{last} (?:text|type|letters|words|copy)\b", ctx.lower())
                if role not in _BACKGROUND_ROLES | _TYPE_ROLES and on_words:
                    add("text_color", {"text": q, "at": s.mid(), "color": hx}, f"{name} {hx} on the words: {ctx}")
            for r in regions:
                add("text_region", {"text": q, "at": s.mid(), "region": r}, f"{r} near: {ctx}")
        if quotes:
            firsts.append((quotes[0], s))
        for name, hx, role in named:  # a named accent fills part of the frame (type is too thin to measure this way)
            if role not in _BACKGROUND_ROLES | _TYPE_ROLES:
                add("bg_color", {"at": s.mid(), "color": hx, "min_fraction": 0.03}, f"{name} {hx} in {s.a:g}-{s.b:g} s")
        if _MOTION.search(s.text) and s.b - s.a >= 0.5:
            add("motion", {"t0": round(s.a + 0.1, 2), "t1": round(min(s.b - 0.05, s.a + 0.9), 2), "min_change": 0.01},
                f"{s.a:g}-{s.b:g} s: {_MOTION.search(s.text).group(0)}")
        if _HOLD.search(s.text) and s.b - s.a >= 0.6:
            add("still", {"t0": round(max(s.a, s.b - 0.5), 2), "t1": round(s.b - 0.05, 2), "max_change": 0.005},
                f"{s.a:g}-{s.b:g} s: {_HOLD.search(s.text).group(0)}", severity="soft")
    for name, hx, role in pal:
        if role in _BACKGROUND_ROLES:
            for s in (sc[0], sc[-1]) if len(sc) > 1 else (sc[0],):
                add("bg_color", {"at": s.mid(), "color": hx, "min_fraction": 0.5}, f"{name} {hx} {role}")
    for (q1, s1), (q2, s2) in zip(firsts, firsts[1:]):
        add("order", {"first": q1, "then": q2}, f"scene {s1.a:g}-{s1.b:g} s before {s2.a:g}-{s2.b:g} s")
    if pal:
        add("palette", {"colors": [hx for _, hx, _ in pal], "min_fraction": 0.85}, "Palette", severity="soft")
    # de-duplicate identical requirements
    seen, uniq = set(), []
    for c in out:
        key = (c.kind, tuple(sorted((k, str(v)) for k, v in c.params.items())))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    for i, c in enumerate(uniq):
        c.id = f"c{i + 1}"
    return uniq


def snap_reader(brief: Brief, model: str, keep: float = 0.5, hard: float = 0.75) -> tuple[list[Check], dict]:
    """Candidates -> one decision model's votes -> the checks it says the brief asks for.

    P >= hard: hard check; keep <= P < hard: soft; below keep: dropped. Returns
    (checks, votes by candidate id) so the report can show what was rejected."""
    from .compile import vote

    cands = generate(brief)
    probs = vote(brief, cands, model)
    kept = []
    for c in cands:
        p = probs.get(c.id, 0.0)
        if p < keep:
            continue
        sev = "soft" if c.severity == "soft" else ("hard" if p >= hard else "soft")
        kept.append(Check(c.id, c.kind, dict(c.params), sev, f"{c.source} (P={p:.2f})", False, [model]))
    return kept, {"candidates": [{"id": c.id, "kind": c.kind, "params": c.params, "p": probs.get(c.id)} for c in cands]}
