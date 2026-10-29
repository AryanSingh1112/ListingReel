from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str
    width: int
    height: int
    fps: int
    crf: int


PRESETS: dict[str, Preset] = {
    "reels": Preset("reels", 1080, 1920, 30, 24),        # 9:16 Reels / Shorts / WhatsApp status
    "square": Preset("square", 1080, 1080, 30, 24),      # 1:1 feed posts
    "landscape": Preset("landscape", 1920, 1080, 30, 24),  # 16:9 YouTube / website
    "preview": Preset("preview", 540, 960, 24, 28),      # fast draft render
}


@dataclass
class Settings:
    preset: str = "reels"
    bpm: int = 96                 # every cut is snapped to this beat grid
    min_total: float = 20.0       # target duration window (seconds)
    max_total: float = 40.0
    transition: float = 0.6       # seconds of overlap between scenes
    tts: str = "auto"             # auto | elevenlabs | edge | kokoro | piper | espeak | none
    voice: str | None = None      # provider-specific voice name / id
    elevenlabs_api_key: str | None = None
    encoder_preset: str = "medium"
    piper_model: str | None = None
    llm: str = "none"             # none | anthropic
    seed: int | None = None       # None -> derived from the input (deterministic)
    music: bool = True
    font: str | None = None
    max_features: int = 5

    def preset_obj(self) -> Preset:
        from .errors import InputError

        try:
            return PRESETS[self.preset]
        except KeyError:
            raise InputError(
                f"Unknown preset '{self.preset}'.", f"choose one of: {', '.join(PRESETS)}"
            ) from None
