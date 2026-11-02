"""UI smoke tests for navigation and the automatic fast-preview Studio flow."""
import inspect
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")

from listingreel.app import (
    SAMPLE,
    _generation_settings,
    _safe_history_media,
    _sample_image_paths,
    _styles,
    delete_history_item,
    list_history_items,
    load_history_item,
    page_overview,
    page_results,
    rename_history_item,
    validate_delete_candidate,
)

HARNESS = '''
import os, sys, types, subprocess
os.environ.pop("ELEVENLABS_API_KEY", None)
fake = types.ModuleType("edge_tts")
class Communicate:
    def __init__(s, text, voice, rate="-4%"): s.t = text
    async def save(s, path):
        subprocess.run(["ffmpeg","-y","-loglevel","error","-f","lavfi","-i","sine=frequency=180:duration=%.2f" % (0.4 + len(s.t.split())/2.6),"-q:a","4",path], check=True)
fake.Communicate = Communicate
sys.modules["edge_tts"] = fake
from listingreel import app
from pathlib import Path
app.HISTORY_DIR = Path(os.environ.get("LISTINGREEL_TEST_HISTORY", "out/history"))
app.main()
'''


def test_studio_generation_uses_fast_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("LISTINGREEL_TEST_HISTORY", str(tmp_path / "history"))
    h = tmp_path / "h.py"
    h.write_text(HARNESS)
    at = st_testing.AppTest.from_file(str(h), default_timeout=300).run()
    assert not at.exception
    [b for b in at.button if b.label == "Studio"][0].click().run()
    assert not at.exception
    assert not at.selectbox
    assert not at.radio
    assert not any("key" in i.label.lower() or "voice" in i.label.lower() for i in at.text_input)
    [b for b in at.button if b.label == "Generate video"][0].click().run()
    assert not at.exception
    assert at.session_state["lr_result"].report["preset"] == "preview"


def test_sidebar_restores_all_pages(tmp_path):
    h = tmp_path / "h.py"
    h.write_text(HARNESS)
    at = st_testing.AppTest.from_file(str(h)).run()
    assert not at.exception
    assert at.session_state["lr_light_mode"] is False
    labels = {button.label for button in at.button}
    assert {"Overview", "Studio", "Results", "Script & Provenance", "Pipeline", "How it works"} <= labels
    expected_titles = {
        "Overview": "Turn a listing into a reel",
        "Studio": "Create your property video",
        "Results": "Your video library",
        "Script & Provenance": "Every line has a source",
        "Pipeline": "From listing to finished reel",
        "How it works": "Designed for clear, trustworthy listings",
    }
    assert next(button for button in at.button if button.label == "Overview").proto.type == "primary"
    for page, title in expected_titles.items():
        at = [b for b in at.button if b.label == page][0].click().run()
        assert not at.exception
        assert at.title[0].value == title
        assert at.session_state["lr_page"] == page
        assert next(button for button in at.button if button.label == page).proto.type == "primary"
        assert all(
            button.proto.type == ("primary" if button.label == page else "secondary")
            for button in at.button
            if button.label in expected_titles
        )


def test_generation_settings_are_automatic_fast_preview():
    settings = _generation_settings()
    assert settings.preset == "preview"
    assert settings.encoder_preset == "ultrafast"
    assert settings.tts == "auto"


def test_theme_styles_define_contrasting_sidebar_controls():
    light = _styles(True)
    dark = _styles(False)
    assert "color-scheme: light" in light
    assert "color-scheme: dark" in dark
    assert '[data-testid="stSidebar"] button[kind="secondary"]' in light
    assert '[data-testid="stSidebar"] button[kind="secondary"]' in dark
    assert 'label:has(input[role="switch"])' in light
    assert 'label:has(input[role="switch"])' in dark
    assert "--lr-nav:#e9efea" in light
    assert "--lr-nav:#24332b" in dark
    assert "--lr-accent-ink:#ffffff" in light
    assert "--lr-accent-ink:#10251a" in dark
    assert "--lr-toggle-knob:#17251d" in light
    assert "--lr-toggle-knob:#f0f6f2" in dark
    assert "--lr-input:#183b2c" in light
    assert "--lr-input-ink:#ffffff" in light
    assert "--lr-input-muted:#d5e5dc" in light
    assert '[data-testid="stTextInputRootElement"]' in light
    assert '[data-testid="stTextInputField"]' in light
    assert '[data-testid="stTextInput"] [data-baseweb="input"] input' in light
    assert '[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] *' in light
    assert "button[data-testid*=\"BaseButton\"] *" in light
    assert "font-weight:700!important" in light


