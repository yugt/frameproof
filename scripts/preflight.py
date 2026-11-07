"""Preflight: every line is a real probe of this machine, not a checklist.

    python scripts/preflight.py            # everything, including one /models call per key
    python scripts/preflight.py --offline  # skip network calls

Exit code 0 when nothing required is red. Keys and model roles are reported, never printed.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src"):
    sys.path.insert(0, str(p))

OK, BAD, INFO = "✓", "✗", "-"
results: list[tuple[str, str, bool]] = []  # (mark, text, required)


def line(mark: str, text: str, required: bool = True) -> None:
    results.append((mark, text, required))
    print(f"  {mark} {text}", flush=True)


def probe(text: str, fn, required: bool = True) -> None:
    try:
        detail = fn()
        line(OK, f"{text}{' (' + detail + ')' if detail else ''}", required)
    except Exception as e:
        line(BAD, f"{text}: {type(e).__name__}: {str(e)[:160]}", required)


def run(cmd: list[str], timeout: int = 60, cwd: Path = ROOT) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout) else f"exit {p.returncode}")
    return p.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    print("frameproof preflight\n")

    print("runtime")
    probe("python >= 3.11", lambda: (sys.version.split()[0] if sys.version_info >= (3, 11) else (_ for _ in ()).throw(RuntimeError(sys.version))))
    for pkg in ("google-adk", "playwright", "anthropic", "openai", "numpy", "pillow"):
        probe(f"{pkg} installed", lambda pkg=pkg: md.version(pkg))
    probe("frameproof imports", lambda: __import__("frameproof").__version__)

    def chromium():
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            b = pw.chromium.launch()
            v = b.version
            b.close()
        return v
    probe("headless Chromium launches", chromium)
    probe("ffmpeg on PATH", lambda: run(["ffmpeg", "-version"]).splitlines()[0].split(" Copyright")[0])
    probe("node on PATH", lambda: run(["node", "--version"]))
    probe("HyperFrames CLI (npx --no-install)", lambda: run(["npx", "--no-install", "hyperframes", "--version"], 120).splitlines()[-1])
    from frameproof.config import VENDORED_GSAP

    probe("GSAP vendored (renders do not need the CDN)", lambda: "node_modules/gsap" if VENDORED_GSAP.exists() else (_ for _ in ()).throw(FileNotFoundError(VENDORED_GSAP)), required=False)

    print("\nstudio")

    def graph():
        # The loader `adk web` uses (it lists a folder only if it has agent.py), not the plain one.
        from google.adk.cli.utils._nested_agent_loader import NestedAgentLoader

        loader = NestedAgentLoader(str(ROOT / "apps"))
        if "frameproof_studio" not in loader.list_agents():
            raise RuntimeError(f"adk web would list {loader.list_agents()} (needs apps/frameproof_studio/agent.py)")
        wf = loader.load_agent("frameproof_studio")
        nodes = sorted({getattr(n, "name", "?") for e in wf.graph.edges for n in (e.from_node, e.to_node)}) if wf.graph else []
        return f"{type(wf).__name__}, {len(wf.graph.edges) if wf.graph else '?'} edges: {', '.join(nodes)}"
    probe("apps/frameproof_studio loads in ADK (adk web apps)", graph)
    ex = ROOT / "examples" / "tidepool-launch"
    probe("replay example present", lambda: ", ".join(f for f in ("brief.json", "checks.json", "index.html") if (ex / f).exists()))

    def writable():
        from frameproof.config import SETTINGS

        SETTINGS.runs_dir.mkdir(parents=True, exist_ok=True)
        t = SETTINGS.runs_dir / ".preflight"
        t.write_text("ok")
        t.unlink()
        return str(SETTINGS.runs_dir)
    probe("runs/ writable", writable)

    print("\nmodels (no key values are printed)")
    from frameproof import llm
    from frameproof.config import PROVIDERS, SECRETS_FILE, SETTINGS

    line(OK if SECRETS_FILE.exists() else INFO, f"secrets.env {'found' if SECRETS_FILE.exists() else 'missing (cp secrets.env.example secrets.env)'}", False)
    for name, prov in PROVIDERS.items():
        has = bool(prov.key())
        line(OK if has else INFO, f"{name}: key {'set' if has else 'not set'}", False)
    anth = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    line(OK if anth else INFO, f"anthropic: key {'set' if anth else 'not set (or use an `ant auth login` profile)'}", False)

    def role(spec: str) -> str:
        if not spec or "<" in spec:
            return "not set"
        return f"{spec} [{llm.family(spec)}]"
    imp = SETTINGS.implementer_model
    line(INFO, f"implementer: {role(imp)}", False)
    for m in SETTINGS.checker_models:
        same = m and "<" not in m and llm.family(m) == llm.family(imp)
        line(BAD if same else INFO, f"checker: {role(m)}{' - same family as implementer' if same else ''}"
             + (" (decision model: votes)" if m and llm.is_decision(m) else ""), False)
    if not SETTINGS.checker_models:
        line(INFO, "checkers: none -> the studio uses an example's checks.json (fixture mode)", False)
    line(INFO, f"judge: {role(SETTINGS.judge_model)}", False)
    line(INFO, f"final render: {'HyperFrames (FP_REAL_RENDER=1)' if os.environ.get('FP_REAL_RENDER') == '1' else 'stand-in probe preview (FP_REAL_RENDER=0)'}", False)

    if not a.offline:
        for name, prov in PROVIDERS.items():
            if prov.key():
                probe(f"{name} answers /models", lambda name=name: f"{len(llm.list_models(name))} models", required=False)

    red = [t for m, t, req in results if m == BAD and req]
    warn = [t for m, t, req in results if m == BAD and not req]
    print()
    if red:
        print(f"PREFLIGHT RED ({len(red)} required probe(s) failed)")
        return 1
    print("PREFLIGHT GREEN" + (f" ({len(warn)} warning(s))" if warn else ""))
    print("\nNext:  python -m agent.run examples/tidepool-launch      # or: adk web apps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
