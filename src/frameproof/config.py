"""Settings. Every value can be overridden with an environment variable.

Model strings are "<provider>:<model>". Providers (see PROVIDERS below):
  anthropic  Claude via the Anthropic SDK (ANTHROPIC_API_KEY or an `ant auth login` profile)
  openai     OpenAI, or any OpenAI-compatible endpoint via FP_OPENAI_BASE_URL
  google     Gemini, OpenAI-compatible endpoint     (GOOGLE_API_KEY or GEMINI_API_KEY)
  meta       Llama API, OpenAI-compatible endpoint  (LLAMA_API_KEY)
  subq       Subquadratic (SUBQ_API_KEY). Snap models are decision models
             (POST /systemone, probabilities, no text): judge or check voter only.
Base URLs can be overridden with FP_<PROVIDER>_BASE_URL. Run `frameproof models`
to see which models each key can reach.

Keys may live in a gitignored `secrets.env` (KEY=value lines) at the repo root;
it is loaded at import and never overrides variables already set.

Independence rule: the checkers and the judge should come from model families
other than the implementer's. `cli.py` warns when they overlap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRETS_FILE = ROOT / "secrets.env"


def load_secrets(path: Path = SECRETS_FILE) -> list[str]:
    """Load KEY=value lines into os.environ (without overriding). Returns names loaded."""
    loaded = []
    if not path.exists():
        return loaded
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded


load_secrets()


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str | None  # None = SDK default
    key_envs: tuple[str, ...]
    family: str  # used for the independence warning

    def url(self) -> str | None:
        return os.environ.get(f"FP_{self.name.upper()}_BASE_URL", self.base_url)

    def key(self) -> str | None:
        for k in self.key_envs:
            if os.environ.get(k):
                return os.environ[k]
        return None


# Base URLs from vendor/gateway docs as of 2026-11-07; confirm with `frameproof models`.
PROVIDERS: dict[str, Provider] = {
    "openai": Provider("openai", None, ("OPENAI_API_KEY",), "gpt"),
    "google": Provider("google", "https://generativelanguage.googleapis.com/v1beta/openai/",
                       ("GOOGLE_API_KEY", "GEMINI_API_KEY"), "gemini"),
    "meta": Provider("meta", "https://api.llama.com/compat/v1/", ("LLAMA_API_KEY", "META_API_KEY"), "llama"),
    "subq": Provider("subq", "https://api.subq.ai/v1", ("SUBQ_API_KEY",), "subq"),
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _list(name: str, fallback: str = "") -> list[str]:
    raw = os.environ.get(name) or os.environ.get(fallback, "")
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass(frozen=True)
class Settings:
    # Who writes the video code (the system under test).
    implementer_model: str = field(default_factory=lambda: _env("FP_IMPLEMENTER_MODEL", "anthropic:claude-opus-5-5"))
    # Who turn the brief into checks (a panel). Never see the implementer's code.
    # FP_CHECKER_MODELS="meta:<id>,google:<id>"; FP_CHECKER_MODEL also accepted.
    checker_models: list[str] = field(default_factory=lambda: _list("FP_CHECKER_MODELS", "FP_CHECKER_MODEL"))
    # Who scores taste (engagement, craft, ...) from frames only.
    judge_model: str = field(default_factory=lambda: _env("FP_JUDGE_MODEL", ""))

    # Checker panel: a check proposed by at least this many checkers becomes hard,
    # otherwise soft. With one checker, the checker's own severity is kept.
    agree_min: int = int(_env("FP_AGREE_MIN", "2"))
    # Decision models in the panel (subq:subq-snap-*) vote instead of proposing: a vote
    # of P(brief asks for it) >= vote_min counts as one more checker backing the check.
    vote_min: float = float(_env("FP_VOTE_MIN", "0.5"))

    # Probe
    sample_step: float = float(_env("FP_SAMPLE_STEP", "0.25"))  # seconds between generic-check samples
    timeline_wait_s: float = float(_env("FP_TIMELINE_WAIT", "8"))
    offline: bool = _env("FP_OFFLINE", "0") == "1"  # block all network except vendored GSAP

    # Verdict policy
    accept_threshold: float = float(_env("FP_ACCEPT", "0.80"))
    judge_weight: float = float(_env("FP_JUDGE_WEIGHT", "0.3"))  # 0 = checks only
    hidden_fraction: float = float(_env("FP_HIDDEN_FRACTION", "0.3"))  # checks held back from implementer

    runs_dir: Path = field(default_factory=lambda: Path(_env("FP_RUNS", str(ROOT / "runs"))))

    @property
    def checker_model(self) -> str:
        return self.checker_models[0] if self.checker_models else ""


SETTINGS = Settings()

# Local GSAP copy (npm i gsap) so renders do not depend on the CDN.
VENDORED_GSAP = ROOT / "node_modules" / "gsap" / "dist" / "gsap.min.js"
