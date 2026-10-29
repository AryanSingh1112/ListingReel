"""Procedural royalty-free score. Generated from scratch with numpy -> zero licensing risk,
perfectly beat-aligned with the edit, deterministic for a given seed."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

SR = 44100


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


# I - V - vi - IV in C major: bass root + triad voicings kept close for smooth voice-leading
PROG = [
    (36, [60, 64, 67, 72]),
    (31, [59, 62, 67, 71]),
    (33, [57, 60, 64, 69]),
    (29, [57, 60, 65, 69]),
]


def _lp(x: np.ndarray, fc: float) -> np.ndarray:
    return sosfilt(butter(4, fc, "low", fs=SR, output="sos"), x, axis=-1).astype(np.float32)


def _add(buf: np.ndarray, t0: float, sig: np.ndarray, pan: float = 0.0) -> None:
    i = int(t0 * SR)
    if i >= buf.shape[1]:
        return
    n = min(len(sig), buf.shape[1] - i)
    buf[0, i:i + n] += sig[:n] * (1 - max(pan, 0))
    buf[1, i:i + n] += sig[:n] * (1 + min(pan, 0))


def compose(duration: float, bpm: int = 96, seed: int = 0) -> np.ndarray:
    """Returns float32 array shaped (2, N), peak-normalised to 0.6."""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    beat = 60.0 / bpm
    bar = 4 * beat
    mix = np.zeros((2, n), np.float32)
    arp_bus = np.zeros((2, n), np.float32)
    nbars = int(np.ceil(duration / bar)) + 1

    tt = lambda d: np.arange(int(d * SR)) / SR  # noqa: E731

    for b in range(nbars):
        root, notes = PROG[b % len(PROG)]
        t0 = b * bar
        # --- pad (detuned stack, slow attack/release so chords melt into each other)
        t = tt(bar + 0.7)
        env = np.minimum(1, t / 0.35) * np.minimum(1, (bar + 0.7 - t) / 0.6)
        for side, dets in ((0, (-7, 0)), (1, (0, 7))):
            sig = np.zeros_like(t, dtype=np.float32)
            for m in notes:
                for d in dets:
                    f = _hz(m) * 2 ** (d / 1200)
                    sig += np.sin(2 * np.pi * f * t) + 0.28 * np.sin(4 * np.pi * f * t)
            i0 = int(t0 * SR)
            m = min(len(t), n - i0)
            if m > 0:
                mix[side, i0:i0 + m] += (sig * env * 0.028)[:m]
        # --- bass
        t = tt(1.6 * beat)
        bass = np.sin(2 * np.pi * _hz(root) * t) * np.exp(-t * 2.2) * np.minimum(1, t / 0.02) * 0.24
        _add(mix, t0, bass)
        _add(mix, t0 + 2.5 * beat, bass * 0.6)
        # --- arpeggio on 8th notes, with seeded variation so it doesn't feel looped
        pattern = [0, 1, 2, 3, 2, 1, 2, 3] if b % 2 == 0 else [0, 2, 1, 3, 1, 2, 3, 2]
        for k, idx in enumerate(pattern):
            tk = t0 + k * beat / 2
            f = _hz(notes[idx] + 12)
            t = tt(0.9)
            vel = 0.7 + 0.3 * rng.random()
            pl = (np.sin(2 * np.pi * f * t) * np.exp(-t * 6.5) + 0.22 * np.sin(4 * np.pi * f * t) * np.exp(-t * 11))
            _add(arp_bus, tk, pl.astype(np.float32) * 0.075 * vel, pan=(-0.35 if k % 2 else 0.35))
        # --- soft kick on 1 & 3, hat on off-beats
        for kb in (0, 2):
            t = tt(0.28)
            f = 42 + 78 * np.exp(-t * 28)
            kick = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 13) * 0.38
            _add(mix, t0 + kb * beat, kick.astype(np.float32))
        for k in range(4):
            t = tt(0.06)
            noise = np.diff(rng.standard_normal(len(t) + 1)).astype(np.float32)
            _add(mix, t0 + (k + 0.5) * beat, noise * np.exp(-t * 70) * 0.022 * (0.7 + 0.3 * rng.random()))

    # dotted-eighth echo on the arp = instant "produced" feel
    for tap, g in ((0.75 * beat, 0.34), (1.5 * beat, 0.16)):
        d = int(tap * SR)
        arp_bus[:, d:] += arp_bus[:, : n - d] * g
    mix += arp_bus
    mix = _lp(mix, 9000)
    peak = float(np.abs(mix).max()) + 1e-9
    mix = mix / peak * 0.6
    fi, fo = int(0.5 * SR), int(min(1.8, duration / 3) * SR)
    mix[:, :fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    mix[:, -fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
    return mix


def swoosh(duration: float, seed: int = 0) -> np.ndarray:
    """Whoosh SFX placed on each transition: band-limited noise under a Hann envelope."""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    x = _lp(rng.standard_normal(n).astype(np.float32), 3800)
    x = x - _lp(x, 300)
    return (x * np.hanning(n).astype(np.float32) * 0.9).astype(np.float32)
