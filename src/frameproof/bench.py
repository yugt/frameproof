"""Defect benchmark: catch rate on planted defects, false-block rate on originals.

For each (brief, good composition, checks):
  original -> should ACCEPT
  each mutant -> should BLOCK
Writes runs/bench-<ts>/results.jsonl and summary.md.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from .brief import Brief
from .checks import Check
from .config import SETTINGS
from .defects import mutants
from .verify import verify


def run_case(brief: Brief, html_path: Path, checks: list[Check], out: Path, seed: int = 0,
             use_judge: bool = False) -> list[dict]:
    rows = []
    case_dir = out / brief.id
    case_dir.mkdir(parents=True, exist_ok=True)
    v = verify(brief, html_path, checks, use_judge=use_judge)
    rows.append({"brief": brief.id, "variant": "original", "category": "none", "expected": "ACCEPT",
                 "decision": v.decision, "score": v.score, "reasons": v.reasons})
    for m in mutants(html_path.read_text(), seed=seed):
        # Keep the mutant next to the original so relative assets still resolve.
        p = html_path.parent / f".mutant-{m.name}.html"
        p.write_text(m.html)
        try:
            v = verify(brief, p, checks, use_judge=use_judge)
        finally:
            (case_dir / f"{m.name}.html").write_text(m.html)
            p.unlink(missing_ok=True)
        rows.append({"brief": brief.id, "variant": m.name, "category": m.category, "expected": "BLOCK",
                     "decision": v.decision, "score": v.score, "reasons": v.reasons})
    return rows


def summarize(rows: list[dict]) -> str:
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        k = r["variant"]
        by[k][0] += r["decision"] == r["expected"]
        by[k][1] += 1
    muts = [r for r in rows if r["expected"] == "BLOCK"]
    orig = [r for r in rows if r["expected"] == "ACCEPT"]
    caught = sum(r["decision"] == "BLOCK" for r in muts)
    false_block = sum(r["decision"] == "BLOCK" for r in orig)
    lines = [
        f"# Defect benchmark ({len(orig)} originals, {len(muts)} mutants)", "",
        f"- Catch rate: **{caught}/{len(muts)}** ({caught / max(1, len(muts)):.0%})",
        f"- False blocks on good originals: **{false_block}/{len(orig)}**", "",
        "| variant | correct |", "|---|---|",
    ]
    lines += [f"| {k} | {a}/{n} |" for k, (a, n) in sorted(by.items())]
    return "\n".join(lines) + "\n"


def bench(cases: list[tuple[Brief, Path, list[Check]]], use_judge: bool = False, seed: int = 0) -> Path:
    out = SETTINGS.runs_dir / f"bench-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for brief, html, checks in cases:
        rows += run_case(brief, html, checks, out, seed=seed, use_judge=use_judge)
    with (out / "results.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    (out / "summary.md").write_text(summarize(rows))
    return out
