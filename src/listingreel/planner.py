"""Turns (verified script + images + narration clips) into a frame-accurate Timeline.

Key ideas
* Audio-driven timing: a scene lasts exactly as long as its narration needs.
* Beat-snapped cuts: every cut lands on the music's beat grid, so edit and score feel composed together.
* Duration governor: keeps the final video inside the 20-40 s window by dropping the least important
  feature scenes (too long) or stretching holds by whole beats (too short).
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

import numpy as np

from .imaging import ImageAsset, fallback_card
from .models import ScriptLine

log = logging.getLogger(__name__)
SR = 44100
LEAD, TAIL = 0.35, 0.5
MIN_BEATS = {"hook": 6, "feature": 5, "price": 4, "cta": 8}
MAX_BEATS = 11


@dataclass
class Motion:
    kind: str                       # push | pull | pan | detail
    z0: float
    z1: float
    c0: tuple[float, float]
    c1: tuple[float, float]


@dataclass
class SceneClip:
    idx: int
    line: ScriptLine
    asset: ImageAsset
    motion: Motion
    variant: int
    vo: np.ndarray
    t0: float = 0.0                 # logical cut points (on the beat grid)
    t1: float = 0.0
    s0: float = 0.0                 # render span including transition overlap
    s1: float = 0.0
    vo_start: float = 0.0
    transition_out: str | None = None

    @property
    def vo_dur(self) -> float:
        return len(self.vo) / SR


@dataclass
class Timeline:
    scenes: list[SceneClip]
    total: float
    fps: int
    bpm: int
    width: int
    height: int
    transition: float
    accent_bgr: tuple[int, int, int]
    warnings: list[str] = field(default_factory=list)


def cover_window(sw: int, sh: int, W: int, H: int, z: float) -> tuple[float, float, float]:
    """Scale k (output px per source px) and crop-window size in source px for zoom z (cover fit)."""
    c = max(W / sw, H / sh)
    k = c * z
    return k, W / k, H / k


def plan_motion(asset: ImageAsset, variant: int, idx: int, W: int, H: int) -> Motion:
    fx, fy = asset.focal if variant == 0 else asset.alt_focal
    sh, sw = asset.bgr.shape[:2]
    if variant >= 1:   # "detail shot": tighter crop on a secondary region, so reused photos feel new
        return Motion("detail", 1.38, 1.55, (fx, fy), (0.5 * 0.12 + fx * 0.88, 0.5 * 0.12 + fy * 0.88))
    _, ww, _ = cover_window(sw, sh, W, H, 1.04)
    slack = 1 - ww / sw
    if slack > 0.22 and idx % 4 != 3:      # wide photo in a tall frame -> reveal it with a pan
        half = ww / sw / 2
        f0, f1 = (0.88, 0.12) if fx < 0.5 else (0.12, 0.88)
        cy = 0.5 + (fy - 0.5) * 0.4
        return Motion("pan", 1.04, 1.04, (half + slack * f0, cy), (half + slack * f1, cy))
    mid = (0.5 + (fx - 0.5) * 0.35, 0.5 + (fy - 0.5) * 0.35)
    if idx % 2 == 0:
        return Motion("push", 1.0, 1.15, mid, (fx, fy))
    return Motion("pull", 1.15, 1.02, (fx, fy), mid)


_IMAGE_CATEGORIES = {
    "balcony": ("balcony", "terrace", "verandah", "veranda", "deck"),
    "kitchen": ("kitchen", "kitchenette", "cooking"),
    "bedroom": ("bedroom", "bed room"),
    "bathroom": ("bathroom", "bath room", "washroom"),
    "living": ("living room", "lounge", "sitting room", "drawing room"),
    "dining": ("dining room", "dining area"),
    "pool": ("swimming pool", "pool"),
    "garden": ("garden", "lawn", "landscape"),
    "exterior": ("exterior", "facade", "façade", "street", "entrance", "building"),
}


def _categories(text: str) -> set[str]:
    normalized = text.lower().replace("_", " ").replace("-", " ")
    return {
        category
        for category, terms in _IMAGE_CATEGORIES.items()
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized) for term in terms)
    }


def assign_images(
    assets: list[ImageAsset], n: int, lines: list[ScriptLine] | None = None,
) -> list[tuple[ImageAsset | None, int]]:
    if not assets:
        return [(None, 0)] * n
    assignments: list[ImageAsset | None] = [None] * n
    used: set[int] = set()
    if lines:
        # Match explicitly named rooms first so they cannot be consumed by an earlier generic scene.
        for i, line in enumerate(lines[:n]):
            scene_categories = _categories(" ".join((line.headline, line.vo, line.subline or "")))
            if not scene_categories:
                continue
            match = next(
                (
                    j for j, asset in enumerate(assets)
                    if j not in used and scene_categories & _categories(asset.name)
                ),
                None,
            )
            if match is not None:
                assignments[i] = assets[match]
                used.add(match)

    remaining = iter(asset for j, asset in enumerate(assets) if j not in used)
    for i in range(n):
        if assignments[i] is None:
            assignments[i] = next(remaining, None)

    out: list[tuple[ImageAsset | None, int]] = []
    reused = 0
    for asset in assignments:
        if asset is None:
            asset = assets[reused % len(assets)]
            reused += 1
            out.append((asset, 1))
        else:
            out.append((asset, 0))
    return out


TRANSITIONS = ["slide", "crossfade", "zoom", "slide", "crossfade"]


def pick_transition(i: int, nxt_role: str) -> str:
    if nxt_role == "cta":
        return "dip"
    if nxt_role == "price":
        return "zoom"
    return TRANSITIONS[i % len(TRANSITIONS)]


def _beats(role: str, vo_dur: float, beat: float, extra: int = 0) -> int:
    need = LEAD + vo_dur + TAIL
    return min(MAX_BEATS + extra, max(MIN_BEATS[role] + extra, math.ceil(need / beat)))


def plan(
    lines: list[ScriptLine], voices: list[np.ndarray], assets: list[ImageAsset], *, W: int, H: int, fps: int,
    bpm: int, min_total: float, max_total: float, transition: float, accent_bgr: tuple[int, int, int], seed: int = 0,
) -> Timeline:
    warnings: list[str] = []
    beat = 60.0 / bpm
    lines, voices = list(lines), list(voices)

    def beat_counts(extra: list[int]) -> list[int]:
        return [_beats(l.role, len(v) / SR, beat, e) for l, v, e in zip(lines, voices, extra)]

    extra = [0] * len(lines)
    counts = beat_counts(extra)
    # --- governor: too long -> drop trailing feature scenes (keep >= 2 if available)
    while sum(counts) * beat > max_total:
        feat_idx = [i for i, l in enumerate(lines) if l.role == "feature"]
        if len(feat_idx) <= 2:
            warnings.append(f"Video is {sum(counts) * beat:.1f}s, above the {max_total:.0f}s target; narration is long.")
            break
        drop = feat_idx[-1]
        warnings.append(f"Dropped feature '{lines[drop].headline}' to stay within {max_total:.0f}s.")
        del lines[drop], voices[drop], extra[drop]
        counts = beat_counts(extra)
    # --- governor: too short -> add whole beats to holds, round-robin (hook/features first)
    guard = 0
    while sum(counts) * beat < min_total and guard < 200:
        order = [i for i, l in enumerate(lines) if l.role in ("hook", "feature", "price")] or list(range(len(lines)))
        i = order[guard % len(order)]
        if counts[i] < MAX_BEATS + 3:
            extra[i] += 1
            counts = beat_counts(extra)
        guard += 1
    total = sum(counts) * beat
    if total < min_total:
        warnings.append(f"Video is {total:.1f}s, below the {min_total:.0f}s target (few details supplied).")

    n = len(lines)
    pairs = assign_images(assets, n, lines)
    if assets:
        for line in lines:
            requested = _categories(" ".join((line.headline, line.vo, line.subline or "")))
            if requested and not any(requested & _categories(asset.name) for asset in assets):
                labels = ", ".join(sorted(requested))
                warnings.append(
                    f"No filename match for {labels} scene '{line.headline}'; "
                    "the supplied image order is used because photo subjects are not classified automatically."
                )
    scenes: list[SceneClip] = []
    t = 0.0
    for i, (line, vo, (asset, variant)) in enumerate(zip(lines, voices, pairs)):
        if asset is None:
            asset = fallback_card(max(W, 1080), max(H, 1080), accent_bgr, seed + i)
        t0, t1 = t, t + counts[i] * beat
        sc = SceneClip(
            idx=i, line=line, asset=asset, motion=plan_motion(asset, variant, i, W, H), variant=variant, vo=vo,
            t0=t0, t1=t1,
            s0=0.0 if i == 0 else t0 - transition / 2,
            s1=total if i == n - 1 else t1 + transition / 2,
            vo_start=(0.6 if i == 0 else t0 + LEAD),
            transition_out=None if i == n - 1 else pick_transition(i, lines[i + 1].role),
        )
        scenes.append(sc)
        t = t1
    if not assets:
        warnings.append("No usable images: the video uses generated brand cards instead of photos.")
    return Timeline(scenes, total, fps, bpm, W, H, transition, accent_bgr, warnings)
