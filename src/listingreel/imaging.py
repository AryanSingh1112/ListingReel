"""Image ingestion: orientation, quality scoring, near-duplicate detection, saliency-based focal point."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .errors import InputError

log = logging.getLogger(__name__)
MAX_SIDE = 2600
MIN_SIDE_REJECT = 240


@dataclass
class ImageAsset:
    name: str
    bgr: np.ndarray                      # uint8 HxWx3
    quality: float = 0.0                 # 0..1 composite
    sharpness: float = 0.0
    exposure: float = 0.0
    brightness: float = 0.5
    focal: tuple[float, float] = (0.5, 0.5)
    alt_focal: tuple[float, float] = (0.5, 0.5)   # secondary region for "detail" crops
    phash: int = 0
    synthetic: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def aspect(self) -> float:
        h, w = self.bgr.shape[:2]
        return w / h

    def summary(self) -> dict:
        h, w = self.bgr.shape[:2]
        return {
            "file": self.name, "size": f"{w}x{h}", "quality": round(self.quality, 3),
            "sharpness": round(self.sharpness, 3), "exposure": round(self.exposure, 3),
            "focal": [round(self.focal[0], 3), round(self.focal[1], 3)], "synthetic": self.synthetic,
        }


def _saliency(gray: np.ndarray) -> np.ndarray:
    """Spectral-residual saliency (Hou & Zhang 2007), implemented in numpy: no opencv-contrib needed."""
    g = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    f = np.fft.fft2(g)
    log_amp = np.log(np.abs(f) + 1e-8)
    resid = log_amp - cv2.blur(log_amp, (3, 3))
    s = np.abs(np.fft.ifft2(np.exp(resid + 1j * np.angle(f)))) ** 2
    s = cv2.GaussianBlur(s.astype(np.float32), (0, 0), 3)
    s -= s.min()
    return s / (s.max() + 1e-8)


def _focal_points(gray: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    s = _saliency(gray)
    h, w = s.shape
    # blend with a centre prior: photographers centre their subject, and it keeps crops safe
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    prior = np.exp(-(((xx / w - 0.5) ** 2) + ((yy / h - 0.5) ** 2)) / (2 * 0.28**2))
    m = s * (0.55 + 0.45 * prior)
    thr = np.percentile(m, 82)
    wgt = np.where(m >= thr, m, 0)
    tot = wgt.sum() + 1e-8
    cx, cy = float((wgt * xx).sum() / tot / w), float((wgt * yy).sum() / tot / h)
    cx = 0.5 * 0.45 + cx * 0.55
    cy = 0.5 * 0.45 + cy * 0.55
    focal = (float(np.clip(cx, 0.28, 0.72)), float(np.clip(cy, 0.28, 0.72)))
    # alt focal: most salient quadrant that does NOT contain the main focal point
    best, alt = -1.0, (0.5, 0.5)
    for qx in (0.3, 0.7):
        for qy in (0.35, 0.65):
            if abs(qx - focal[0]) < 0.2 and abs(qy - focal[1]) < 0.2:
                continue
            x0, x1 = int((qx - 0.2) * w), int((qx + 0.2) * w)
            y0, y1 = int((qy - 0.2) * h), int((qy + 0.2) * h)
            v = float(s[max(y0, 0):y1, max(x0, 0):x1].mean())
            if v > best:
                best, alt = v, (qx, qy)
    return focal, alt


def _phash(gray: np.ndarray) -> int:
    d = cv2.dct(cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32))[:8, :8]
    med = np.median(d.flatten()[1:])
    bits = (d.flatten() > med).astype(np.uint8)
    return int("".join(map(str, bits)), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def load_asset(path: Path) -> ImageAsset:
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            rgb = np.array(im.convert("RGB"))
    except Exception as e:  # corrupt / truncated / unsupported
        raise InputError(f"Cannot read image '{path.name}': {type(e).__name__}") from None
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    if min(h, w) < MIN_SIDE_REJECT:
        raise InputError(f"Image '{path.name}' is too small ({w}x{h}); need at least {MIN_SIDE_REJECT}px.")
    if max(h, w) > MAX_SIDE:
        f = MAX_SIDE / max(h, w)
        bgr = cv2.resize(bgr, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
        h, w = bgr.shape[:2]

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (512, int(512 * h / w)) if w >= h else (int(512 * w / h), 512), interpolation=cv2.INTER_AREA)
    lap = float(cv2.Laplacian(small, cv2.CV_64F).var())
    sharp = float(min(1.0, np.log1p(lap) / np.log1p(250)))
    bright = float(small.mean() / 255.0)
    exposure = float(np.clip(1 - abs(bright - 0.5) * 1.8, 0, 1))
    res = float(min(1.0, min(h, w) / 1080))
    quality = 0.45 * sharp + 0.25 * exposure + 0.30 * res

    a = ImageAsset(path.name, bgr, quality, sharp, exposure, bright)
    a.focal, a.alt_focal = _focal_points(gray)
    a.phash = _phash(gray)
    if bright < 0.07 or bright > 0.96:
        raise InputError(f"Image '{path.name}' is almost entirely black/white; skipped.")
    if sharp < 0.35:
        a.warnings.append(f"{path.name}: looks blurry (sharpness {sharp:.2f}).")
    if min(h, w) < 720:
        a.warnings.append(f"{path.name}: low resolution ({w}x{h}); it will look soft in HD output.")
    return a


def load_assets(paths: list[Path]) -> tuple[list[ImageAsset], list[str]]:
    """Load, drop unreadable & near-duplicate images; never raise for a single bad file."""
    warnings: list[str] = []
    assets: list[ImageAsset] = []
    for p in paths:
        try:
            a = load_asset(p)
        except InputError as e:
            warnings.append(e.message)
            continue
        dup = next((o for o in assets if hamming(o.phash, a.phash) <= 5), None)
        if dup is not None:
            warnings.append(f"{a.name} is a near-duplicate of {dup.name}; skipped.")
            continue
        warnings += a.warnings
        assets.append(a)
    return assets, warnings


def fallback_card(width: int, height: int, accent_bgr: tuple[int, int, int], seed: int = 0) -> ImageAsset:
    """Branded gradient used when a scene has no photo at all."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    t = (0.65 * yy / height + 0.35 * xx / width)[..., None]
    c0 = np.array([40, 32, 28], np.float32)
    c1 = np.array(accent_bgr, np.float32) * 0.55
    img = c0 * (1 - t) + c1 * t
    img += rng.normal(0, 1.2, img.shape).astype(np.float32)
    a = ImageAsset("<generated card>", np.clip(img, 0, 255).astype(np.uint8), 0.3, synthetic=True)
    a.brightness = float(img.mean() / 255)
    return a


def accent_from_image(bgr: np.ndarray) -> tuple[int, int, int]:
    """Brand-adaptive accent: the most 'colourful' hue in the hero photo, lifted for legibility on dark scrims."""
    small = cv2.resize(bgr, (96, 96), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    h, s, v = hsv[:, 0], hsv[:, 1] / 255, hsv[:, 2] / 255
    mask = (s > 0.30) & (v > 0.30)
    if mask.sum() < 40:
        hue = 88.0  # teal fallback
    else:
        w = (s * v) * np.where((h > 35) & (h < 85), 0.5, 1.0)   # lawns/trees are everywhere in listings: discount foliage green
        hist, edges = np.histogram(h[mask], bins=30, range=(0, 180), weights=w[mask])
        k = int(hist.argmax())
        hue = float((edges[k] + edges[k + 1]) / 2)
    out = cv2.cvtColor(np.uint8([[[hue, 0.50 * 255, 0.96 * 255]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(out[0]), int(out[1]), int(out[2])
