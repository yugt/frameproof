"""Adopt-or-rollback gate for the self-improvement loop (stretch goal).

A candidate change (new skill version) is adopted only if, on HELD-OUT briefs
scored by HIDDEN checks, it beats the current version by more than noise:
the lower bound of a paired bootstrap CI on the mean score difference must be
above `min_delta`. Otherwise it is rolled back. Every decision is appended to
skills/decisions.jsonl, so the history of adopted and rejected changes is the demo.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class GateDecision:
    candidate: str
    baseline: str
    adopt: bool
    mean_delta: float
    ci_low: float
    ci_high: float
    n: int
    note: str = ""


def paired_bootstrap(base: list[float], cand: list[float], iters: int = 5000, alpha: float = 0.05,
                     seed: int = 0) -> tuple[float, float, float]:
    if len(base) != len(cand) or not base:
        raise ValueError("need paired, non-empty score lists")
    diffs = [c - b for b, c in zip(base, cand)]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(iters))
    lo = means[int(alpha / 2 * iters)]
    hi = means[int((1 - alpha / 2) * iters) - 1]
    return sum(diffs) / n, lo, hi


def decide(baseline: str, candidate: str, base_scores: list[float], cand_scores: list[float],
           min_delta: float = 0.0) -> GateDecision:
    mean, lo, hi = paired_bootstrap(base_scores, cand_scores)
    adopt = lo > min_delta
    note = "adopt: held-out gain beyond noise" if adopt else "rollback: no confirmed held-out gain"
    return GateDecision(candidate, baseline, adopt, mean, lo, hi, len(base_scores), note)


def log(decision: GateDecision, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **asdict(decision)}) + "\n")
