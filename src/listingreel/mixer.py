"""Mix narration + music + SFX. Music is sidechain-ducked under speech via an envelope follower."""
from __future__ import annotations

import numpy as np
from scipy.io import wavfile
from scipy.ndimage import gaussian_filter1d, maximum_filter1d

SR = 44100
ENV_HZ = 200


def _place(track: np.ndarray, start: float, clip: np.ndarray) -> None:
    i = int(start * SR)
    if i >= len(track):
        return
    n = min(len(clip), len(track) - i)
    track[i:i + n] += clip[:n]


def _normalise_voice(x: np.ndarray, target_rms: float = 0.13) -> np.ndarray:
    if not np.any(x):
        return x
    nz = x[np.abs(x) > 0.01]
    rms = float(np.sqrt(np.mean(nz**2))) if nz.size else 1.0
    y = x * (target_rms / max(rms, 1e-6))
    peak = float(np.abs(y).max())
    if peak > 0.9:
        y *= 0.9 / peak
    f = int(0.02 * SR)
    y[:f] *= np.linspace(0, 1, f)
    y[-f:] *= np.linspace(1, 0, f)
    return y.astype(np.float32)


def duck_gain(voice: np.ndarray, depth: float = 0.62) -> np.ndarray:
    """Per-sample gain (1 = untouched) that dips while speech is present."""
    hop = SR // ENV_HZ
    m = len(voice) // hop
    env = np.sqrt((voice[: m * hop].reshape(m, hop) ** 2).mean(axis=1))
    mask = (env > 0.01).astype(np.float32)
    mask = maximum_filter1d(mask, size=int(0.30 * ENV_HZ))        # hold through short gaps
    mask = gaussian_filter1d(mask, sigma=0.09 * ENV_HZ)           # smooth attack / release
    g = 1.0 - depth * mask
    return np.interp(np.arange(len(voice)) / SR, np.arange(m) / ENV_HZ, g).astype(np.float32)


def mix_audio(
    total: float,
    music: np.ndarray | None,
    voices: list[tuple[float, np.ndarray]],
    sfx: list[tuple[float, np.ndarray]],
) -> tuple[np.ndarray, dict]:
    n = int(total * SR)
    voice = np.zeros(n, np.float32)
    for start, clip in voices:
        _place(voice, start, _normalise_voice(clip.copy()))
    out = np.zeros((2, n), np.float32)
    info = {"voice": bool(np.any(voice)), "music": music is not None}
    if music is not None:
        m = music[:, :n]
        if m.shape[1] < n:
            m = np.pad(m, ((0, 0), (0, n - m.shape[1])))
        base = 0.55 if info["voice"] else 0.9
        out += m * base * (duck_gain(voice) if info["voice"] else 1.0)
    for start, clip in sfx:
        s = np.zeros(n, np.float32)
        _place(s, start, clip * 0.35)
        out += s
    out += voice  # centred
    peak = float(np.abs(out).max())
    if peak > 0.97:  # transparent safety: final loudness is handled by ffmpeg loudnorm
        out *= 0.97 / peak
    info["peak"] = round(float(np.abs(out).max()), 3)
    return out, info


def write_wav(path: str, audio: np.ndarray) -> None:
    wavfile.write(path, SR, (np.clip(audio.T, -1, 1) * 32767).astype(np.int16))
