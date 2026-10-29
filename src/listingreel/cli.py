"""Command line interface. Errors are printed as one clear message; use --debug for a traceback."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import PRESETS, Settings
from .errors import ListingReelError
from .pipeline import generate


def _settings(a: argparse.Namespace) -> Settings:
    return Settings(
        preset=a.preset, bpm=a.bpm, tts=a.tts, voice=a.voice, piper_model=a.piper_model, llm=a.llm, seed=a.seed,
        music=not a.no_music, font=a.font, max_features=a.max_features,
    )


def _progress(label: str, frac: float) -> None:
    bar = "█" * int(frac * 24) + "·" * (24 - int(frac * 24))
    sys.stderr.write(f"\r[{bar}] {frac * 100:3.0f}%  {label[:38]:<38}")
    sys.stderr.flush()
    if frac >= 1:
        sys.stderr.write("\n")


def cmd_generate(a: argparse.Namespace) -> int:
    res = generate(
        a.property, a.out, _settings(a), images_dir=a.images, dry_run=a.dry_run,
        progress=None if a.quiet else _progress,
    )
    if a.dry_run:
        print("\nGrounded script (dry run, nothing rendered):")
        for i, l in enumerate(res.script.lines, 1):
            print(f"  {i}. [{l.role:<7}] {l.vo}   <- facts: {', '.join(l.fact_ids)}")
    else:
        print(f"\n✔ Video      {res.video}  ({res.report['duration_s']}s, {res.report['preset']}, narration: {res.report['tts']})")
        print(f"  Captions   {res.srt}\n  Script     {res.script_json}\n  Report     {res.report_json}")
        if res.contact_sheet:
            print(f"  Preview    {res.contact_sheet}")
        failed = [c for c in res.checks if not c["pass"]]
        print(f"  QA         {len(res.checks) - len(failed)}/{len(res.checks)} checks passed")
    if res.warnings:
        print("\nWarnings:")
        for w in res.warnings:
            print(f"  ! {w}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    def row(name: str, ok: bool, note: str = "") -> None:
        print(f"  {'✔' if ok else '✘'} {name:<12} {note}")

    print(f"listingreel {__version__} environment check")
    row("ffmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "REQUIRED")
    row("ffprobe", bool(shutil.which("ffprobe")), shutil.which("ffprobe") or "REQUIRED")
    row("espeak-ng", bool(shutil.which("espeak-ng")), "offline narration (optional)")
    row("piper", bool(shutil.which("piper")), "higher-quality narration (optional)")
    from .text_overlay import find_fonts

    f = find_fonts()
    row("fonts", bool(f.bold), f.bold or "none found; pass --font")
    try:
        import anthropic  # noqa: F401
        row("anthropic", True, "optional LLM scripting")
    except ImportError:
        row("anthropic", False, "optional: pip install 'listingreel[llm]'")
    return 0 if shutil.which("ffmpeg") and shutil.which("ffprobe") else 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="listingreel", description="Property photos + details -> promo video (MP4).")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="create the video")
    g.add_argument("property", help="property details file (.json / .yaml)")
    g.add_argument("--images", help="folder of photos (overrides the `images` list in the file)")
    g.add_argument("--out", default="out", help="output folder (default: ./out)")
    g.add_argument("--preset", choices=list(PRESETS), default="reels")
    g.add_argument("--bpm", type=int, default=96)
    g.add_argument("--tts", choices=["auto", "elevenlabs", "edge", "kokoro", "piper", "espeak", "none"], default="auto")
    g.add_argument("--voice", default=None, help="voice name/id for the chosen provider (e.g. en-US-AriaNeural)")
    g.add_argument("--piper-model", help="path to a Piper .onnx voice")
    g.add_argument("--llm", choices=["none", "anthropic"], default="none", help="optional LLM script writer (verified)")
    g.add_argument("--seed", type=int, help="override the deterministic seed (music/variation)")
    g.add_argument("--no-music", action="store_true")
    g.add_argument("--font", help="path to a .ttf/.otf to use for captions")
    g.add_argument("--max-features", type=int, default=5)
    g.add_argument("--dry-run", action="store_true", help="only write + verify the script")
    g.add_argument("--quiet", action="store_true")
    g.set_defaults(fn=cmd_generate)

    d = sub.add_parser("doctor", help="check dependencies")
    d.set_defaults(fn=cmd_doctor)

    for s in (p, g, d):
        s.add_argument("--debug", action="store_true", help="show tracebacks")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(args, "debug", False) else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.fn(args)
    except ListingReelError as e:
        sys.stderr.write(f"\nerror: {e}\n")
        return e.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("\ncancelled\n")
        return 130
    except Exception as e:  # last-resort guard: never a raw traceback for end users
        if getattr(args, "debug", False):
            raise
        sys.stderr.write(f"\nunexpected error: {type(e).__name__}: {e}\n  re-run with --debug for details\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
