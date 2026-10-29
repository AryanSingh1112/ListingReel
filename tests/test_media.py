import numpy as np
import pytest

from listingreel.errors import InputError
from listingreel.imaging import accent_from_image, hamming, load_asset, load_assets
from listingreel.mixer import duck_gain, mix_audio
from listingreel.music import compose
from listingreel.planner import SR, assign_images, plan
from listingreel.models import ScriptLine
from listingreel.tts import prepare_speech


def test_asset_quality_and_focal(photos):
    a = load_asset(photos[0])
    assert 0 < a.quality <= 1 and 0.28 <= a.focal[0] <= 0.72


def test_corrupt_and_tiny_images_raise_inputerror(tmp_path):
    bad = tmp_path / "bad.jpg"; bad.write_bytes(b"not an image")
    with pytest.raises(InputError):
        load_asset(bad)
    from PIL import Image
    Image.new("RGB", (100, 100), "red").save(tmp_path / "tiny.png")
    with pytest.raises(InputError):
        load_asset(tmp_path / "tiny.png")


def test_bad_files_do_not_abort_batch(photos, tmp_path):
    bad = tmp_path / "bad.jpg"; bad.write_bytes(b"xx")
    assets, warns = load_assets([photos[0], bad, photos[0].with_name("p0.jpg")])
    assert len(assets) == 1 and warns


def test_near_duplicate_removed(photos, tmp_path):
    import shutil
    dup = tmp_path / "dup.jpg"; shutil.copy(photos[0], dup)
    assets, warns = load_assets([photos[0], dup, photos[1]])
    assert len(assets) == 2 and any("duplicate" in w for w in warns)


def test_accent_is_valid_bgr(photos):
    assert all(0 <= c <= 255 for c in accent_from_image(load_asset(photos[0]).bgr))


def test_speech_normalisation():
    assert "B H K" in prepare_speech("2 BHK Apartment")
    assert "rupees" in prepare_speech("₹85 Lakh") and prepare_speech("₹85 Lakh").index("Lakh") < prepare_speech("₹85 Lakh").index("rupees")
    assert "square feet" in prepare_speech("1,050 sq ft")
    assert "24 by 7" in prepare_speech("24x7 security")
    assert prepare_speech("+91 98765 43210").startswith("plus 9 1, 9 8 7 6 5")


def test_music_deterministic_and_bounded():
    a, b = compose(6, 96, 5), compose(6, 96, 5)
    assert np.array_equal(a, b) and np.abs(a).max() <= 0.61 and a.shape[0] == 2
    assert not np.array_equal(a, compose(6, 96, 6))


def test_ducking_dips_under_speech():
    v = np.zeros(SR * 4, np.float32); v[SR:2 * SR] = 0.3 * np.sin(np.arange(SR) / 5)
    g = duck_gain(v)
    assert g[int(1.5 * SR)] < 0.5 < g[int(3.5 * SR)]


def _lines():
    mk = lambda r, t: ScriptLine(role=r, vo=t, headline=t, fact_ids=["x"])
    return [mk("hook", "a"), mk("feature", "b"), mk("feature", "c"), mk("feature", "d"), mk("price", "p"), mk("cta", "z")]


def test_planner_beat_grid_and_duration_window(photos):
    assets, _ = load_assets(photos)
    voices = [np.zeros(int(2.0 * SR), np.float32)] * 6
    tl = plan(_lines(), voices, assets, W=540, H=960, fps=24, bpm=96, min_total=20, max_total=40, transition=0.6, accent_bgr=(1, 2, 3))
    beat = 60 / 96
    assert 20 <= tl.total <= 40
    for s in tl.scenes:
        assert abs(s.t1 / beat - round(s.t1 / beat)) < 1e-6          # cuts on the beat
    assert tl.scenes[0].s0 == 0 and tl.scenes[-1].s1 == tl.total


def test_planner_drops_features_when_narration_too_long(photos):
    assets, _ = load_assets(photos)
    voices = [np.zeros(int(7.0 * SR), np.float32)] * 6
    tl = plan(_lines(), voices, assets, W=540, H=960, fps=24, bpm=96, min_total=20, max_total=40, transition=0.6, accent_bgr=(1, 2, 3))
    assert len([s for s in tl.scenes if s.line.role == "feature"]) < 3 and any("Dropped" in w for w in tl.warnings)


def test_reuse_photos_as_detail_crops_when_few(photos):
    assets, _ = load_assets(photos[:2])
    pairs = assign_images(assets, 6)
    assert len(pairs) == 6 and sum(v for _, v in pairs) == 4


def test_named_rooms_match_features_without_reordering_other_photos(photos):
    assets = [load_asset(path) for path in photos[:3]]
    assets[0].name, assets[0].quality = "balcony.jpg", 0.1
    assets[1].name, assets[1].quality = "kitchen.jpg", 1.0
    assets[2].name, assets[2].quality = "00_exterior.jpg", 0.5
    lines = [
        ScriptLine(role="hook", vo="Introducing the residence.", headline="Skyline Residency"),
        ScriptLine(role="feature", vo="Highlights include modular kitchen.", headline="Modular kitchen"),
        ScriptLine(role="feature", vo="Private balcony.", headline="Private balcony"),
        ScriptLine(role="feature", vo="Clubhouse.", headline="Clubhouse"),
    ]

    pairs = assign_images(assets, len(lines), lines)

    assert [asset.name for asset, _ in pairs] == [
        "00_exterior.jpg", "kitchen.jpg", "balcony.jpg", "balcony.jpg"
    ]
    assert [variant for _, variant in pairs] == [0, 0, 0, 1]


def test_unlabelled_photos_keep_the_supplied_order(photos):
    assets = [load_asset(path) for path in photos[:2]]
    assets[0].quality, assets[1].quality = 0.1, 1.0

    pairs = assign_images(assets, 2)

    assert [asset for asset, _ in pairs] == assets


def test_unmatched_room_name_is_reported(photos):
    assets = [load_asset(path) for path in photos]
    lines = _lines()
    lines[1].headline = "Modular kitchen"
    lines[1].vo = "Highlights include modular kitchen."
    voices = [np.zeros(int(2.0 * SR), np.float32)] * len(lines)

    tl = plan(lines, voices, assets, W=540, H=960, fps=24, bpm=96, min_total=20, max_total=40,
              transition=0.6, accent_bgr=(1, 2, 3))

    assert any("No filename match for kitchen" in warning for warning in tl.warnings)


def test_no_images_still_plans(photos):
    voices = [np.zeros(int(2.0 * SR), np.float32)] * 6
    tl = plan(_lines(), voices, [], W=540, H=960, fps=24, bpm=96, min_total=20, max_total=40, transition=0.6, accent_bgr=(150, 168, 0))
    assert all(s.asset.synthetic for s in tl.scenes) and tl.warnings
