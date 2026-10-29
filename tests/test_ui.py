"""UI smoke tests for navigation and the automatic fast-preview Studio flow."""
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")

from listingreel.app import SAMPLE, _generation_settings, _sample_image_paths, _styles

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
app.main()
'''


def test_studio_generation_uses_fast_preview(tmp_path):
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
    labels = {button.label for button in at.button}
    assert {"Overview", "Studio", "Results", "Script & Provenance", "Pipeline", "How it works"} <= labels
    expected_titles = {
        "Overview": "Turn a listing into a reel",
        "Studio": "Create your property video",
        "Results": "Your generated video",
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

    assert "max-height:48vh" in light
    assert [path.name for path in paths] == [
        "00_exterior.jpg", "01_exterior_street.jpg", "02_living_room.jpg", "kitchen.jpg", "balcony.jpg"
    ]
