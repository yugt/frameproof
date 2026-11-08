"""Prepare user-provided visual references for model input and run reports."""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

MAX_BYTES = 50 * 1024 * 1024


def prepare(data: bytes, mime: str, run_dir: Path, mode: str = "inspiration") -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Reference must be nonempty and at most 50 MB")
    if mode not in {"inspiration", "asset"}:
        raise ValueError("Reference mode must be inspiration or asset")
    folder = run_dir / "reference"
    folder.mkdir(parents=True, exist_ok=True)
    if mime.startswith("image/"):
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
            im.load()
            im.thumbnail((1920, 1920))
            im.convert("RGBA" if "A" in im.getbands() else "RGB").save(folder / "image.png")
        except Exception as exc:
            raise ValueError("Could not decode reference image") from exc
        result = {"kind": "image", "mode": mode, "frames": [str(folder / "image.png")],
                  "times": [0.0], "asset": str(folder / "image.png")}
    elif mime.startswith("video/"):
        if mode == "asset":
            raise ValueError("Video as an embedded asset is not supported yet; use inspiration mode")
        source = folder / "source-video"
        source.write_bytes(data)
        try:
            info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
                                  check=True, capture_output=True, text=True, timeout=30)
            duration = float(json.loads(info.stdout)["format"]["duration"])
            if not 0 < duration <= 600:
                raise ValueError("Video must be between 0 and 600 seconds")
            times = [round(duration * f, 3) for f in (0.05, 0.35, 0.65, 0.95)]
            frames = []
            for i, t in enumerate(times):
                path = folder / f"frame-{i + 1}.png"
                subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(source), "-frames:v", "1",
                                "-vf", "scale=960:960:force_original_aspect_ratio=decrease", "-y", str(path)],
                               check=True, capture_output=True, timeout=30)
                frames.append(str(path))
        except (subprocess.SubprocessError, OSError, KeyError, ValueError) as exc:
            raise ValueError(f"Could not sample reference video: {exc}") from exc
        result = {"kind": "video", "mode": mode, "frames": frames, "times": times, "duration": duration}
    else:
        raise ValueError(f"Unsupported reference type: {mime}. Attach an image or video.")
    (folder / "reference.json").write_text(json.dumps(result, indent=2))
    return result


def images(reference: dict | None) -> list[bytes]:
    return [Path(p).read_bytes() for p in (reference or {}).get("frames", [])]
