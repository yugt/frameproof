"""Run the studio from a terminal, end to end.

    python -m agent.run examples/tidepool-launch
    python -m agent.run briefs.json#my-brief --max-attempts 3

Prints one line per node message, waits at the desk for the render job, answers
it by id, and prints where the run page is. The same graph runs in `adk web apps`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import mimetypes
from pathlib import Path
import sys

from google.adk.runners import InMemoryRunner
from google.genai import types

from . import deliver
from .graph import root_agent

APP = "frameproof_studio"


def _print_event(e) -> None:
    path = (e.node_info.path if e.node_info else "") or ""
    node = path.rsplit("/", 1)[-1] if path else e.author
    for p in (e.content.parts if e.content and e.content.parts else []):
        if p.text:
            print(f"[{node}] {p.text}", flush=True)
    route = e.actions.route if e.actions else None
    if route:
        print(f"[{node}] route -> {route}", flush=True)


async def run(text: str, reference: Path | None = None, reference_mode: str = "inspiration") -> list:
    runner = InMemoryRunner(node=root_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="studio")
    parts = [types.Part(text=text)]
    if reference:
        import json
        mime = mimetypes.guess_type(reference.name)[0] or "application/octet-stream"
        parts = [types.Part(text=json.dumps({"text": text, "reference_mode": reference_mode})),
                 types.Part.from_bytes(data=reference.read_bytes(), mime_type=mime)]
    msg = types.Content(role="user", parts=parts)
    events = []
    answered: set[str] = set()  # our own answers are not echoed back as events
    while True:
        async for e in runner.run_async(user_id="studio", session_id=session.id, new_message=msg):
            events.append(e)
            _print_event(e)
        calls = [c for c in deliver.pending_calls(events) if c.id not in answered]
        if not calls:
            return events
        call = calls[0]
        r = deliver.receipt_of(call)
        print(f"[desk] waiting for {r.kind} render, job {call.id} -> {r.video}", flush=True)
        msg = await asyncio.to_thread(
            deliver.wait_and_answer, call,
            on_tick=lambda s: print(f"[desk] still rendering ({s:.0f}s)", flush=True) if int(s) % 30 < 5 else None)
        answered.add(call.id)
        print(f"[desk] answering {call.id}", flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m agent.run")
    ap.add_argument("brief", help="example folder, briefs.json#id, JSON brief, or brief text")
    ap.add_argument("--max-attempts", type=int)
    ap.add_argument("--replay", help="FP_REPLAY variants, e.g. shift_timing,typo,original")
    ap.add_argument("--reference", type=Path, help="image or video reference")
    ap.add_argument("--reference-mode", choices=["inspiration", "asset"], default="inspiration")
    ap.add_argument("--real-render", action="store_true", help="final render with HyperFrames (slow)")
    a = ap.parse_args(argv)
    if a.max_attempts:
        os.environ["FP_MAX_ATTEMPTS"] = str(a.max_attempts)
    if a.replay:
        os.environ["FP_REPLAY"] = a.replay
    if a.real_render:
        os.environ["FP_REAL_RENDER"] = "1"
    asyncio.run(run(a.brief, a.reference, a.reference_mode))


if __name__ == "__main__":
    sys.exit(main())
