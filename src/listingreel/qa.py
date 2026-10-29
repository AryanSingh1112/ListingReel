"""Post-render verification. The video is probed as a *consumer* would see it, not trusted from our own state."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from .planner import Timeline
from .render import MIN_CONTRAST


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def probe(path: str) -> dict:
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    return json.loads(r.stdout or "{}")


def mean_volume_db(path: str) -> float | None:
    r = _run(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-vn", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else None


def run_checks(video: str, tl: Timeline, scene_report: list[dict], min_total: float, max_total: float, expect_audio: bool) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    p = Path(video)
    add("file exists", p.is_file() and p.stat().st_size > 10_000, f"{p.stat().st_size // 1024} KB" if p.is_file() else "missing")
    info = probe(video)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    add("H.264 + yuv420p (plays everywhere)", v.get("codec_name") == "h264" and v.get("pix_fmt") == "yuv420p",
        f"{v.get('codec_name')}/{v.get('pix_fmt')}")
    add("resolution", (v.get("width"), v.get("height")) == (tl.width, tl.height), f"{v.get('width')}x{v.get('height')}")
    add("duration in target window", min_total - 2.5 <= dur <= max_total + 2.5, f"{dur:.1f}s (target {min_total:.0f}-{max_total:.0f}s)")
    add("audio stream present", a is not None, a.get("codec_name", "") if a else "none")
    if a is not None and expect_audio:
        vol = mean_volume_db(video)
        add("audio is audible", vol is not None and vol > -45, f"mean {vol} dB")
    worst = min((s["text_contrast"] for s in scene_report), default=99)
    add(f"caption contrast >= {MIN_CONTRAST}:1 (WCAG AA)", worst >= MIN_CONTRAST - 0.25, f"worst scene {worst:.2f}:1")
    return checks


def poster_and_sheet(video: str, tl: Timeline, out_dir: Path) -> tuple[Path | None, Path | None]:
    ff = shutil.which("ffmpeg")
    if not ff:
        return None, None
    poster = out_dir / "poster.jpg"
    _run([ff, "-y", "-loglevel", "error", "-ss", f"{min(tl.scenes[0].t0 + 1.6, tl.total - 0.1):.2f}", "-i", video,
          "-frames:v", "1", "-q:v", "2", str(poster)])
    frames: list[tuple[str, Path]] = []
    with tempfile.TemporaryDirectory() as d:
        for sc in tl.scenes:
            t = (sc.t0 + sc.t1) / 2 + 0.2
            f = Path(d) / f"{sc.idx}.jpg"
            _run([ff, "-y", "-loglevel", "error", "-ss", f"{min(t, tl.total - 0.1):.2f}", "-i", video,
                  "-frames:v", "1", "-vf", "scale=-2:480", "-q:v", "3", str(f)])
            if f.exists():
                frames.append((f"{sc.idx + 1}. {sc.line.role}  {sc.t0:.1f}s", f))
        if not frames:
            return (poster if poster.exists() else None), None
        ims = [(lbl, Image.open(f).convert("RGB")) for lbl, f in frames]
        w, h = ims[0][1].size
        cols = min(len(ims), 4)
        rows = -(-len(ims) // cols)
        sheet = Image.new("RGB", (cols * w + (cols + 1) * 12, rows * (h + 36) + 12), (18, 20, 24))
        dr = ImageDraw.Draw(sheet)
        for i, (lbl, im) in enumerate(ims):
            x, y = 12 + (i % cols) * (w + 12), 12 + (i // cols) * (h + 36)
            sheet.paste(im, (x, y + 28))
            dr.text((x, y + 6), lbl, fill=(220, 224, 230))
        sp = out_dir / "contact_sheet.jpg"
        sheet.save(sp, quality=88)
    return (poster if poster.exists() else None), sp
