"""Design briefs. A brief is prose plus a few parsed fields the generic checks use.

Sources:
  - briefs/*.json written for this project (list of {"id", "category", "text", ...})
  - the public Code2Video split (Kaggle: heygen/code2video, CC BY 4.0) once downloaded
    into data/kaggle/; `load_jsonl` reads one brief per line with the same keys.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
_DURATION = re.compile(r"(?:~|about|approx\.?|duration[:\s]*)\s*(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b", re.I)
_SCENE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s)?\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*s", re.I)


@dataclass
class Brief:
    id: str
    text: str
    category: str = ""
    split: str = "train"  # train | test (held out from the self-improvement loop)
    duration: float | None = None
    palette: list[str] = field(default_factory=list)
    scenes: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.palette:
            self.palette = sorted({h.upper() for h in _HEX.findall(self.text)})
        if self.duration is None:
            m = _DURATION.search(self.text)
            if m:
                self.duration = float(m.group(1))
        if not self.scenes:
            self.scenes = [(float(a), float(b)) for a, b in _SCENE.findall(self.text) if float(b) > float(a)]
        if self.duration is None and self.scenes:
            self.duration = max(b for _, b in self.scenes)

    def vocabulary(self) -> set[str]:
        return set(tokens(self.text))


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.casefold())


def load(path: str | Path) -> list[Brief]:
    path = Path(path)
    if path.suffix == ".jsonl":
        return load_jsonl(path)
    data = json.loads(path.read_text())
    items = data if isinstance(data, list) else [data]
    return [_from_dict(d) for d in items]


def load_jsonl(path: Path) -> list[Brief]:
    return [_from_dict(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def _from_dict(d: dict) -> Brief:
    known = {"id", "text", "category", "split", "duration", "palette", "scenes"}
    kwargs = {k: v for k, v in d.items() if k in known}
    if "text" not in kwargs:  # tolerate other field names in external sets
        kwargs["text"] = d.get("brief") or d.get("prompt") or ""
    if "scenes" in kwargs:
        kwargs["scenes"] = [tuple(s) for s in kwargs["scenes"]]
    return Brief(**kwargs)
