"""frameproof CLI.

  frameproof models   [--provider google]                        list model ids each key can reach
  frameproof compile  BRIEFS.json [--id ID] [--out checks/] [--checkers a:x,b:y]
                                                                 brief -> checks (checker panel)
  frameproof vote     BRIEFS.json --checks checks.json [--voters subq:subq-snap-1.1]
                                                                 decision model scores each check against the brief
  frameproof probe    index.html  [--at 0,1.5,3] [--frames DIR]  dump what the probe sees
  frameproof verify   BRIEFS.json --id ID index.html --checks checks.json [--judge] [--lint]
  frameproof bench    examples/                                  planted-defect benchmark
  frameproof generate BRIEFS.json --id ID [--out runs/gen/ID]    implementer writes index.html
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import brief as brief_mod
from . import compile as compile_mod
from .config import SETTINGS


def _pick(path: str, bid: str | None):
    briefs = brief_mod.load(path)
    if bid is None:
        if len(briefs) != 1:
            sys.exit(f"{path} has {len(briefs)} briefs; pass --id")
        return briefs[0]
    for b in briefs:
        if b.id == bid:
            return b
    sys.exit(f"no brief {bid!r} in {path}")


def _warn_independence() -> None:
    from .llm import family

    imp = family(SETTINGS.implementer_model)
    roles = [("checker", m) for m in SETTINGS.checker_models] + [("judge", SETTINGS.judge_model)]
    for role, spec in roles:
        if spec and family(spec) == imp:
            print(f"warning: {role} ({spec}) is the same family as the implementer "
                  f"({SETTINGS.implementer_model}); independence is weaker", file=sys.stderr)


def cmd_compile(a) -> None:
    _warn_independence()
    briefs = brief_mod.load(a.briefs)
    for b in briefs:
        if a.id and b.id != a.id:
            continue
        models = [m.strip() for m in a.checkers.split(",")] if a.checkers else None
        checks, report = compile_mod.compile_brief(b, models)
        out = Path(a.out) / f"{b.id}.json"
        compile_mod.save(checks, out)
        out.with_suffix(".panel.json").write_text(json.dumps(report, indent=2))
        print(f"{b.id}: {len(checks)} checks -> {out}  (per checker {report['checkers']}, "
              f"agreed {report['agreed']}/{report['merged']}, errors {list(report['errors']) or 'none'})")


def cmd_vote(a) -> None:
    b = _pick(a.briefs, a.id)
    checks = [c for c in compile_mod.load(Path(a.checks)) if c.kind in compile_mod.BRIEF_KINDS]
    voters = [m.strip() for m in a.voters.split(",")] if a.voters else [m for m in SETTINGS.checker_models
                                                                         if compile_mod.llm.is_decision(m)]
    if not voters:
        sys.exit("no voter: pass --voters subq:subq-snap-1.1 or add a decision model to FP_CHECKER_MODELS")
    votes = {v: compile_mod.vote(b, checks, v) for v in voters}
    print(f"| check | {' | '.join(voters)} | statement |\n|---|{'---|' * len(voters)}---|")
    for c in checks:
        ps = " | ".join(f"{votes[v][c.id]:.2f}" for v in voters)
        print(f"| {c.id} | {ps} | {compile_mod.describe(c)} |")
    if a.out:
        Path(a.out).write_text(json.dumps(votes, indent=2))


def cmd_models(a) -> None:
    from .config import PROVIDERS
    from .llm import list_models

    names = [a.provider] if a.provider else ["anthropic", *PROVIDERS]
    for name in names:
        if name != "anthropic" and not PROVIDERS[name].key():
            print(f"{name}: no key (set {' or '.join(PROVIDERS[name].key_envs)})")
            continue
        try:
            ids = list_models(name)
            print(f"{name}: {len(ids)} models")
            for i in ids if a.all else ids[:40]:
                print(f"  {name}:{i}")
        except Exception as e:
            print(f"{name}: error {type(e).__name__}: {str(e)[:200]}")


def cmd_probe(a) -> None:
    from .probe import probe

    times = [float(t) for t in a.at.split(",")] if a.at else None
    obs = probe(a.html, times=times, out_dir=Path(a.frames) if a.frames else None)
    print(json.dumps({"size": [obs.width, obs.height], "duration": obs.duration,
                      "timeline_ok": obs.timeline_ok, "errors": obs.errors,
                      "frames": {t: [asdict(tb) for tb in f.texts] for t, f in obs.frames.items()}},
                     indent=1, default=str))


def cmd_verify(a) -> None:
    from .probe import render_gate
    from .verify import verify, write_report

    _warn_independence()
    b = _pick(a.briefs, a.id)
    checks = compile_mod.load(Path(a.checks))
    out = Path(a.out or SETTINGS.runs_dir / "verify" / b.id)
    v = verify(b, Path(a.html), checks, use_judge=a.judge, frames_dir=out / "frames")
    if a.lint:
        v.meta["hyperframes"] = render_gate(Path(a.html).parent, render=a.render)
        if v.meta["hyperframes"].get("status") == "failed":
            v.decision = "BLOCK"
            v.reasons.append("hyperframes lint/render failed")
    if a.render_video:
        from .probe import preview_mp4, render_mp4

        v.meta["render"] = ({**render_mp4(Path(a.html).parent, out / "video.mp4"), "kind": "hyperframes"}
                            if a.render_video == "hyperframes" else preview_mp4(a.html, out / "video.mp4"))
    write_report(v, out)
    print((out / "verdict.md").read_text())
    print(f"open: {out / 'report.html'}")
    sys.exit(0 if v.decision == "ACCEPT" else 1)


def cmd_bench(a) -> None:
    from .bench import bench

    cases = []
    for d in sorted(Path(a.examples).iterdir()):
        if not (d / "index.html").exists():
            continue
        b = brief_mod.load(d / "brief.json")[0]
        checks = compile_mod.load(d / "checks.json")
        cases.append((b, d / "index.html", checks))
    out = bench(cases, use_judge=a.judge, seed=a.seed, video=a.video)
    print((out / "summary.md").read_text())
    print(f"open: {out / 'index.html'}")


def cmd_generate(a) -> None:
    from .generate import generate

    b = _pick(a.briefs, a.id)
    out = Path(a.out or SETTINGS.runs_dir / "gen" / b.id)
    path = generate(b, out, skills_dir=Path(a.skills) if a.skills else None)
    print(path)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="frameproof")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("models"); s.add_argument("--provider"); s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_models)

    s = sub.add_parser("compile"); s.add_argument("briefs"); s.add_argument("--id"); s.add_argument("--out", default="checks")
    s.add_argument("--checkers", help="comma list of provider:model, overrides FP_CHECKER_MODELS")
    s.set_defaults(fn=cmd_compile)

    s = sub.add_parser("vote"); s.add_argument("briefs"); s.add_argument("--id")
    s.add_argument("--checks", required=True); s.add_argument("--voters", help="comma list of decision models")
    s.add_argument("--out", help="write the votes as JSON"); s.set_defaults(fn=cmd_vote)

    s = sub.add_parser("probe"); s.add_argument("html"); s.add_argument("--at"); s.add_argument("--frames")
    s.set_defaults(fn=cmd_probe)

    s = sub.add_parser("verify"); s.add_argument("briefs"); s.add_argument("html"); s.add_argument("--id")
    s.add_argument("--checks", required=True); s.add_argument("--judge", action="store_true")
    s.add_argument("--lint", action="store_true", help="also run the HyperFrames CLI lint")
    s.add_argument("--render", action="store_true", help="with --lint, also render a draft MP4")
    s.add_argument("--video", dest="render_video", nargs="?", const="preview", choices=["preview", "hyperframes"],
                   help="add a video to the report: preview (fast, default) or hyperframes (real render)")
    s.add_argument("--out"); s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("bench"); s.add_argument("examples"); s.add_argument("--judge", action="store_true")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--video", choices=["preview", "hyperframes", "none"], default="preview",
                   help="video per variant: fast probe preview (default), real HyperFrames render, or none")
    s.set_defaults(fn=cmd_bench)

    s = sub.add_parser("generate"); s.add_argument("briefs"); s.add_argument("--id"); s.add_argument("--out")
    s.add_argument("--skills"); s.set_defaults(fn=cmd_generate)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
