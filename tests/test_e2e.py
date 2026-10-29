import json
import subprocess

import pytest

from conftest import needs_ffmpeg
from listingreel.cli import main
from listingreel.config import Settings
from listingreel.errors import InputError
from listingreel.pipeline import generate

FAST = Settings(preset="preview", tts="none")


@needs_ffmpeg
def test_full_pipeline_produces_valid_mp4(tmp_path, prop_dict, photos):
    prop_dict["images"] = [str(p) for p in photos]
    res = generate(prop_dict, tmp_path / "o", FAST)
    assert res.video.exists() and all(c["pass"] for c in res.checks), res.checks
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(res.video)],
                                      capture_output=True, text=True).stdout)
    kinds = {s["codec_type"] for s in probe["streams"]}
    assert kinds == {"video", "audio"}
    assert (tmp_path / "o" / "captions.srt").read_text().startswith("1\n")


@needs_ffmpeg
def test_no_images_no_price_no_contact_still_renders(tmp_path):
    res = generate({"name": "Green Acres", "location": "Pune", "property_type": "Plot"}, tmp_path / "o", FAST)
    assert res.video.exists()
    assert any("No usable images" in w for w in res.warnings) and any("No price" in w for w in res.warnings)


@needs_ffmpeg
def test_one_image_is_enough(tmp_path, prop_dict, photos):
    prop_dict["images"] = [str(photos[0])]
    res = generate(prop_dict, tmp_path / "o", FAST)
    assert res.video.exists() and any("Only 1 usable" in w for w in res.warnings)


def test_dry_run_needs_no_ffmpeg_or_images(tmp_path, prop_dict):
    res = generate(prop_dict, tmp_path / "o", FAST, dry_run=True)
    assert res.video is None and res.script.lines[0].role == "hook"


def test_invalid_input_raises_inputerror_not_crash(tmp_path):
    with pytest.raises(InputError):
        generate({"name": "x"}, tmp_path / "o", FAST)


def test_cli_prints_clean_error(tmp_path, capsys):
    f = tmp_path / "p.json"; f.write_text('{"name": "A"}')
    rc = main(["generate", str(f), "--out", str(tmp_path / "o"), "--dry-run"])
    err = capsys.readouterr().err
    assert rc == 2 and "error:" in err and "Traceback" not in err


def test_cli_missing_file(capsys):
    assert main(["generate", "nope.json"]) == 2
