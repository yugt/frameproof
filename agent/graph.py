"""The studio graph (ADK 2 Workflow).

Nodes are plain functions; blocking work (model calls, the headless-browser probe,
ffmpeg) runs in a thread so the workflow's event loop stays free and the three
readers really run in parallel.

Input (the first user message) is one of:
  examples/tidepool-launch          a folder with brief.json (and optional checks.json, index.html)
  briefs.json#<id>                  a brief file and an id
  {"id": ..., "text": ...}          a brief as JSON
  any other text                    the brief itself

Modes, all labelled in every report so nothing is mistaken for model output:
  readers   FP_CHECKER_MODELS (text models propose, subq:*snap* models vote).
            With none configured and the input folder holding checks.json, the
            panel uses that fixture.
  proposer  FP_IMPLEMENTER_MODEL when its key is set. Otherwise, for an example
            folder with index.html, "replay": FP_REPLAY lists the variants to
            submit in order (planted defects first, default
            shift_timing,typo,original), which shows the block -> revise -> accept loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from google.adk import Event, Workflow
from google.adk.events import RequestInput
from google.adk.workflow import JoinNode

from frameproof import compile as compile_mod
from frameproof import llm
from frameproof.brief import Brief
from frameproof.brief import load as load_briefs
from frameproof.checks import Check
from frameproof.config import PROVIDERS, ROOT, SETTINGS
from frameproof.defects import mutants
from frameproof.generate import generate
from frameproof.probe import preview_mp4
from frameproof.verify import verify, write_report

from . import desk as desk_mod
from .schemas import AttemptRecord, DeskAnswer, ReaderOutput
from .studio_report import write_studio_index

READER_SLOTS = 3


# ----------------------------------------------------------------------- intake


def _parse_input(text: str) -> tuple[Brief, Path | None]:
    text = text.strip()
    if text.startswith("{"):
        d = json.loads(text)
        return Brief(id=d.get("id", "brief"), text=d.get("text") or d.get("brief", "")), None
    path_part, _, bid = text.partition("#")
    p = Path(path_part)
    if not p.is_absolute():
        p = ROOT / p
    if p.is_dir() and (p / "brief.json").exists():
        return load_briefs(p / "brief.json")[0], p
    if p.is_file():
        briefs = load_briefs(p)
        pick = next((b for b in briefs if b.id == bid), None) if bid else (briefs[0] if len(briefs) == 1 else None)
        if pick is None:
            raise ValueError(f"{p}: pass '#<id>' to pick one of {[b.id for b in briefs]}")
        return pick, None
    return Brief(id="brief-" + time.strftime("%H%M%S"), text=text), None


def intake(node_input: str):
    """Read the brief, open a run folder, reset the loop counters."""
    brief, example = _parse_input(node_input)
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{brief.id}"
    run_dir = SETTINGS.runs_dir / "studio" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "brief.json").write_text(json.dumps({"id": brief.id, "text": brief.text}, indent=2))
    yield Event(state={
        "brief": {"id": brief.id, "text": brief.text, "duration": brief.duration},
        "example_dir": str(example or ""),
        "run_id": run_id, "run_dir": str(run_dir),
        "attempt": 0, "max_attempts": int(os.environ.get("FP_MAX_ATTEMPTS", "4")),
        "feedback": [], "history": [],
    }, message=f"Brief {brief.id!r}. Run folder: {run_dir}")


def _brief(d: dict) -> Brief:
    return Brief(id=d["id"], text=d["text"], duration=d.get("duration"))


# ---------------------------------------------------------------------- readers


def _writer_models() -> list[str]:
    return [m for m in SETTINGS.checker_models if not llm.is_decision(m)]


def _voter_models() -> list[str]:
    return [m for m in SETTINGS.checker_models if llm.is_decision(m)]


async def _read(slot: int, brief: dict) -> dict:
    models = _writer_models()
    if slot >= len(models):
        return ReaderOutput(slot=slot, skipped=True).model_dump()
    m = models[slot]
    try:
        checks = await asyncio.to_thread(compile_mod.propose, _brief(brief), m)
        return ReaderOutput(slot=slot, model=m, checks=[c.to_dict() for c in checks]).model_dump()
    except Exception as e:  # one reader failing must not sink the panel
        return ReaderOutput(slot=slot, model=m, error=f"{type(e).__name__}: {e}").model_dump()


async def reader_1(brief: dict):
    return await _read(0, brief)


async def reader_2(brief: dict):
    return await _read(1, brief)


async def reader_3(brief: dict):
    return await _read(2, brief)


join_readers = JoinNode(name="join_readers")


# ------------------------------------------------------------------------ panel


async def panel(node_input: dict, brief: dict, run_dir: str, example_dir: str = ""):
    """Merge the readers' checks (agreement -> hard), add the voters, hide a share."""
    outs = [ReaderOutput(**v) for v in node_input.values()]
    per_model = {o.model: [Check.from_dict(c) for c in o.checks] for o in outs if o.model and o.checks}
    errors = {o.model: o.error for o in outs if o.error}
    b = _brief(brief)
    if per_model:
        merged = compile_mod.merge(per_model, agree_min=SETTINGS.agree_min)
        for i, c in enumerate(merged):
            c.id = f"b{i+1}-{c.kind}"
        votes: dict[str, dict[str, float]] = {}
        voted = []
        for v in _voter_models():
            try:
                for cid, p in (await asyncio.to_thread(compile_mod.vote, b, merged, v)).items():
                    votes.setdefault(cid, {})[v] = p
                voted.append(v)
            except Exception as e:
                errors[v] = f"{type(e).__name__}: {e}"
        if voted:
            compile_mod.apply_votes(merged, votes, SETTINGS.agree_min, len(per_model) + len(voted), SETTINGS.vote_min)
        checks = compile_mod.mark_hidden(merged, b.id) + compile_mod.generic_checks(b.duration)
        source = {"mode": "panel", "writers": list(per_model), "voters": voted, "errors": errors}
    elif example_dir and (Path(example_dir) / "checks.json").exists():
        checks = compile_mod.load(Path(example_dir) / "checks.json")
        source = {"mode": "fixture", "file": str(Path(example_dir) / "checks.json"), "errors": errors,
                  "note": "no reader produced checks (no checker keys); using the example's hand-written checks"}
    else:
        raise RuntimeError(f"no checks: configure FP_CHECKER_MODELS (reader errors: {errors})")
    compile_mod.save(checks, Path(run_dir) / "checks.json")
    (Path(run_dir) / "panel.json").write_text(json.dumps(source, indent=2))
    hidden = sum(c.hidden for c in checks)
    yield Event(state={"checks": [c.to_dict() for c in checks], "panel": source},
                message=f"Panel ({source['mode']}): {len(checks)} checks, {hidden} hidden from the proposer.")


