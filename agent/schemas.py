"""Shapes of what moves through the studio graph. Everything stored in ADK session
state is plain JSON (model_dump), so a session can be inspected in `adk web`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReaderOutput(BaseModel):
    """One checker model's proposals for the brief (a reader slot)."""
    slot: int
    model: str | None = None
    checks: list[dict] = Field(default_factory=list)
    how: str = ""  # "writes" (text model proposes checks) or "snap" (decision model votes on candidates)
    error: str | None = None
    skipped: bool = False
    votes: dict | None = None  # snap readers: every candidate with its P(the brief asks for it)


class AttemptRecord(BaseModel):
    """One proposer attempt and the gate's verdict on it."""
    attempt: int
    proposer: str  # model spec, or "replay:<variant>"
    html: str
    run_dir: str
    decision: Literal["ACCEPT", "BLOCK"]
    score: float
    reasons: list[str]
    public_feedback: list[str]  # what the proposer is allowed to see next time
    hidden_failures: int  # how many hidden checks failed (count only, never their content)
    report: str
    judge: dict | None = None  # {"model", "scores", "mean"} or {"error"}; the judge sees frames only


class DeskReceipt(BaseModel):
    """What the desk hands out when it pauses: the render job to wait for."""
    job_id: str
    kind: Literal["hyperframes", "stand-in"]
    project_dir: str
    video: str
    pid: int | None = None
    log: str | None = None


class DeskAnswer(BaseModel):
    """How the paused desk is answered (by deliver.py, or a person in adk web)."""
    approved: bool = True
    video: str | None = None
    ok: bool = True
    note: str = ""
