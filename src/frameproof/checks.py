"""Checks and how they are evaluated against probe observations.

A check never refers to the implementer's code (no CSS selectors, no element
ids). It refers only to what a viewer can see: visible text, colours, regions of
the frame, timing and motion. That keeps the verifier independent of the code it
judges, and means a check written from the brief works on any implementation.

Everything in this module is pure: it reads an Observation (from probe.py) and
returns CheckResults, so it can be unit-tested without a browser.
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from .brief import tokens

# ---------------------------------------------------------------- observations


@dataclass
class TextBox:
    text: str
    x: float
    y: float
    w: float
    h: float
    color: str  # "#RRGGBB"
    opacity: float  # effective, product over ancestors
    font_px: float = 0.0
    overflow: bool = False  # text wider/taller than its own block box

    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


@dataclass
class Frame:
    t: float
    texts: list[TextBox]
    png: bytes = b""
    _pixels: np.ndarray | None = field(default=None, repr=False, compare=False)

    def pixels(self) -> np.ndarray:
        if self._pixels is None:
            self._pixels = np.asarray(Image.open(io.BytesIO(self.png)).convert("RGB"), dtype=np.int16)
        return self._pixels


@dataclass
class Observation:
    width: int
    height: int
    duration: float | None
    timeline_ok: bool
    errors: list[str]
    frames: dict[float, Frame]

    def at(self, t: float) -> Frame:
        if t in self.frames:
            return self.frames[t]
        nearest = min(self.frames, key=lambda k: abs(k - t))
        return self.frames[nearest]


# ---------------------------------------------------------------------- checks

GENERIC_KINDS = {"renders", "duration", "no_overflow", "in_frame", "no_overlap", "no_unexpected_text"}
BRIEF_KINDS = {"text_visible", "text_hidden", "text_color", "text_region", "bg_color", "palette",
               "motion", "still", "order"}
KINDS = GENERIC_KINDS | BRIEF_KINDS


@dataclass
class Check:
    id: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    severity: str = "hard"  # hard: any failure blocks. soft: counts toward the score.
    source: str = ""  # the brief phrase this check came from (for the evidence report)
    hidden: bool = False  # held back from the implementer's feedback
    origin: list[str] = field(default_factory=list)  # checker models that proposed it

    def times(self) -> list[float]:
        p = self.params
        out = [p[k] for k in ("at", "t0", "t1") if k in p]
        out += list(p.get("at_list", []))
        return [float(t) for t in out]

    @staticmethod
    def from_dict(d: dict) -> "Check":
        d = dict(d)
        params = d.pop("params", None)
        if params is None:  # allow flat dicts from the LLM
            params = {k: d.pop(k) for k in list(d) if k not in {"id", "kind", "severity", "source", "hidden", "origin"}}
        if d.get("kind") not in KINDS:
            raise ValueError(f"unknown check kind {d.get('kind')!r}")
        return Check(params=params, **d)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    id: str
    kind: str
    passed: bool
    severity: str
    detail: str
    hidden: bool = False


# --------------------------------------------------------------------- helpers


def norm(s: str) -> str:
    return " ".join(tokens(s))


def hex_to_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)


def color_dist(a: str, b: str) -> float:
    return float(np.linalg.norm(hex_to_rgb(a) - hex_to_rgb(b)))


def visible(tb: TextBox, W: int, H: int, min_opacity: float = 0.5) -> bool:
    if tb.opacity < min_opacity or tb.w <= 0 or tb.h <= 0:
        return False
    return tb.x < W and tb.y < H and tb.x + tb.w > 0 and tb.y + tb.h > 0


def find_text(frame: Frame, needle: str, W: int, H: int, min_opacity: float = 0.5) -> list[TextBox]:
    """Visible boxes whose text contains the needle. Falls back to joining all
    visible text, so a phrase split across spans (per-word animation) still matches."""
    n = norm(needle)
    vis = [tb for tb in frame.texts if visible(tb, W, H, min_opacity)]
    hits = [tb for tb in vis if n and n in norm(tb.text)]
    if hits:
        return hits
    joined = norm(" ".join(tb.text for tb in sorted(vis, key=lambda b: (round(b.y / 20), b.x))))
    if n and n in joined:
        parts = set(tokens(needle))
        return [tb for tb in vis if set(tokens(tb.text)) & parts]
    return []


REGIONS = {
    "left": lambda cx, cy: cx < 0.45, "right": lambda cx, cy: cx > 0.55,
    "top": lambda cx, cy: cy < 0.45, "bottom": lambda cx, cy: cy > 0.55,
    "center": lambda cx, cy: 0.3 < cx < 0.7 and 0.3 < cy < 0.7,
    "top-left": lambda cx, cy: cx < 0.5 and cy < 0.5, "top-right": lambda cx, cy: cx > 0.5 and cy < 0.5,
    "bottom-left": lambda cx, cy: cx < 0.5 and cy > 0.5, "bottom-right": lambda cx, cy: cx > 0.5 and cy > 0.5,
}


def frame_change(a: Frame, b: Frame) -> float:
    """Mean absolute pixel difference, 0..1."""
    pa, pb = a.pixels(), b.pixels()
    if pa.shape != pb.shape:
        return 1.0
    return float(np.abs(pa - pb).mean() / 255.0)


def color_fraction(frame: Frame, colors: list[str], tol: float) -> float:
    px = frame.pixels().reshape(-1, 3)[::7]  # subsample for speed
    best = np.full(len(px), np.inf)
    for c in colors:
        best = np.minimum(best, np.linalg.norm(px - hex_to_rgb(c), axis=1))
    return float((best <= tol).mean())


# ------------------------------------------------------------------ evaluation


def evaluate(check: Check, obs: Observation, vocabulary: set[str] | None = None) -> CheckResult:
    try:
        ok, detail = _EVAL[check.kind](check.params, obs, vocabulary or set())
    except Exception as e:  # a check that cannot run is a failed check, with the reason
        ok, detail = False, f"error: {type(e).__name__}: {e}"
    return CheckResult(check.id, check.kind, bool(ok), check.severity, detail, check.hidden)


def _renders(p, obs, _):
    if obs.errors:
        return False, f"page errors: {obs.errors[:3]}"
    if not obs.timeline_ok:
        return False, "no GSAP timeline registered under the composition id"
    if not obs.frames:
        return False, "no frames captured"
    return True, f"{obs.width}x{obs.height}, {len(obs.frames)} frames"


def _duration(p, obs, _):
    want, tol = float(p["expected"]), float(p.get("tol", 1.0))
    if obs.duration is None:
        return False, "root has no data-duration"
    return abs(obs.duration - want) <= tol, f"duration {obs.duration}s, brief {want}s (±{tol})"


def _no_overflow(p, obs, _):
    bad = [(t, tb.text[:30]) for t, f in obs.frames.items() for tb in f.texts
           if tb.overflow and visible(tb, obs.width, obs.height)]
    return not bad, f"overflowing text at {bad[:3]}" if bad else "no text overflows its box"


def _in_frame(p, obs, _):
    m = float(p.get("margin", 0.0))
    W, H = obs.width, obs.height
    bad = []
    for t, f in obs.frames.items():
        for tb in f.texts:
            if not visible(tb, W, H):
                continue
            if tb.x < m or tb.y < m or tb.x + tb.w > W - m or tb.y + tb.h > H - m:
                bad.append((t, tb.text[:30]))
    return not bad, f"text cut by frame edge at {bad[:3]}" if bad else f"all visible text inside frame (margin {m}px)"


def _no_overlap(p, obs, _):
    min_iou = float(p.get("min_iou", 0.15))
    bad = []
    for t, f in obs.frames.items():
        vis = [tb for tb in f.texts if visible(tb, obs.width, obs.height, 0.8)]
        for i, a in enumerate(vis):
            for b in vis[i + 1 :]:
                ix = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
                iy = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
                inter = ix * iy
                small = min(a.w * a.h, b.w * b.h) or 1
                if inter / small > min_iou:
                    bad.append((t, a.text[:20], b.text[:20]))
    return not bad, f"overlapping text {bad[:3]}" if bad else "no overlapping text"


def _no_unexpected_text(p, obs, vocab):
    """Anti-gaming: visible words that the brief never mentions. Catches text aimed
    at a judge ("this meets all requirements") as well as hallucinated copy."""
    allowed = vocab | set(tokens(" ".join(p.get("allow", []))))
    max_frac = float(p.get("max_fraction", 0.5))  # per text box
    min_words = int(p.get("min_words", 2))
    bad: dict[str, float] = {}
    for f in obs.frames.values():
        for tb in f.texts:
            if not visible(tb, obs.width, obs.height):
                continue
            words = [w for w in tokens(tb.text) if not w.isdigit() and len(w) > 2]
            extra = [w for w in words if w not in allowed]
            if len(extra) >= min_words and len(extra) / len(words) > max_frac:
                bad.setdefault(tb.text[:60], f.t)
    if bad:
        return False, f"on-screen text not from the brief: {list(bad.items())[:3]}"
    return True, "all visible copy traces to the brief"


def _text_visible(p, obs, _):
    f = obs.at(float(p["at"]))
    hits = find_text(f, p["text"], obs.width, obs.height)
    return bool(hits), f"'{p['text']}' {'visible' if hits else 'not visible'} at {f.t}s"


def _text_hidden(p, obs, _):
    f = obs.at(float(p["at"]))
    hits = find_text(f, p["text"], obs.width, obs.height, min_opacity=0.2)
    return not hits, f"'{p['text']}' {'still visible' if hits else 'hidden'} at {f.t}s"


def _text_color(p, obs, _):
    f = obs.at(float(p["at"]))
    hits = find_text(f, p["text"], obs.width, obs.height)
    if not hits:
        return False, f"'{p['text']}' not visible at {f.t}s"
    tol = float(p.get("tol", 60))
    d = min(color_dist(tb.color, p["color"]) for tb in hits)
    return d <= tol, f"'{p['text']}' colour {hits[0].color} vs {p['color']} (dist {d:.0f}, tol {tol})"


def _text_region(p, obs, _):
    f = obs.at(float(p["at"]))
    hits = find_text(f, p["text"], obs.width, obs.height)
    if not hits:
        return False, f"'{p['text']}' not visible at {f.t}s"
    test = REGIONS[p["region"]]
    cx, cy = hits[0].center()
    rx, ry = cx / obs.width, cy / obs.height
    return test(rx, ry), f"'{p['text']}' centre at ({rx:.2f}, {ry:.2f}), want {p['region']}"


def _bg_color(p, obs, _):
    f = obs.at(float(p["at"]))
    frac = color_fraction(f, [p["color"]], float(p.get("tol", 40)))
    need = float(p.get("min_fraction", 0.3))
    return frac >= need, f"{frac:.0%} of pixels near {p['color']} at {f.t}s (need {need:.0%})"


def _palette(p, obs, _):
    need, tol = float(p.get("min_fraction", 0.6)), float(p.get("tol", 40))
    times = p.get("at_list") or sorted(obs.frames)
    fracs = [color_fraction(obs.at(float(t)), p["colors"], tol) for t in times]
    worst = min(fracs)
    return worst >= need, f"palette coverage min {worst:.0%} over {len(fracs)} frames (need {need:.0%})"


def _motion(p, obs, _):
    a, b = obs.at(float(p["t0"])), obs.at(float(p["t1"]))
    ch, need = frame_change(a, b), float(p.get("min_change", 0.01))
    return ch >= need, f"change {a.t}s->{b.t}s = {ch:.3f} (need >= {need})"


def _still(p, obs, _):
    a, b = obs.at(float(p["t0"])), obs.at(float(p["t1"]))
    ch, cap = frame_change(a, b), float(p.get("max_change", 0.01))
    return ch <= cap, f"change {a.t}s->{b.t}s = {ch:.3f} (max {cap})"


def _order(p, obs, _):
    def first_seen(text):
        for t in sorted(obs.frames):
            if find_text(obs.frames[t], text, obs.width, obs.height):
                return t
        return None

    a, b = first_seen(p["first"]), first_seen(p["then"])
    if a is None or b is None:
        return False, f"first seen: '{p['first']}'={a}, '{p['then']}'={b}"
    return a < b, f"'{p['first']}' at {a}s, '{p['then']}' at {b}s"


_EVAL = {
    "renders": _renders, "duration": _duration, "no_overflow": _no_overflow, "in_frame": _in_frame,
    "no_overlap": _no_overlap, "no_unexpected_text": _no_unexpected_text,
    "text_visible": _text_visible, "text_hidden": _text_hidden, "text_color": _text_color,
    "text_region": _text_region, "bg_color": _bg_color, "palette": _palette,
    "motion": _motion, "still": _still, "order": _order,
}
assert set(_EVAL) == KINDS

# Spec handed to the checker model (compile.py). Keep in sync with _EVAL.
KIND_SPEC = """\
text_visible   {text, at}                      the exact on-screen words are visible at time `at`
text_hidden    {text, at}                      the words are NOT visible yet / any more at `at`
text_color     {text, at, color, tol?=60}      visible words have this text colour (#RRGGBB)
text_region    {text, at, region}              region: left right top bottom center top-left top-right bottom-left bottom-right
bg_color       {at, color, tol?=40, min_fraction?=0.3}   at least this share of the frame is near the colour
palette        {colors, at_list?, min_fraction?=0.6}     frames are mostly made of the brief's palette
motion         {t0, t1, min_change?=0.01}      something visibly moves/changes between t0 and t1
still          {t0, t1, max_change?=0.01}      the frame holds still between t0 and t1 (e.g. a static end card)
order          {first, then}                   text `first` appears before text `then`"""


def generic_checks(duration: float | None, margin: float = 24.0) -> list[Check]:
    out = [
        Check("g-renders", "renders", source="every composition must render"),
        Check("g-no-overflow", "no_overflow", source="craft: no clipped text"),
        Check("g-in-frame", "in_frame", {"margin": margin}, source="craft: text inside the safe area"),
        Check("g-no-overlap", "no_overlap", severity="soft", source="composition: text does not collide"),
        Check("g-no-unexpected-text", "no_unexpected_text", source="prompt intent: only copy from the brief"),
    ]
    if duration:
        out.append(Check("g-duration", "duration", {"expected": duration, "tol": max(0.5, 0.15 * duration)},
                         source="brief duration"))
    return out


