"""The render desk: start the final render of an accepted attempt as a background job.

The real HyperFrames render takes minutes (5m19s for a 6 s clip on a 2-CPU node),
so the workflow does not wait inside a node. The desk starts the job, writes a
receipt to <run>/desk.json, and the graph pauses on a RequestInput whose
interrupt id is the job id. deliver.py (or a person in adk web) answers that id
once the job is done, and the graph resumes.

FP_REAL_RENDER=1 renders with HyperFrames. Otherwise (default) the stand-in uses
the probe preview the gate already made, so the loop finishes in seconds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .schemas import DeskReceipt


def real_render_enabled() -> bool:
    return os.environ.get("FP_REAL_RENDER", "0") == "1"


def start(run_dir: Path, attempt_dir: Path, job_id: str) -> DeskReceipt:
    run_dir, attempt_dir = Path(run_dir), Path(attempt_dir)
    final = run_dir / "final.mp4"
    if os.environ.get("FP_STUDIO_HTML_ONLY") == "1":
        receipt = DeskReceipt(job_id=job_id, kind="html", project_dir=str(attempt_dir),
                              video=str(attempt_dir / "index.html"))
    elif real_render_enabled() and shutil.which("npx"):
        log = run_dir / "desk.log"
        with log.open("w") as fh:
            p = subprocess.Popen(
                ["npx", "--no-install", "hyperframes", "render", "--quality", "draft", "--output", str(final.resolve())],
                cwd=attempt_dir, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        receipt = DeskReceipt(job_id=job_id, kind="hyperframes", project_dir=str(attempt_dir),
                              video=str(final), pid=p.pid, log=str(log))
    else:
        preview = attempt_dir / "video.mp4"
        if preview.exists():
            shutil.copyfile(preview, final)
        receipt = DeskReceipt(job_id=job_id, kind="stand-in", project_dir=str(attempt_dir), video=str(final))
    (run_dir / "desk.json").write_text(receipt.model_dump_json(indent=2))
    return receipt


def status(receipt: DeskReceipt) -> str:
    """running | done | failed"""
    if receipt.kind in {"stand-in", "html"}:
        return "done" if Path(receipt.video).exists() else "failed"
    if receipt.pid and _alive(receipt.pid):
        return "running"
    return "done" if Path(receipt.video).exists() and Path(receipt.video).stat().st_size > 0 else "failed"


def wait(receipt: DeskReceipt, timeout_s: float = 1800, poll_s: float = 5, on_tick=None) -> str:
    t0 = time.time()
    while (s := status(receipt)) == "running" and time.time() - t0 < timeout_s:
        if on_tick:
            on_tick(time.time() - t0)
        time.sleep(poll_s)
    return s


def load(run_dir: Path) -> DeskReceipt:
    return DeskReceipt(**json.loads((Path(run_dir) / "desk.json").read_text()))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a finished child that nobody reaped is a zombie: treat as not running
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True
