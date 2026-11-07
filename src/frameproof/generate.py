"""The implementer: brief -> one HyperFrames index.html (the system under test).

Single call, no tools, like the Code2Video-AA setup. `skills_dir` holds the
versioned skill text the self-improvement loop edits (skills/<version>/*.md);
its contents are appended to the system prompt.
"""

from __future__ import annotations

from pathlib import Path

from . import llm
from .brief import Brief
from .config import SETTINGS

BASE_SYSTEM = """You write HyperFrames compositions: one self-contained index.html that renders
to video. Contract:
- Root element: data-composition-id="main" data-start="0" data-duration="<seconds>"
  data-width="1920" data-height="1080", explicitly sized 1920x1080, overflow hidden.
- Timed elements: class="clip" with id, data-start and data-duration (seconds).
- Animation: GSAP 3 from https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js.
  Build ONE timeline with gsap.timeline({ paused: true }), add all tweens with explicit
  positions, then register it: window.__timelines = window.__timelines || {};
  window.__timelines.main = tl;
- Deterministic: no Date.now, no Math.random, no requestAnimationFrame, no infinite repeats,
  no network fetches besides GSAP and web fonts.
- Use only the copy the brief asks for. Keep all text inside the frame with a safe margin.
Return the full file in one ```html block."""


def system_prompt(skills_dir: Path | None) -> str:
    if not skills_dir or not skills_dir.exists():
        return BASE_SYSTEM
    parts = [BASE_SYSTEM]
    for p in sorted(skills_dir.glob("*.md")):
        parts.append(f"\n## Skill: {p.stem}\n{p.read_text()}")
    return "\n".join(parts)


def generate(brief: Brief, out_dir: Path, model: str | None = None, skills_dir: Path | None = None,
             feedback: list[str] | None = None) -> Path:
    model = model or SETTINGS.implementer_model
    prompt = f"Brief:\n<brief>\n{brief.text}\n</brief>"
    if feedback:
        prompt += "\n\nA verifier rejected your previous attempt:\n" + "\n".join(f"- {f}" for f in feedback)
    reply = llm.complete(model, system_prompt(skills_dir), prompt, max_tokens=64000)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    path.write_text(llm.extract_block(reply.text))
    return path
