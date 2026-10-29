"""Caption/overlay layout. Pillow renders each scene's text once into a cropped RGBA layer;
the compositor then animates it (slide + fade) per frame at near-zero cost."""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .models import ScriptLine

_FONT_DIRS = [
    "/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"),
    "/Library/Fonts", "/System/Library/Fonts", "C:/Windows/Fonts",
]


@dataclass
class FontSet:
    bold: str | None
    semibold: str | None
    regular: str | None


@functools.lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith((".ttf", ".otf")):
                    idx.setdefault(f.lower(), os.path.join(root, f))
    return idx


def find_fonts(override: str | None = None) -> FontSet:
    if override and os.path.isfile(override):
        return FontSet(override, override, override)
    idx = _font_index()

    def first(*names: str) -> str | None:
        return next((idx[n.lower()] for n in names if n.lower() in idx), None)

    return FontSet(
        bold=first("Poppins-Bold.ttf", "Inter-Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"),
        semibold=first("Poppins-SemiBold.ttf", "Inter-SemiBold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"),
        regular=first("Poppins-Regular.ttf", "Inter-Regular.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"),
    )


@functools.lru_cache(maxsize=64)
def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def _w(font, s: str) -> float:
    return font.getlength(s)


def _wrap(font, text: str, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if _w(font, trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(path, text, base: int, max_w: int, max_lines: int, floor: float = 0.55):
    size = base
    while True:
        f = _font(path, size)
        lines = _wrap(f, text, max_w)
        widest = max(_w(f, l) for l in lines) if lines else 0
        if (len(lines) <= max_lines and widest <= max_w) or size <= base * floor:
            return f, lines, size
        size = int(size * 0.93)


@dataclass
class TextLayer:
    x: int
    y: int
    bgr: np.ndarray      # float32 h,w,3
    alpha: np.ndarray    # float32 h,w,1

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.bgr.shape[1], self.bgr.shape[0]


def build_layer(line: ScriptLine, W: int, H: int, accent_bgr: tuple[int, int, int], fonts: FontSet) -> TextLayer:
    portrait = H / W > 1.2
    u = min(W, H) / 1080.0
    mx = int(W * (0.08 if portrait else 0.06))
    max_w = W - 2 * mx
    bottom = int(H * (0.78 if portrait else 0.88))     # keeps clear of Reels/Shorts UI chrome
    acc = (accent_bgr[2], accent_bgr[1], accent_bgr[0], 255)
    white, soft = (255, 255, 255, 255), (255, 255, 255, 225)
    dark = (18, 22, 28, 255)

    base = {"hook": 96, "price": 128, "feature": 82, "cta": 74}[line.role]
    hf, hl, hsz = _fit(fonts.bold, line.headline, int(base * u), max_w, 3 if line.role != "price" else 1)
    kf = _font(fonts.semibold, int(32 * u))
    sf = _font(fonts.regular, int(42 * u))
    sub_lines = _wrap(sf, line.subline, max_w) if (line.subline and line.role != "cta") else []

    gap1, gap2 = int(22 * u), int(20 * u)
    kh = int(32 * u) if line.kicker else 0
    lh = int(hsz * 1.14)
    pill_pad_x, pill_pad_y = int(34 * u), int(20 * u)
    pill_font = _font(fonts.semibold, int(46 * u))
    contact = line.subline if line.role == "cta" and line.subline else None
    pill_h = int(46 * u) + 2 * pill_pad_y if contact else 0

    total = kh + (gap1 if kh else 0) + lh * len(hl)
    if sub_lines:
        total += gap2 + int(42 * u * 1.3) * len(sub_lines)
    if contact:
        total += gap2 + int(8 * u) + pill_h
    y = bottom - total

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    if line.kicker:
        d.rectangle([mx, y + kh // 2 - int(2.5 * u), mx + int(64 * u), y + kh // 2 + int(2.5 * u)], fill=acc)
        x = mx + int(84 * u)
        for ch in line.kicker.upper():                  # manual letter-spacing
            d.text((x, y), ch, font=kf, fill=acc)
            x += _w(kf, ch) + 5 * u
        y += kh + gap1
    for l in hl:
        d.text((mx, y), l, font=hf, fill=acc if line.role == "price" else white)
        y += lh
    if sub_lines:
        y += gap2 - int(6 * u)
        if line.role == "hook":
            r = int(9 * u)
            d.ellipse([mx, y + int(21 * u) - r, mx + 2 * r, y + int(21 * u) + r], fill=acc)
        for l in sub_lines:
            d.text((mx + (int(34 * u) if line.role == "hook" else 0), y), l, font=sf, fill=soft)
            y += int(42 * u * 1.3)
    if contact:
        y += gap2 + int(8 * u)
        tw = _w(pill_font, contact)
        d.rounded_rectangle([mx, y, mx + tw + 2 * pill_pad_x, y + pill_h], radius=pill_h // 2, fill=acc)
        d.text((mx + pill_pad_x, y + pill_pad_y - int(4 * u)), contact, font=pill_font, fill=dark)

    # soft drop shadow for legibility on busy photos
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    a = canvas.getchannel("A").filter(ImageFilter.GaussianBlur(10 * u)).point(lambda v: int(v * 0.65))
    shadow.putalpha(a)
    out = Image.alpha_composite(shadow, canvas)
    bbox = out.getchannel("A").getbbox() or (0, 0, 8, 8)
    pad = int(24 * u)
    x0, y0 = max(0, bbox[0] - pad), max(0, bbox[1] - pad)
    x1, y1 = min(W, bbox[2] + pad), min(H, bbox[3] + pad)
    arr = np.asarray(out.crop((x0, y0, x1, y1)), dtype=np.float32)
    return TextLayer(x0, y0, np.ascontiguousarray(arr[..., 2::-1]), arr[..., 3:] / 255.0)
