"""Verdict = render gate -> hard checks -> score (soft checks + optional judge).

Policy:
  BLOCK  if it does not render, or any hard check fails.
  BLOCK  if score < accept_threshold.
  ACCEPT otherwise.
The report lists every check with the brief phrase it came from (evidence), so a
blocked change says exactly why.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .brief import Brief
from .checks import Check, CheckResult, Observation, evaluate
from .config import SETTINGS
from .probe import probe


@dataclass
class Verdict:
    decision: str  # ACCEPT | BLOCK
    score: float
    reasons: list[str]
    results: list[CheckResult]
    judge: dict | None = None
    meta: dict = field(default_factory=dict)

    def public_feedback(self) -> list[str]:
        """What the implementer may see: failures of non-hidden checks only."""
        return [f"{r.id}: {r.detail}" for r in self.results if not r.passed and not r.hidden]

    def public_reasons(self) -> list[str]:
        """Sanitized decision reasons that reveal no hidden check IDs or details."""
        public_hard = [
            f"public hard check failed: {r.id} ({r.detail})"
            for r in self.results
            if not r.passed and not r.hidden and r.severity == "hard"
        ]
        reasons = list(public_hard)
        if self.score < SETTINGS.accept_threshold:
            reasons.append(f"public verification score was below the acceptance threshold")
        if self.decision == "BLOCK" and not public_hard and any(
            not r.passed and r.hidden for r in self.results
        ):
            reasons.append("verification blocked by a withheld check; no hidden details are available")
        return reasons

    def to_dict(self) -> dict:
        return asdict(self)


def decide(results: list[CheckResult], judge_score: float | None = None,
           threshold: float | None = None, judge_weight: float | None = None) -> tuple[str, float, list[str]]:
    threshold = SETTINGS.accept_threshold if threshold is None else threshold
    w = SETTINGS.judge_weight if judge_weight is None else judge_weight
    reasons = []
    render = next((r for r in results if r.kind == "renders"), None)
    if render and not render.passed:
        return "BLOCK", 0.0, [f"does not render: {render.detail}"]
    hard_fail = [r for r in results if r.severity == "hard" and not r.passed]
    reasons += [f"hard check failed: {r.id} ({r.detail})" for r in hard_fail]
    checks_score = sum(r.passed for r in results) / max(1, len(results))
    score = checks_score if judge_score is None else (1 - w) * checks_score + w * judge_score
    if score < threshold:
        reasons.append(f"score {score:.2f} < {threshold:.2f}")
    return ("BLOCK" if reasons else "ACCEPT"), score, reasons


def times_needed(checks: list[Check], duration: float | None, step: float) -> list[float]:
    from .probe import sample_times

    extra = [t for c in checks for t in c.times()]
    return sample_times(duration or (max(extra) + 0.5 if extra else 5.0), step, extra)


def verify(brief: Brief, html_path: Path, checks: list[Check], use_judge: bool = False,
           frames_dir: Path | None = None, obs: Observation | None = None) -> Verdict:
    t0 = time.time()
    if obs is None:
        obs = probe(html_path, times=times_needed(checks, brief.duration, SETTINGS.sample_step),
                    out_dir=frames_dir)
    vocab = brief.vocabulary()
    results = [evaluate(c, obs, vocab) for c in checks]
    j = None
    if use_judge and obs.frames and obs.timeline_ok:
        from .judge import judge

        j = judge(brief, obs)
    decision, score, reasons = decide(results, j["mean"] if j else None)
    return Verdict(decision, score, reasons, results, j,
                   {"brief": brief.id, "html": str(html_path), "seconds": round(time.time() - t0, 2),
                    "frames": len(obs.frames), "duration": obs.duration, "width": obs.width, "height": obs.height})


def write_report(v: Verdict, out: Path, html: bool = True, compare_to: Path | None = None,
                 back_link: str | None = None) -> None:
    """verdict.json + verdict.md, and report.html unless html=False."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdict.json").write_text(json.dumps(v.to_dict(), indent=2))
    lines = [f"# {v.decision}  (score {v.score:.2f})", ""]
    lines += [f"- {r}" for r in v.reasons] or ["- all checks passed"]
    lines += ["", "| check | kind | sev | hidden | pass | detail |", "|---|---|---|---|---|---|"]
    for r in v.results:
        lines.append(f"| {r.id} | {r.kind} | {r.severity} | {'y' if r.hidden else ''} | "
                     f"{'✅' if r.passed else '❌'} | {r.detail.replace('|', '/')} |")
    if v.judge:
        lines += ["", f"Judge ({v.judge['model']}): {v.judge['scores']} - {v.judge['notes']}"]
    (out / "verdict.md").write_text("\n".join(lines) + "\n")
    if html:
        from .report import render_run

        render_run(out, compare_to=compare_to, back_link=back_link)
