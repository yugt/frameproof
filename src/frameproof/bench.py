"""Defect benchmark: catch rate on planted defects, false-block rate on originals.

For each (brief, good composition, checks):
  original -> should ACCEPT
  each mutant -> should BLOCK
Writes runs/bench-<ts>/: results.jsonl, summary.md, index.html (open in a browser)
and one run directory with report.html per variant.
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
from .probe import preview_mp4, render_mp4
from .report import render_index, render_run
from .verify import verify, write_report


def run_case(brief: Brief, html_path: Path, checks: list[Check], out: Path, seed: int = 0,
             use_judge: bool = False, video: str = "preview") -> list[dict]:
    """Verify the original and every mutant. Each variant gets its own run directory
    <out>/<brief>/<variant>/ with index.html, verdict, frames, report.html and,
    video.mp4: video="preview" (fast probe preview, default), "hyperframes" (real render,
    minutes per clip on a small node) or "none".

    Variants are copied into their own folders, so compositions that load
    relative assets need those assets copied too (the bundled example has none)."""
    case_dir = out / brief.id
    variants = [("original", "none", "ACCEPT", html_path.read_text())]
    variants += [(m.name, m.category, "BLOCK", m.html) for m in mutants(variants[0][3], seed=seed)]
    rows, dirs = [], {}
    for name, cat, expected, text in variants:
        d = case_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(text)
        v = verify(brief, d / "index.html", checks, use_judge=use_judge, frames_dir=d / "frames")
        v.meta.update({"variant": name, "category": cat, "expected": expected})
        if video == "preview":
            v.meta["render"] = preview_mp4(d / "index.html", d / "video.mp4")
        elif video == "hyperframes":
            v.meta["render"] = {**render_mp4(d, d / "video.mp4"), "kind": "hyperframes"}
        write_report(v, d, html=False)
        dirs[name] = d
        rows.append({"brief": brief.id, "variant": name, "category": cat, "expected": expected,
                     "decision": v.decision, "score": v.score, "reasons": v.reasons})
        print(f"  {brief.id}/{name}: {v.decision} (expected {expected})"
              + (f", video {'ok' if v.meta['render']['ok'] else 'failed'}" if video != "none" else ""), flush=True)
    # Reports last, so each mutant can show the original side by side.
    for name, d in dirs.items():
        render_run(d, compare_to=None if name == "original" else dirs["original"],
                   title=f"{brief.id} · {name}", back_link="../../index.html")
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


def bench(cases: list[tuple[Brief, Path, list[Check]]], use_judge: bool = False, seed: int = 0,
          video: str = "preview") -> Path:
    out = SETTINGS.runs_dir / f"bench-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for brief, html, checks in cases:
        rows += run_case(brief, html, checks, out, seed=seed, use_judge=use_judge, video=video)
    with (out / "results.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    summary = summarize(rows)
    (out / "summary.md").write_text(summary)
    render_index(out, rows, summary)
    return out