def test_deploy_action_is_hidden_without_hiding_sidebar_controls():
    styles = _styles(True)

    assert '[data-testid="stAppDeployButton"]{display:none!important}' in styles
    assert '[data-testid="stSidebar"]' in styles
    assert '[data-testid="stSidebar"] div[data-testid="stButton"] > button' in styles
    assert '[data-testid="stCheckbox"] label:has(input[role="switch"])' in styles


def test_studio_preview_is_compact_and_sample_order_is_explicit():
    light = _styles(True)
    sample = json.loads((SAMPLE / "property.json").read_text(encoding="utf-8"))
    paths = _sample_image_paths(sample)

    assert "max-height:36vh" in light
    assert "max-width:min(100%, 300px)" in light
    assert "height:min(36vh, 320px)" in light
    assert ".lr-studio-subtitle" in light
    assert "margin:.4rem 0 2rem" in light
    assert [path.name for path in paths] == [
        "00_exterior.jpg", "01_exterior_street.jpg", "02_living_room.jpg", "kitchen.jpg", "balcony.jpg"
    ]


def _create_history_record(history: Path, item_id: str = "sample-item") -> Path:
    item = history / item_id
    item.mkdir(parents=True)
    (item / "video.mp4").write_bytes(b"video")
    (item / "photos").mkdir()
    image = (SAMPLE / "images" / "00_exterior.jpg").read_bytes()
    photo_path = item / "photos" / "01_front.jpg"
    photo_path.write_bytes(image)
    (item / "poster.jpg").write_bytes(image)
    (item / "contact_sheet.jpg").write_bytes(image)
    (item / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nWelcome home.\n", encoding="utf-8")
    (item / "script.json").write_text("{}", encoding="utf-8")
    (item / "report.json").write_text("{}", encoding="utf-8")
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "item_id": item_id,
                "display_name": "Sample home",
                "created_at": "2026-11-02T10:00:00+05:30",
                "property": {"name": "Sample home", "location": "Central"},
                "checks": [{"check": "video", "pass": True}],
                "photos": [{"name": "01_front.jpg", "size_bytes": photo_path.stat().st_size}],
            }
        ),
        encoding="utf-8",
    )
    return item