# --------------------------------------------------------------------- proposer


def _implementer_ready() -> bool:
    spec = SETTINGS.implementer_model
    if not spec:
        return False
    provider = spec.partition(":")[0]
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return provider in PROVIDERS and bool(PROVIDERS[provider].key())


def _replay_html(example_dir: Path, step: int) -> tuple[str, str]:
    seq = [s.strip() for s in os.environ.get("FP_REPLAY", "shift_timing,typo,original").split(",") if s.strip()]
    name = seq[min(step, len(seq) - 1)]
    original = (example_dir / "index.html").read_text()
    if name == "original":
        return original, name
    found = {m.name: m.html for m in mutants(original)}
    if name not in found:
        raise ValueError(f"FP_REPLAY: unknown variant {name!r}; choose from original, {', '.join(found)}")
    return found[name], name


async def proposer(brief: dict, attempt: int, run_dir: str, feedback: list, example_dir: str = ""):
    """Write the next composition. Sees the brief and PUBLIC feedback only."""
    n = attempt + 1
    attempt_dir = Path(run_dir) / f"attempt-{n}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    if _implementer_ready() and os.environ.get("FP_PROPOSER", "auto") != "replay":
        skills = os.environ.get("FP_SKILLS")
        await asyncio.to_thread(generate, _brief(brief), attempt_dir,
                                skills_dir=Path(skills) if skills else None, feedback=feedback or None)
        label = SETTINGS.implementer_model
    elif example_dir and (Path(example_dir) / "index.html").exists():
        html, variant = _replay_html(Path(example_dir), attempt)
        (attempt_dir / "index.html").write_text(html)
        label = f"replay:{variant}"
    else:
        raise RuntimeError("no proposer: set FP_IMPLEMENTER_MODEL and its key, or pass an example folder")
    (attempt_dir / "proposer.json").write_text(json.dumps({"proposer": label, "feedback_seen": feedback}, indent=2))
    yield Event(state={"attempt": n, "attempt_dir": str(attempt_dir), "html": str(attempt_dir / "index.html"),
                       "proposer_label": label},
                message=f"Attempt {n} by {label}" + (f", after {len(feedback)} feedback items" if feedback else ""))


# ------------------------------------------------------------------------- gate


def _verify_attempt(brief: dict, attempt_dir: Path, checks: list[dict], prev_dir: Path | None):
    b = _brief(brief)
    cs = [Check.from_dict(c) for c in checks]
    v = verify(b, attempt_dir / "index.html", cs, frames_dir=attempt_dir / "frames")
    v.meta["render"] = preview_mp4(attempt_dir / "index.html", attempt_dir / "video.mp4")
    v.meta["variant"] = attempt_dir.name
    write_report(v, attempt_dir, compare_to=prev_dir, back_link="../index.html")
    return v


