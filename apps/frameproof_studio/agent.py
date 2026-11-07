"""`adk web apps` entry point: exposes the studio graph as root_agent.

ADK's web loader lists a folder only if it has agent.py (or root_agent.yaml), so
the export lives here; __init__.py imports it, the standard ADK layout.

First message: an example folder (examples/tidepool-launch), briefs.json#id, a JSON
brief, or the brief text. When the desk pauses, answer {"approved": true} once the
render it names is ready (or run `python -m agent.run` to have it answered for you).
"""

import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT / "src", _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# This file may itself be imported as a module named "agent"; load the repo's
# agent package explicitly so the two names never shadow each other.
_pkg = sys.modules.get("agent")
if _pkg is not None and Path(getattr(_pkg, "__file__", "") or "").resolve() == Path(__file__).resolve():
    del sys.modules["agent"]
root_agent = importlib.import_module("agent.graph").root_agent

__all__ = ["root_agent"]