def test_history_manifest_listing_and_rename(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    item = _create_history_record(history)

    loaded = load_history_item(item, history)
    listed, issues = list_history_items(history)

    assert loaded.manifest["photos"] == [
        {"name": "01_front.jpg", "size_bytes": (item / "photos" / "01_front.jpg").stat().st_size}
    ]
    assert len(listed) == 1
    assert listed[0].directory == item
    assert issues == []
    assert rename_history_item(item, "  New display name  ", history).manifest["display_name"] == "New display name"
    assert json.loads((item / "manifest.json").read_text(encoding="utf-8"))["display_name"] == "New display name"


def test_delete_validation_only_allows_manifested_direct_child(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    item = _create_history_record(history)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_item = _create_history_record(outside, "outside-item")

    assert validate_delete_candidate(item, history) == item
    with pytest.raises(ValueError):
        validate_delete_candidate(history, history)
    with pytest.raises(ValueError):
        validate_delete_candidate(outside_item, history)

    delete_history_item(item, history)
    assert not item.exists()
    assert (outside_item / "video.mp4").is_file()
    assert history.is_dir()


def test_history_listing_reports_corrupt_manifests(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    invalid = history / "broken"
    invalid.mkdir()
    (invalid / "manifest.json").write_text("{", encoding="utf-8")

    records, issues = list_history_items(history)

    assert records == []
    assert len(issues) == 1
    assert "could not be read" in issues[0]


def test_history_media_paths_reject_traversal_and_symlinks(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    item_path = _create_history_record(history)
    item = load_history_item(item_path, history)

    assert _safe_history_media(item, "poster.jpg") == item_path / "poster.jpg"
    assert _safe_history_media(item, "01_front.jpg", photo=True) == item_path / "photos" / "01_front.jpg"
    assert _safe_history_media(item, "../outside.jpg", photo=True) is None
    assert _safe_history_media(item, r"..\\outside.jpg", photo=True) is None

    outside = tmp_path / "outside.jpg"
    outside.write_bytes((SAMPLE / "images" / "00_exterior.jpg").read_bytes())
    link = item_path / "photos" / "linked.jpg"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links are unavailable in this environment.")
    assert _safe_history_media(item, "linked.jpg", photo=True) is None


def test_results_library_shows_record_actions_and_opens_details(tmp_path, monkeypatch):
    history = tmp_path / "history"
    history.mkdir()
    _create_history_record(history)
    monkeypatch.setenv("LISTINGREEL_TEST_HISTORY", str(history))

    harness = tmp_path / "h.py"
    harness.write_text(HARNESS)
    at = st_testing.AppTest.from_file(str(harness)).run()
    assert not at.exception
    at = next(button for button in at.button if button.label == "Results").click().run()
    assert not at.exception
    assert {"Open / Preview", "Rename", "Delete"} <= {button.label for button in at.button}
    action_buttons = {button.label: button for button in at.button}
    assert action_buttons["Open / Preview"].proto.icon == ":material/visibility:"
    assert action_buttons["Rename"].proto.icon == ":material/edit:"
    assert action_buttons["Delete"].proto.icon == ":material/delete:"
    download = at.get("download_button")[0]
    assert download.label == "Download"
    assert download.proto.icon == ":material/download:"
    for light_mode in (True, False):
        action_styles = _styles(light_mode)
        assert '[class*="st-key-results-actions-"]' in action_styles
        assert "max-width:800px" in action_styles
        assert "width:100%;max-width:800px" in action_styles
        assert '[data-testid="stColumn"]' in action_styles
        assert "background:var(--lr-nav)!important;color:var(--lr-ink)!important" in action_styles
        assert "color:var(--lr-ink)!important;fill:var(--lr-ink)!important" in action_styles
        assert "grid-template-columns:repeat(4,minmax(0,1fr))" in action_styles
        assert "grid-template-columns:repeat(2,minmax(0,1fr))" in action_styles
        assert "width:100%;min-width:0;min-height:44px;white-space:normal" in action_styles
        assert "overflow-wrap:anywhere;line-height:1.2;padding:.45rem .35rem" in action_styles
        assert 'button [data-testid="stMarkdownContainer"]' in action_styles
        assert "white-space:normal!important;overflow-wrap:anywhere;text-align:center" in action_styles
    assert {"Date", "Size", "Preview size"} <= {caption.value for caption in at.caption}
    assert not any("QA " in item.value for item in at.markdown)
    assert not any("Stored locally" in item.value for item in at.caption)

    at = next(button for button in at.button if button.label == "Open / Preview").click().run(timeout=30)
    assert not at.exception
    assert at.session_state["lr_selected_result_id"] == "sample-item"
    assert "Sample home" in [heading.value for heading in at.subheader]
    markdown_values = [item.value for item in at.markdown]
    assert "**Poster**" in markdown_values
    assert "**Storyboard**" in markdown_values
    assert "**Source photos**" in markdown_values
    assert {expander.label for expander in at.get("expander")} == {"Property details", "Captions"}
    assert [button.label for button in at.get("download_button")] == ["Download", "Download MP4"]
    assert not any("JSON" in label or "report" in label.lower() or "script" in label.lower()
                   for label in [button.label for button in at.get("download_button")])


def test_overview_places_latest_result_below_intro_as_full_width_section():
    source = inspect.getsource(page_overview)
    assert source.index("overview_create") < source.index("Latest result")
    assert source.index("Latest result") < source.index("st.columns")
    assert "saved in the Results library" not in source
    results_source = inspect.getsource(page_results)
    assert "Stored locally" not in results_source
