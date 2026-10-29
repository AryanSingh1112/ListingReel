"""Frame compositor + encoder.

Frames are produced in numpy/OpenCV (sub-pixel Ken Burns via affine warp, adaptive scrim, animated text,
custom transitions) and streamed to ffmpeg over a pipe: no temp frames on disk, one deterministic pass,
and total control over every pixel (no fragile ffmpeg filtergraph strings, no moviepy).
"""
from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from .errors import DependencyError, RenderError
from .planner import SceneClip, Timeline, cover_window
from .text_overlay import FontSet, TextLayer, build_layer

log = logging.getLogger(__name__)
SRC_TARGET_C = 1 / 1.25     # source px per output px after pre-scaling (keeps warps crisp, memory small)
MIN_CONTRAST = 4.5          # WCAG AA for normal text


def ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 0.5 - 0.5 * math.cos(math.pi * p)


def _rel_lum(bgr: np.ndarray) -> np.ndarray:
    c = bgr[..., ::-1].astype(np.float32) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)


# --------------------------------------------------------------------------------------- scenes
class SceneRenderer:
    def __init__(self, sc: SceneClip, W: int, H: int, accent: tuple[int, int, int], fonts: FontSet):
        self.sc, self.W, self.H = sc, W, H
        src = sc.asset.bgr
        sh, sw = src.shape[:2]
        c = max(W / sw, H / sh)
        if c < SRC_TARGET_C:
            f = c / SRC_TARGET_C
            src = cv2.resize(src, (max(W, int(sw * f)), max(H, int(sh * f))), interpolation=cv2.INTER_AREA)
        self.src = src
        self.layer: TextLayer = build_layer(sc.line, W, H, accent, fonts)
        txt_rgb = accent if sc.line.role == "price" else (255, 255, 255)
        self.text_lum = float(_rel_lum(np.uint8([[txt_rgb]]))[0, 0])
        self.scrim_strength, self.contrast = self._tune_scrim()

    # -- geometry
    def raw(self, p: float) -> np.ndarray:
        m = self.sc.motion
        e = ease(p)
        z = m.z0 + (m.z1 - m.z0) * e
        cx = m.c0[0] + (m.c1[0] - m.c0[0]) * e
        cy = m.c0[1] + (m.c1[1] - m.c0[1]) * e
        sh, sw = self.src.shape[:2]
        k, ww, wh = cover_window(sw, sh, self.W, self.H, z)
        x0 = min(max(cx * sw - ww / 2, 0), sw - ww)
        y0 = min(max(cy * sh - wh / 2, 0), sh - wh)
        M = np.float32([[k, 0, -k * x0], [0, k, -k * y0]])
        return cv2.warpAffine(self.src, M, (self.W, self.H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)

    # -- scrim: darkens from above the text block downwards + gentle vignette
    def _light_map(self, strength: float) -> np.ndarray:
        W, H = self.W, self.H
        _, ty, _, th = self.layer.bbox
        y = np.arange(H, dtype=np.float32)
        ramp_start, ramp_end = ty - 0.20 * H, ty + 0.02 * H
        t = np.clip((y - ramp_start) / max(ramp_end - ramp_start, 1), 0, 1)
        scrim = strength * (t * t * (3 - 2 * t))
        top = 0.30 * np.clip(1 - y / (0.14 * H), 0, 1) ** 2        # light top shade for status-bar legibility
        a = np.maximum(scrim, top)[:, None]
        xx = (np.arange(W, dtype=np.float32) / W - 0.5)[None, :]
        vig = 1 - 0.22 * np.clip((xx**2 + ((y / H - 0.5) ** 2)[:, None]) * 2.2, 0, 1)
        rng = np.random.default_rng(7)
        L = (1 - a) * vig * 0.94 + rng.normal(0, 0.0025, (H, W)).astype(np.float32)   # dither vs banding
        return np.clip(L, 0, 1).astype(np.float32)[..., None]

    def _tune_scrim(self) -> tuple[float, float]:
        x, y, w, h = self.layer.bbox
        samples = [self.raw(p)[y:y + h, x:x + w] for p in (0.0, 0.5, 1.0)]
        best_s, best_c = 0.88, 0.0
        for s in (0.45, 0.55, 0.65, 0.75, 0.85, 0.92):
            L = self._light_map(s)[y:y + h, x:x + w]
            lum = max(float(np.percentile(_rel_lum(sm * L), 92)) for sm in samples)
            contrast = (max(self.text_lum, 0.0) + 0.05) / (lum + 0.05)
            best_s, best_c = s, contrast
            if contrast >= MIN_CONTRAST:
                break
        self.L = self._light_map(best_s)
        return best_s, best_c

    def base(self, p: float) -> np.ndarray:
        return (self.raw(p).astype(np.float32) * self.L).astype(np.uint8)

    def text_params(self, t: float, tl: Timeline) -> tuple[float, float]:
        sc, tr = self.sc, tl.transition
        first, last = sc.idx == 0, sc.idx == len(tl.scenes) - 1
        t_in = sc.t0 + (0.30 if first else tr / 2 + 0.05)
        a_in = ease((t - t_in) / 0.5)
        a_out = 1.0 if last else 1 - ease((t - (sc.t1 - tr / 2 - 0.28)) / 0.24)
        return a_in * a_out, (1 - a_in) * 0.035 * self.H

    def frame(self, t: float, tl: Timeline) -> np.ndarray:
        sc = self.sc
        span = max(sc.s1 - sc.s0, 1e-6)
        f = self.base((t - sc.s0) / span)
        a, dy = self.text_params(t, tl)
        if a > 0.003:
            blend_text(f, self.layer, a, dy)
        return f


def blend_text(frame: np.ndarray, layer: TextLayer, a: float, dy: float) -> None:
    H, W = frame.shape[:2]
    x, y, w, h = layer.bbox
    y += int(dy)
    ys, ye = max(y, 0), min(y + h, H)
    xs, xe = max(x, 0), min(x + w, W)
    if ye <= ys or xe <= xs:
        return
    sub_a = layer.alpha[ys - y:ye - y, xs - x:xe - x] * a
    sub_c = layer.bgr[ys - y:ye - y, xs - x:xe - x]
    reg = frame[ys:ye, xs:xe].astype(np.float32)
    frame[ys:ye, xs:xe] = (reg * (1 - sub_a) + sub_c * sub_a).astype(np.uint8)


# ---------------------------------------------------------------------------------- transitions
def _scale(img: np.ndarray, s: float) -> np.ndarray:
    H, W = img.shape[:2]
    M = np.float32([[s, 0, W / 2 * (1 - s)], [0, s, H / 2 * (1 - s)]])
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def transition(kind: str, A: np.ndarray, B: np.ndarray, p: float) -> np.ndarray:
    p = ease(p)
    H, W = A.shape[:2]
    if kind == "slide":
        out = np.empty_like(A)
        if H >= W:
            off = int(round(p * H))
            out[: H - off] = A[off:]
            out[H - off:] = B[:off]
        else:
            off = int(round(p * W))
            out[:, : W - off] = A[:, off:]
            out[:, W - off:] = B[:, :off]
        return out
    if kind == "zoom":
        return cv2.addWeighted(_scale(A, 1 + 0.18 * p), 1 - p, _scale(B, 1.10 - 0.10 * p), p, 0)
    if kind == "dip":
        return (A * (1 - 2 * p)).astype(np.uint8) if p < 0.5 else (B * (2 * p - 1)).astype(np.uint8)
    return cv2.addWeighted(A, 1 - p, B, p, 0)  # crossfade


# -------------------------------------------------------------------------------------- encoder
def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise DependencyError("ffmpeg not found on PATH.", "install ffmpeg (apt install ffmpeg / brew install ffmpeg)")
    return exe


@dataclass
class RenderResult:
    path: str
    frames: int
    seconds: float
    scenes: list[dict]


def render(
    tl: Timeline, audio_wav: str, out_path: str, fonts: FontSet, crf: int = 20,
    progress: Callable[[int, int], None] | None = None,
    encoder_preset: str = "medium",
) -> RenderResult:
    W, H, fps = tl.width, tl.height, tl.fps
    t_start = time.time()
    renderers = [SceneRenderer(sc, W, H, tl.accent_bgr, fonts) for sc in tl.scenes]
    scene_report = [
        {"scene": i, "role": r.sc.line.role, "image": r.sc.asset.name, "motion": r.sc.motion.kind,
         "scrim": round(r.scrim_strength, 2), "text_contrast": round(r.contrast, 2),
         "t0": round(r.sc.t0, 3), "t1": round(r.sc.t1, 3), "transition_out": r.sc.transition_out}
        for i, r in enumerate(renderers)
    ]
    n_frames = int(math.ceil(tl.total * fps))
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-i", audio_wav,
        "-map", "0:v", "-map", "1:a",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", encoder_preset, "-crf", str(crf), "-profile:v", "high", "-g", str(fps * 2),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-movflags", "+faststart",
        "-af", f"loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=out:st={max(tl.total - 0.9, 0):.3f}:d=0.9",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-t", f"{tl.total:.3f}", out_path,
    ]
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
    fade_in, fade_out = 0.45, 0.8
    try:
        assert proc.stdin is not None
        for n in range(n_frames):
            t = n / fps
            active = [r for r in renderers if r.sc.s0 <= t < r.sc.s1] or [renderers[-1]]
            if len(active) == 1:
                frame = active[0].frame(t, tl)
            else:
                a, b = active[0], active[1]
                p = (t - b.sc.s0) / max(a.sc.s1 - b.sc.s0, 1e-6)
                frame = transition(a.sc.transition_out or "crossfade", a.frame(t, tl), b.frame(t, tl), p)
            g = min(1.0, t / fade_in, max(0.0, (tl.total - t) / fade_out))
            if g < 1.0:
                frame = (frame * g).astype(np.uint8)
            proc.stdin.write(np.ascontiguousarray(frame).data)
            if progress and n % 15 == 0:
                progress(n, n_frames)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        rc = proc.wait()
    if rc != 0:
        err.seek(0)
        raise RenderError("ffmpeg failed to encode the video.", err.read().decode(errors="replace")[-400:])
    if progress:
        progress(n_frames, n_frames)
    return RenderResult(out_path, n_frames, time.time() - t_start, scene_report)
