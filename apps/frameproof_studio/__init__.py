"""`adk web apps` entry point: exposes the studio graph as root_agent.

First message: an example folder (examples/tidepool-launch), briefs.json#id, a JSON
brief, or the brief text. When the desk pauses, answer {"approved": true} once the
render it names is ready (or run `python -m agent.run` to have it answered for you).
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT, _ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agent.graph import root_agent  # noqa: E402

__all__ = ["root_agent"]
