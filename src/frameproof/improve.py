"""Retrieval -> generation -> verification -> memory -> repair loop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .brief import Brief
from .checks import Check
from .generate import generate
from .memory import MemoryHit, MemoryStore
from .verify import Verdict, verify, write_report


@dataclass
class ImproveResult:
    decision: str
    attempts: int
    html: Path
    verdict: Verdict
    retrieved: list[MemoryHit]
    out_dir: Path


def run(brief: Brief, checks: list[Check], out_dir: Path, *, memory: MemoryStore | None = None,
        max_attempts: int = 2, use_judge: bool = False, model: str | None = None,
        skills_dir: Path | None = None,
        generate_fn: Callable = generate, verify_fn: Callable = verify) -> ImproveResult:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    memory = memory or MemoryStore()
    memory.ping()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Retrieve only historical examples. Store the current requirement afterward
    # so it cannot become its own nearest neighbour on the first run.
    retrieved = memory.search(
        brief.text,
        types=("brief_requirement", "verification_failure", "successful_fix"),
        limit=6,
        exclude_brief_id=brief.id,
    )
    memory.remember_requirement(brief)
    feedback = ["Relevant historical memory (use as guidance, not as a replacement for the brief):"]
    feedback += [h.guidance() for h in retrieved]
    previous: Verdict | None = None
    latest_html: Path | None = None
    latest: Verdict | None = None

    for attempt in range(1, max_attempts + 1):
        attempt_dir = out_dir / f"attempt-{attempt}"
        latest_html = generate_fn(brief, attempt_dir, model=model, skills_dir=skills_dir,
                                  feedback=feedback or None)
        latest = verify_fn(brief, latest_html, checks, use_judge=use_judge,
                           frames_dir=attempt_dir / "frames")
        write_report(latest, attempt_dir)
        if latest.decision == "ACCEPT":
            if previous is not None:
                memory.remember_fix(brief, previous, latest, feedback, attempt)
            break

        memory.remember_failure(brief, latest, attempt)
        previous = latest
        if attempt == max_attempts:
            break
        failure_query = "\n".join([brief.text, *latest.public_feedback(), *latest.public_reasons()])
        similar = memory.search(
            failure_query,
            types=("verification_failure", "successful_fix"),
            limit=5,
            exclude_brief_id=brief.id,
        )
        retrieved.extend(similar)
        feedback = latest.public_feedback()
        feedback += ["Similar historical failure or fix: " + h.guidance() for h in similar]

    assert latest_html is not None and latest is not None
    retrieved = list({(h.type, h.brief_id, h.content): h for h in retrieved}.values())
    summary = {
        "brief_id": brief.id, "decision": latest.decision, "attempts": attempt,
        "html": str(latest_html), "score": latest.score,
        "retrieved": [{"type": h.type, "score": round(h.score, 4),
                       "brief_id": h.brief_id, "content": h.content}
                      for h in retrieved],
    }
    (out_dir / "improve.json").write_text(json.dumps(summary, indent=2))
    return ImproveResult(latest.decision, attempt, latest_html, latest, retrieved, out_dir)
