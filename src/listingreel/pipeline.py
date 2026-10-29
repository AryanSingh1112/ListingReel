"""Orchestration. One function, used identically by the CLI and the web UI."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .config import Settings
from .errors import DependencyError, InputError
from .imaging import accent_from_image, fallback_card, load_assets
from .intake import build_facts, collect_image_paths, load_property, parse_property
from .mixer import mix_audio, write_wav
from .models import PropertyInput, Script
from .music import compose, swoosh
from .planner import SR, plan
from .qa import poster_and_sheet, run_checks
from .render import render
from .scripting import AnthropicLLM, generate_script
from .text_overlay import find_fonts
from .tts import pick_tts, prepare_speech

log = logging.getLogger(__name__)
Progress = Callable[[str, float], None]


@dataclass
class Result:
    video: Path | None
    srt: Path | None
    script_json: Path
    report_json: Path
    poster: Path | None
    contact_sheet: Path | None
    script: Script
    warnings: list[str]
    checks: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _seed(prop: PropertyInput, settings: Settings) -> int:
    if settings.seed is not None:
        return settings.seed
    return int(hashlib.sha256(prop.model_dump_json().encode()).hexdigest()[:8], 16)


def check_environment() -> list[str]:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    return missing


def generate(
    property_source: str | Path | PropertyInput | dict,
    out_dir: str | Path,
    settings: Settings | None = None,
    images_dir: str | Path | None = None,
    image_files: list[Path] | None = None,
    base_dir: str | Path | None = None,
    dry_run: bool = False,
    progress: Progress | None = None,
    llm=None,
) -> Result:
    settings = settings or Settings()
    preset = settings.preset_obj()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda *_: None)
    t_all = time.time()
    warnings: list[str] = []

    if not dry_run and (miss := check_environment()):
        raise DependencyError(f"Required tool(s) missing: {', '.join(miss)}", "install ffmpeg and re-run `listingreel doctor`")

    # 1. intake -------------------------------------------------------------------------------
    say("Reading property details", 0.02)
    if isinstance(property_source, (str, Path)):
        prop, bdir = load_property(property_source)
    else:
        prop = property_source if isinstance(property_source, PropertyInput) else parse_property(property_source)
        bdir = Path(base_dir or ".")
    facts, w, feats, price = build_facts(prop, settings.max_features)
    warnings += w

    # 2. script (grounded) ------------------------------------------------------------------
    say("Writing grounded script", 0.06)
    if llm is None and settings.llm == "anthropic":
        llm = AnthropicLLM()
    script, w = generate_script(prop, feats, price, facts, llm)
    warnings += w
    script_path = out / "script.json"
    script_path.write_text(json.dumps({
        "facts": [f.model_dump() for f in facts.facts], "script": script.model_dump(),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    if dry_run:
        rp = out / "report.json"
        rp.write_text(json.dumps({"warnings": warnings, "dry_run": True}, indent=2))
        return Result(None, None, script_path, rp, None, None, script, warnings)

    # 3. images -------------------------------------------------------------------------------
    say("Analysing photos", 0.10)
    paths, w = collect_image_paths(prop, bdir, images_dir, image_files)
    warnings += w
    assets, w = load_assets(paths)
    warnings += w
    if 0 < len(assets) < 4:
        warnings.append(f"Only {len(assets)} usable image(s); 4-8 recommended. Photos will be reused as detail crops.")
    if len(assets) > 8:
        assets = sorted(sorted(assets, key=lambda a: -a.quality)[:8], key=lambda a: paths.index(next(p for p in paths if p.name == a.name)))
        warnings.append("More than 8 images supplied; kept the 8 highest-quality ones.")
    accent = accent_from_image(assets[0].bgr) if assets else (150, 168, 0)

    # 4. narration -----------------------------------------------------------------------------
    say("Synthesising narration", 0.18)
    tts, w = pick_tts(settings.tts, settings.piper_model, settings.voice, settings.elevenlabs_api_key)
    warnings += w
    try:
        voices = [tts.synth(prepare_speech(l.vo)) for l in script.lines]
    except DependencyError as e:  # e.g. offline: degrade to a local voice instead of failing the job
        warnings.append(f"{tts.name} failed mid-run ({e.message}); fell back to a local voice.")
        try:
            tts, _ = pick_tts("espeak")
        except DependencyError:
            tts, _ = pick_tts("none")
        voices = [tts.synth(prepare_speech(l.vo)) for l in script.lines]

    # 5. plan the edit ------------------------------------------------------------------------
    say("Planning beat-synced edit", 0.24)
    seed = _seed(prop, settings)
    tl = plan(
        script.lines, voices, assets, W=preset.width, H=preset.height, fps=preset.fps, bpm=settings.bpm,
        min_total=settings.min_total, max_total=settings.max_total, transition=settings.transition,
        accent_bgr=accent, seed=seed,
    )
    warnings += tl.warnings
    # keep script in sync if the governor dropped scenes
    script.lines = [s.line for s in tl.scenes]

    # 6. audio ---------------------------------------------------------------------------------
    say("Composing music & mixing", 0.30)
    music = compose(tl.total + 1.0, settings.bpm, seed) if settings.music else None
    sw = swoosh(settings.transition, seed)
    sfx = [(s.t1 - settings.transition / 2, sw) for s in tl.scenes[:-1]]
    audio, ainfo = mix_audio(tl.total, music, [(s.vo_start, s.vo) for s in tl.scenes], sfx)
    tmp = Path(tempfile.mkdtemp(prefix="listingreel_"))
    wav = tmp / "mix.wav"
    write_wav(str(wav), audio)

    # 7. render ---------------------------------------------------------------------------------
    video = out / "video.mp4"
    fonts = find_fonts(settings.font)
    if not fonts.bold:
        warnings.append("No TrueType font found; using a basic fallback font.")
    rr = render(
        tl, str(wav), str(video), fonts, preset.crf,
        progress=lambda n, tot: say(f"Rendering frames {n}/{tot}", 0.32 + 0.6 * n / max(tot, 1)),
        encoder_preset=settings.encoder_preset,
    )
    shutil.rmtree(tmp, ignore_errors=True)

    # 8. captions sidecar, QA, artefacts --------------------------------------------------------------
    say("Verifying output", 0.94)
    srt = out / "captions.srt"
    srt.write_text("\n".join(
        f"{i}\n{_srt_time(s.vo_start)} --> {_srt_time(s.vo_start + max(s.vo_dur, 1.5))}\n{s.line.vo}\n"
        for i, s in enumerate(tl.scenes, 1)), encoding="utf-8")
    checks = run_checks(str(video), tl, rr.scenes, settings.min_total, settings.max_total, expect_audio=True)
    poster, sheet = poster_and_sheet(str(video), tl, out)
    for c in checks:
        if not c["pass"]:
            warnings.append(f"QA check failed: {c['check']} ({c['detail']})")

    report = {
        "version": __version__, "preset": preset.name, "seed": seed, "tts": tts.name,
        "script_source": script.source, "duration_s": round(tl.total, 2), "bpm": settings.bpm,
        "render_seconds": round(rr.seconds, 1), "total_seconds": round(time.time() - t_all, 1),
        "audio": ainfo, "images": [a.summary() for a in assets], "scenes": rr.scenes,
        "qa": checks, "warnings": warnings,
        "ai_usage": {"script": script.source, "llm": settings.llm, "tts": tts.name, "music": "procedural (numpy)",
                     "vision": "classical CV (spectral-residual saliency, Laplacian sharpness, DCT pHash)"},
    }
    rp = out / "report.json"
    rp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    say("Done", 1.0)
    return Result(video, srt, script_path, rp, poster, sheet, script, warnings, checks, report)
