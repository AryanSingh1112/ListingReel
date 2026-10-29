"""Narration providers behind one interface, plus text normalisation so TTS reads real-estate copy correctly."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from math import gcd
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from .errors import DependencyError

log = logging.getLogger(__name__)
SR = 44100


# --------------------------------------------------------------------------- speech normalisation
def prepare_speech(text: str) -> str:
    t = text
    t = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", " by ", t)                       # 24x7 -> 24 by 7
    t = re.sub(r"\bsq\.?\s?ft\.?", "square feet", t, flags=re.I)
    t = re.sub(r"\bsqft\b", "square feet", t, flags=re.I)
    t = re.sub(r"\bBHK\b", "B H K", t)
    t = re.sub(r"\bCr\b\.?", "crore", t)
    units = r"(lakhs?|lacs?|crores?|cr|k|m|mn|million|billion|thousand)?"
    for sym, word in (("₹", "rupees"), ("$", "dollars"), ("€", "euros"), ("£", "pounds")):
        t = re.sub(re.escape(sym) + r"\s*([\d.,]+)\s*" + units,
                   lambda m, w=word: f"{m.group(1)} {m.group(2) or ''} {w}".replace("  ", " "), t, flags=re.I)

    def phone(m: re.Match) -> str:
        raw = m.group(0)
        if sum(c.isdigit() for c in raw) < 7:
            return raw
        groups = [g for g in re.split(r"[\s\-]+", raw.strip()) if g]
        out = []
        for g in groups:
            plus = g.startswith("+")
            digits = " ".join(c for c in g if c.isdigit())
            out.append(("plus " if plus else "") + digits)
        return ", ".join(out)

    t = re.sub(r"\+?\d[\d\s\-]{6,}\d", phone, t)
    t = t.replace("@", " at ").replace("&", " and ").replace("/", " ")
    t = re.sub(r"\.(?=[a-z]{2,}\b)", " dot ", t)                              # name@x.com
    return re.sub(r"\s+", " ", t).strip()


# -------------------------------------------------------------------------------------- providers
class TTS:
    name = "base"

    def synth(self, text: str) -> np.ndarray:  # mono float32 @ SR
        raise NotImplementedError


def _read_wav(path: str) -> np.ndarray:
    sr, data = wavfile.read(path)
    x = data.astype(np.float32)
    if data.dtype == np.int16:
        x /= 32768.0
    elif data.dtype == np.int32:
        x /= 2147483648.0
    elif data.dtype == np.uint8:
        x = (x - 128) / 128.0
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR:
        g = gcd(sr, SR)
        x = resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def _trim(x: np.ndarray, thr: float = 0.004) -> np.ndarray:
    idx = np.flatnonzero(np.abs(x) > thr)
    if idx.size == 0:
        return x
    a, b = max(0, idx[0] - int(0.03 * SR)), min(len(x), idx[-1] + int(0.05 * SR))
    return x[a:b]


def _decode_to_array(src: str, wav: str) -> np.ndarray:
    """mp3 -> mono 44.1k wav via ffmpeg (already a hard dependency of the renderer)."""
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", str(SR), wav],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(wav):
        raise DependencyError(f"Could not decode TTS audio: {r.stderr.strip()[:160]}")
    return _read_wav(wav)


class EspeakTTS(TTS):
    name = "espeak-ng"

    def __init__(self, voice: str = "en-us+f3", speed: int = 160, pitch: int = 52):
        self.exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.exe:
            raise DependencyError("espeak-ng not found.", "apt install espeak-ng  (or use --tts none / --tts piper)")
        self.voice, self.speed, self.pitch = voice, speed, pitch

    def synth(self, text: str) -> np.ndarray:
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.wav")
            r = subprocess.run(
                [self.exe, "-v", self.voice, "-s", str(self.speed), "-p", str(self.pitch), "-g", "3", "-w", out, text],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0 or not os.path.exists(out):
                raise DependencyError(f"espeak-ng failed: {r.stderr.strip()[:200]}")
            return _trim(_read_wav(out))


class PiperTTS(TTS):
    name = "piper"

    def __init__(self, model: str):
        self.exe = shutil.which("piper")
        if not self.exe:
            raise DependencyError("piper not found.", "pip install piper-tts and download a voice model")
        if not model or not Path(model).is_file():
            raise DependencyError(f"Piper voice model not found: {model}", "pass --piper-model /path/voice.onnx")
        self.model = model

    def synth(self, text: str) -> np.ndarray:
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v.wav")
            r = subprocess.run([self.exe, "--model", self.model, "--output_file", out],
                               input=text, capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(out):
                raise DependencyError(f"piper failed: {r.stderr.strip()[:200]}")
            return _trim(_read_wav(out))


class EdgeTTS(TTS):
    """Microsoft Edge neural voices. Free, no API key, needs internet. Very natural."""
    name = "edge"

    def __init__(self, voice: str = "en-US-AriaNeural", rate: str = "-4%"):
        try:
            import edge_tts  # noqa: F401
        except ImportError as e:
            raise DependencyError("edge-tts not installed.", "pip install edge-tts") from e
        self.voice, self.rate = voice, rate

    def synth(self, text: str) -> np.ndarray:
        import asyncio

        import edge_tts

        async def run(path: str) -> None:
            await edge_tts.Communicate(text, self.voice, rate=self.rate).save(path)

        with tempfile.TemporaryDirectory() as d:
            mp3, wav = os.path.join(d, "v.mp3"), os.path.join(d, "v.wav")
            try:
                asyncio.run(run(mp3))
            except Exception as e:  # network / service errors
                raise DependencyError(f"Edge TTS failed: {str(e)[:160]}", "check your internet connection or use --tts kokoro / espeak") from e
            return _trim(_decode_to_array(mp3, wav))


class ElevenLabsTTS(TTS):
    """ElevenLabs (best quality). Needs ELEVENLABS_API_KEY; free tier ~10k characters/month."""
    name = "elevenlabs"

    def __init__(self, voice_id: str | None = None, api_key: str | None = None):
        self.key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not self.key:
            raise DependencyError("ELEVENLABS_API_KEY not set.", "export ELEVENLABS_API_KEY=... (free key at elevenlabs.io)")
        self.voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # "Sarah"

    def synth(self, text: str) -> np.ndarray:
        import json as _json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}?output_format=mp3_44100_128",
            data=_json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                              "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.2}}).encode(),
            headers={"xi-api-key": self.key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        )
        with tempfile.TemporaryDirectory() as d:
            mp3, wav = os.path.join(d, "v.mp3"), os.path.join(d, "v.wav")
            try:
                with urllib.request.urlopen(req, timeout=60) as r, open(mp3, "wb") as f:
                    f.write(r.read())
            except urllib.error.HTTPError as e:
                raise DependencyError(f"ElevenLabs error {e.code}.", "check the API key / free-tier quota") from e
            except Exception as e:
                raise DependencyError(f"ElevenLabs request failed: {str(e)[:160]}") from e
            return _trim(_decode_to_array(mp3, wav))


class KokoroTTS(TTS):
    """Kokoro-82M, fully offline neural voice (CPU). Needs model + voices files."""
    name = "kokoro"

    def __init__(self, model: str | None = None, voices: str | None = None, voice: str = "af_heart", speed: float = 0.98):
        model = model or os.environ.get("KOKORO_MODEL", "kokoro-v1.0.onnx")
        voices = voices or os.environ.get("KOKORO_VOICES", "voices-v1.0.bin")
        try:
            from kokoro_onnx import Kokoro
        except ImportError as e:
            raise DependencyError("kokoro-onnx not installed.", "pip install kokoro-onnx") from e
        if not (Path(model).is_file() and Path(voices).is_file()):
            raise DependencyError("Kokoro model files not found.", "download kokoro-v1.0.onnx and voices-v1.0.bin (see README)")
        self.k, self.voice, self.speed = Kokoro(model, voices), voice, speed

    def synth(self, text: str) -> np.ndarray:
        samples, sr = self.k.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        x = np.asarray(samples, dtype=np.float32)
        if sr != SR:
            g = gcd(sr, SR)
            x = resample_poly(x, SR // g, sr // g).astype(np.float32)
        return _trim(x)


class SilentTTS(TTS):
    """No narration: scenes are timed by estimated reading speed and captions carry the message."""
    name = "none"

    def synth(self, text: str) -> np.ndarray:
        words = max(1, len(text.split()))
        return np.zeros(int((0.35 + words / 2.7) * SR), np.float32)


def _build(name: str, piper_model: str | None, voice: str | None) -> TTS:
    if name == "elevenlabs":
        return ElevenLabsTTS(voice)
    if name == "edge":
        return EdgeTTS(voice) if voice else EdgeTTS()
    if name == "kokoro":
        return KokoroTTS(voice=voice) if voice else KokoroTTS()
    if name == "piper":
        if not piper_model:
            raise DependencyError("Piper requested but no --piper-model / LISTINGREEL_PIPER_MODEL given.")
        return PiperTTS(piper_model)
    if name == "espeak":
        return EspeakTTS()
    raise DependencyError(f"Unknown TTS provider '{name}'.", "use auto | elevenlabs | edge | kokoro | piper | espeak | none")


def pick_tts(
    choice: str = "auto", piper_model: str | None = None, voice: str | None = None,
    elevenlabs_api_key: str | None = None,
) -> tuple[TTS, list[str]]:
    """auto = best available: ElevenLabs (if key) -> Edge neural -> Kokoro -> Piper -> espeak -> silent."""
    notes: list[str] = []
    piper_model = piper_model or os.environ.get("LISTINGREEL_PIPER_MODEL")
    if choice == "none":
        return SilentTTS(), notes
    if choice == "auto" and elevenlabs_api_key:
        try:
            return ElevenLabsTTS(api_key=elevenlabs_api_key), notes
        except DependencyError as e:
            notes.append(f"elevenlabs unavailable ({e.message})")
    if choice != "auto":
        if choice == "elevenlabs":
            return ElevenLabsTTS(voice, elevenlabs_api_key), notes
        return _build(choice, piper_model, voice), notes
    order = (["elevenlabs"] if os.environ.get("ELEVENLABS_API_KEY") else []) + ["edge", "kokoro"]
    if piper_model:
        order.append("piper")
    order.append("espeak")
    for n in order:
        try:
            return _build(n, piper_model, None), notes
        except DependencyError as e:
            notes.append(f"{n} unavailable ({e.message})")
    notes.append("No TTS engine found; rendering without narration.")
    return SilentTTS(), notes