async def gate(brief: dict, attempt: int, attempt_dir: str, checks: list, history: list,
               max_attempts: int, run_dir: str, proposer_label: str = ""):
    """frameproof decides. Routes ACCEPT, REVISE (public feedback only) or GIVE_UP."""
    ad = Path(attempt_dir)
    prev = Path(history[-1]["run_dir"]) if history else None
    v = await asyncio.to_thread(_verify_attempt, brief, ad, checks, prev)
    rec = AttemptRecord(
        attempt=attempt, proposer=proposer_label, html=str(ad / "index.html"), run_dir=str(ad),
        decision=v.decision, score=round(v.score, 3), reasons=v.reasons,
        public_feedback=v.public_feedback(),
        hidden_failures=sum(1 for r in v.results if r.hidden and not r.passed),
        report=str(ad / "report.html"))
    history = history + [rec.model_dump()]
    if v.decision == "ACCEPT":
        route = "ACCEPT"
    elif attempt < max_attempts:
        route = "REVISE"
    else:
        route = "GIVE_UP"
    write_studio_index(Path(run_dir), brief, history, status=route)
    shown = rec.public_feedback[:6]
    msg = (f"Gate: {v.decision} (score {v.score:.2f}) -> {route}. "
           + (f"{len(rec.public_feedback)} public failures go back to the proposer"
              f"{' (+' + str(rec.hidden_failures) + ' hidden, not shown)' if rec.hidden_failures else ''}: "
              + "; ".join(shown) if route == "REVISE" else ""))
    yield Event(state={"history": history, "feedback": rec.public_feedback, "verdict": rec.model_dump()},
                route=route, message=msg.strip())


# ------------------------------------------------------------------ desk/deliver


def desk(run_id: str, run_dir: str, attempt_dir: str):
    """Start the final render and pause until deliver.py answers the job id."""
    receipt = desk_mod.start(Path(run_dir), Path(attempt_dir), job_id=f"desk-{run_id}")
    yield Event(state={"desk": receipt.model_dump()})
    yield RequestInput(
        interrupt_id=receipt.job_id,
        message=(f"Accepted. Final render started ({receipt.kind}). Answer this call with "
                 f'{{"approved": true}} when {receipt.video} is ready, or {{"approved": false}} to stop.'),
        payload=receipt.model_dump(),
        response_schema=DeskAnswer.model_json_schema(),
    )


def _answer(node_input) -> DeskAnswer:
    if isinstance(node_input, dict):
        # adk web fills fields the person left out with null; drop them so defaults apply
        return DeskAnswer(**{k: v for k, v in node_input.items() if k in DeskAnswer.model_fields and v is not None})
    s = str(node_input).strip().lower()
    return DeskAnswer(approved=s not in {"no", "reject", "false", "stop"}, note=str(node_input))


def deliver(node_input, run_dir: str, brief: dict, history: list, desk: dict):
    ans = _answer(node_input)
    video = Path(ans.video or desk["video"])
    ok = ans.approved and ans.ok and video.exists()
    status = "DELIVERED" if ok else ("REJECTED_AT_DESK" if not ans.approved else "RENDER_FAILED")
    summary = {"status": status, "video": str(video) if video.exists() else None, "desk": desk,
               "attempts": len(history), "final": history[-1] if history else None}
    (Path(run_dir) / "summary.json").write_text(json.dumps(summary, indent=2))
    write_studio_index(Path(run_dir), brief, history, status=status, final_video=video if ok else None)
    yield Event(message=f"{status}: {video if ok else ans.note or 'no video'} "
                        f"after {len(history)} attempt(s). Open {Path(run_dir) / 'index.html'}")


def give_up(run_dir: str, brief: dict, history: list):
    (Path(run_dir) / "summary.json").write_text(json.dumps({"status": "GAVE_UP", "attempts": len(history),
                                                            "final": history[-1] if history else None}, indent=2))
    write_studio_index(Path(run_dir), brief, history, status="GAVE_UP")
    yield Event(message=f"GAVE_UP after {len(history)} attempts; nothing shipped. Open {Path(run_dir) / 'index.html'}")


# ------------------------------------------------------------------------ graph

root_agent = Workflow(
    name="frameproof_studio",
    description="Writes a HyperFrames video for a brief; frameproof gates every attempt.",
    edges=[
        ("START", intake, (reader_1, reader_2, reader_3), join_readers, panel, proposer, gate),
        (gate, {"ACCEPT": desk, "REVISE": proposer, "GIVE_UP": give_up}),
        (desk, deliver),
    ],
)
