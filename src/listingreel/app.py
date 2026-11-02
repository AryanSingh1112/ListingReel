"""Streamlit application for the ListingReel property-video workflow."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

import streamlit as st

from listingreel.config import Settings
from listingreel.errors import ListingReelError
from listingreel.pipeline import generate

SAMPLE = Path(__file__).resolve().parents[2] / "samples"
HISTORY_DIR = Path(__file__).resolve().parents[2] / "out" / "history"
MANIFEST_NAME = "manifest.json"
OUTPUT_FILES = (
    "video.mp4",
    "captions.srt",
    "script.json",
    "report.json",
    "poster.jpg",
    "contact_sheet.jpg",
)
PAGES = ("Overview", "Studio", "Results", "Script & Provenance", "Pipeline", "How it works")
PAGE_ICONS = {
    "Overview": ":material/dashboard:",
    "Studio": ":material/edit_note:",
    "Results": ":material/movie:",
    "Script & Provenance": ":material/fact_check:",
    "Pipeline": ":material/account_tree:",
    "How it works": ":material/info:",
}


@dataclass(frozen=True)
class HistoryItem:
    directory: Path
    manifest: dict[str, Any]


def _ensure_history_dir(history_dir: Path | None = None) -> Path:
    history_dir = history_dir or HISTORY_DIR
    history_dir.mkdir(parents=True, exist_ok=True)
    resolved = history_dir.resolve()
    if not resolved.is_dir():
        raise OSError(f"Results history is not a directory: {resolved}")
    return resolved


def _validated_item_directory(item_dir: Path, history_dir: Path | None = None) -> Path:
    history = _ensure_history_dir(history_dir)
    candidate = item_dir if item_dir.is_absolute() else history / item_dir
    if candidate.is_symlink():
        raise ValueError("Result item cannot be a symbolic link.")
    resolved = candidate.resolve()
    if resolved == history or resolved.parent != history or not resolved.is_dir():
        raise ValueError("Result item must be a direct child of the history directory.")
    return resolved


def load_history_item(item_dir: Path, history_dir: Path | None = None) -> HistoryItem:
    directory = _validated_item_directory(item_dir, history_dir)
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Result manifest is missing or unsafe.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Result manifest could not be read: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format_version") != 1
        or manifest.get("item_id") != directory.name
        or not isinstance(manifest.get("display_name"), str)
        or not manifest["display_name"].strip()
        or not isinstance(manifest.get("property"), dict)
        or not isinstance(manifest.get("checks"), list)
    ):
        raise ValueError("Result manifest has an unsupported or incomplete format.")
    return HistoryItem(directory=directory, manifest=manifest)


def list_history_items(history_dir: Path | None = None) -> tuple[list[HistoryItem], list[str]]:
    history = _ensure_history_dir(history_dir)
    items: list[HistoryItem] = []
    issues: list[str] = []
    for candidate in history.iterdir():
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        try:
            items.append(load_history_item(candidate, history))
        except ValueError as exc:
            issues.append(f"{candidate.name}: {exc}")
    items.sort(key=lambda item: str(item.manifest.get("created_at", "")), reverse=True)
    return items, issues


def _write_history_manifest(
    item_dir: Path,
    property_data: dict[str, Any],
    result: Any,
    photo_paths: list[Path],
) -> HistoryItem:
    report = result.report
    checks = result.checks
    manifest = {
        "format_version": 1,
        "item_id": item_dir.name,
        "display_name": str(property_data.get("name") or "Untitled property").strip(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "property": property_data,
        "duration_s": report.get("duration_s"),
        "preset": report.get("preset", "preview"),
        "resolution": "540 × 960",
        "video_size_bytes": result.video.stat().st_size if result.video and result.video.is_file() else None,
        "qa_passed": sum(bool(check.get("pass")) for check in checks),
        "qa_total": len(checks),
        "checks": checks,
        "warnings": result.warnings,
        "photos": [
            {"name": path.name, "size_bytes": path.stat().st_size}
            for path in photo_paths
            if path.is_file()
        ],
    }
    manifest_path = item_dir / MANIFEST_NAME
    pending_path = item_dir / f"{MANIFEST_NAME}.pending"
    pending_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    pending_path.replace(manifest_path)
    return load_history_item(item_dir)


def rename_history_item(
    item_dir: Path, display_name: str, history_dir: Path | None = None
) -> HistoryItem:
    item = load_history_item(item_dir, history_dir)
    cleaned_name = display_name.strip()
    if not cleaned_name:
        raise ValueError("Result name cannot be empty.")
    if len(cleaned_name) > 120:
        raise ValueError("Result name must be 120 characters or fewer.")
    manifest = dict(item.manifest)
    manifest["display_name"] = cleaned_name
    manifest_path = item.directory / MANIFEST_NAME
    pending_path = item.directory / f"{MANIFEST_NAME}.pending"
    pending_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    pending_path.replace(manifest_path)
    return load_history_item(item.directory, history_dir)


def validate_delete_candidate(item_dir: Path, history_dir: Path | None = None) -> Path:
    item = load_history_item(item_dir, history_dir)
    for entry in item.directory.iterdir():
        if entry.is_symlink():
            raise ValueError("Result folder contains a symbolic link; deletion was blocked.")
        if entry.is_dir() and entry.name != "photos":
            raise ValueError("Result folder contains an unexpected directory; deletion was blocked.")
    photos = item.directory / "photos"
    if photos.exists():
        if photos.is_symlink() or not photos.is_dir():
            raise ValueError("Result photos folder is unsafe; deletion was blocked.")
        if any(path.is_symlink() or not path.is_file() for path in photos.iterdir()):
            raise ValueError("Result photos folder contains an unsafe entry; deletion was blocked.")
    allowed_files = set(OUTPUT_FILES) | {MANIFEST_NAME, f"{MANIFEST_NAME}.pending"}
    for entry in item.directory.iterdir():
        if entry.is_file() and entry.name not in allowed_files:
            raise ValueError("Result folder contains an unexpected file; deletion was blocked.")
        if not entry.is_file() and not entry.is_dir():
            raise ValueError("Result folder contains an unsafe entry; deletion was blocked.")
    return item.directory


def delete_history_item(item_dir: Path, history_dir: Path | None = None) -> None:
    directory = validate_delete_candidate(item_dir, history_dir)
    photos = directory / "photos"
    if photos.exists():
        for photo in photos.iterdir():
            photo.unlink()
        photos.rmdir()
    for filename in (*OUTPUT_FILES, MANIFEST_NAME, f"{MANIFEST_NAME}.pending"):
        file_path = directory / filename
        if file_path.exists():
            if file_path.is_symlink() or not file_path.is_file():
                raise ValueError(f"Unsafe result file; deletion was blocked: {filename}")
            file_path.unlink()
    directory.rmdir()


def _format_bytes(size: int | None) -> str:
    if size is None or size < 0:
        return "Unavailable"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _format_created_at(value: str) -> str:
    try:
        created = datetime.fromisoformat(value)
        if created.tzinfo is not None:
            created = created.astimezone()
        return created.strftime("%b %d, %Y · %I:%M %p")
    except (TypeError, ValueError):
        return "Date unavailable"


def _safe_history_media(item: HistoryItem, name: str, *, photo: bool = False) -> Path | None:
    if (
        not isinstance(name, str)
        or not name
        or Path(name).name != name
        or PureWindowsPath(name).name != name
        or name in {".", ".."}
    ):
        return None
    try:
        result_dir = item.directory
        if result_dir.is_symlink() or not result_dir.is_dir():
            return None
        result_root = result_dir.resolve()
        media_dir = result_dir / "photos" if photo else result_dir
        if media_dir.is_symlink() or not media_dir.is_dir():
            return None
        media_root = media_dir.resolve()
        if photo and media_root.parent != result_root:
            return None
        candidate = media_dir / name
        if candidate.is_symlink() or not candidate.is_file():
            return None
        resolved = candidate.resolve()
        if resolved.parent != media_root:
            return None
        return candidate
    except (OSError, RuntimeError):
        return None


def _unique_photo_name(name: str, index: int) -> str:
    basename = Path(name.replace("\\", "/")).name
    if not basename or basename in {".", ".."}:
        basename = "photo"
    return f"{index:02d}_{basename}"


def _styles(light: bool) -> str:
    if light:
        bg, panel, ink, muted, line, accent = "#f3f6f3", "#ffffff", "#17251d", "#52655a", "#d5dfd7", "#176b4d"
        nav, nav_hover, input_bg, accent_ink = "#e9efea", "#dce7df", "#183b2c", "#ffffff"
        input_ink, input_muted = "#ffffff", "#d5e5dc"
        toggle_knob, toggle_active_knob = "#17251d", "#ffffff"
    else:
        bg, panel, ink, muted, line, accent = "#101713", "#18221d", "#f0f6f2", "#bdc9c1", "#34443a", "#75c69d"
        nav, nav_hover, input_bg, accent_ink = "#24332b", "#2d4035", "#18221d", "#10251a"
        input_ink, input_muted = "#f0f6f2", "#bdc9c1"
        toggle_knob, toggle_active_knob = "#f0f6f2", "#10251a"
    return f"""
    <style>
    :root{{
        color-scheme: {"light" if light else "dark"};
        --lr-bg:{bg};--lr-panel:{panel};--lr-ink:{ink};--lr-muted:{muted};
        --lr-line:{line};--lr-accent:{accent};--lr-nav:{nav};
        --lr-nav-hover:{nav_hover};--lr-input:{input_bg};--lr-accent-ink:{accent_ink};
        --lr-input-ink:{input_ink};--lr-input-muted:{input_muted};
        --lr-toggle-knob:{toggle_knob};--lr-toggle-active-knob:{toggle_active_knob};
    }}
    html, body, [data-testid="stAppViewContainer"], .stApp{{
        background:var(--lr-bg);color:var(--lr-ink);
        font-family:"Aptos","Segoe UI",sans-serif;
    }}
    [data-testid="stHeader"]{{background:transparent}}
    #MainMenu,[data-testid="stMainMenu"],button[aria-label="Main menu"]{{display:none!important}}
    [data-testid="stAppDeployButton"]{{display:none!important}}
    [data-testid="stSidebar"]{{
        background:var(--lr-panel);border-right:1px solid var(--lr-line);
    }}
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"]{{
        color:var(--lr-ink);
    }}
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"]{{
        color:var(--lr-muted);font-size:.76rem;font-weight:700;letter-spacing:.12em;
    }}
    [data-testid="stSidebar"] div[data-testid="stButton"] > button,
    [data-testid="stSidebar"] button[kind="secondary"],
    [data-testid="stSidebar"] button[data-testid*="BaseButton"]{{
        background:var(--lr-nav)!important;color:var(--lr-ink)!important;
        border:1px solid var(--lr-line)!important;border-radius:10px!important;
        font-size:.96rem!important;font-weight:700!important;text-align:left!important;
        min-height:44px;justify-content:flex-start;
    }}
    [data-testid="stSidebar"] div[data-testid="stButton"] > button *,
    [data-testid="stSidebar"] button[kind="secondary"] *,
    [data-testid="stSidebar"] button[data-testid*="BaseButton"] *{{
        font-weight:700!important;
    }}
    [data-testid="stSidebar"] div[data-testid="stButton"] > button:hover,
    [data-testid="stSidebar"] button[kind="secondary"]:hover{{
        background:var(--lr-nav-hover)!important;border-color:var(--lr-accent)!important;
    }}
    [data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"],
    [data-testid="stSidebar"] button[kind="primary"],
    [data-testid="stSidebar"] [data-testid*="BaseButton-primary"]{{
        background:var(--lr-accent)!important;color:var(--lr-accent-ink)!important;
        border-color:var(--lr-accent)!important;
    }}
    [data-testid="stSidebar"] button[kind="primary"] *,
    [data-testid="stSidebar"] button[data-testid*="BaseButton-primary"] *,
    .stButton>button[kind="primary"] *, .stFormSubmitButton>button[kind="primary"] *,
    .stDownloadButton>button[kind="primary"] *{{
        color:var(--lr-accent-ink)!important;
    }}
    [data-testid="stSidebar"] div[data-testid="stButton"] > button:focus-visible,
    [data-testid="stSidebar"] button:focus-visible{{
        outline:3px solid var(--lr-accent)!important;outline-offset:2px;
    }}
    [data-testid="stMain"]{{color:var(--lr-ink)}}
    [data-testid="stMarkdownContainer"],[data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li,[data-testid="stMarkdownContainer"] strong,
    [data-testid="stText"],[data-testid="stWidgetLabel"],label,
    [data-testid="stFileUploaderDropzone"]{{color:var(--lr-ink)}}
    h1,h2,h3,h4{{color:var(--lr-ink)!important;letter-spacing:-.035em}}
    h1{{font-size:2.35rem!important;font-weight:800!important;line-height:1.12!important;margin-bottom:.3rem!important}}
    h2{{font-size:1.55rem!important;font-weight:750!important;line-height:1.2!important}}
    h3{{font-size:1.2rem!important;font-weight:750!important}}
    p,li{{font-size:1rem;line-height:1.6}}
    label,[data-testid="stWidgetLabel"] p{{font-size:.94rem!important;font-weight:650!important}}
    [data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] *{{
        color:var(--lr-muted)!important;font-size:.88rem;
    }}
    [data-testid="stVerticalBlockBorderWrapper"]{{
        background:var(--lr-panel);border:1px solid var(--lr-line);
        border-radius:14px;padding:14px;
    }}
    [data-testid="stTextInputRootElement"],
    [data-testid="stTextAreaRootElement"],
    [data-testid="stSelectbox"] [role="combobox"],
    [data-testid="stMultiSelect"] [role="combobox"],
    [data-testid="stTextInput"] [data-baseweb="input"]>div,
    [data-testid="stSelectbox"] [data-baseweb="select"]>div,
    [data-testid="stMultiSelect"] [data-baseweb="select"]>div,
    [data-testid="stTextArea"] [data-baseweb="textarea"]>div,
    [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"]{{
        background:var(--lr-input)!important;border-color:var(--lr-line)!important;
        border-radius:9px;color:var(--lr-input-ink)!important;
    }}
    [data-testid="stTextInputField"],
    [data-testid="stTextAreaRootElement"] textarea,
    [data-testid="stSelectbox"] [role="combobox"],
    [data-testid="stMultiSelect"] [role="combobox"],
    [data-testid="stTextInput"] [data-baseweb="input"] input,
    [data-testid="stTextArea"] [data-baseweb="textarea"] textarea,
    [data-testid="stSelectbox"] [data-baseweb="select"] *,
    [data-testid="stMultiSelect"] [data-baseweb="select"] *,
    [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] *{{
        color:var(--lr-input-ink)!important;caret-color:var(--lr-input-ink)!important;
    }}
    [data-testid="stTextInput"] input::placeholder,
    [data-testid="stTextArea"] textarea::placeholder{{
        color:var(--lr-input-muted)!important;opacity:1;
    }}
    [data-baseweb="popover"],[data-baseweb="menu"],[role="listbox"]{{
        background:var(--lr-panel)!important;color:var(--lr-ink)!important;
    }}
    [data-baseweb="menu"] *,[role="option"]{{color:var(--lr-ink)!important}}
    [data-testid="stCheckbox"] label,[data-testid="stToggle"] label{{
        color:var(--lr-ink)!important;
    }}
    [data-testid="stCheckbox"] label:has(input[role="switch"]) > div:first-of-type{{
        background:var(--lr-nav)!important;border:1px solid var(--lr-line)!important;
    }}
    [data-testid="stCheckbox"] label[data-selected="true"]:has(input[role="switch"]) > div:first-of-type{{
        background:var(--lr-accent)!important;border-color:var(--lr-accent)!important;
    }}
    [data-testid="stCheckbox"] label:has(input[role="switch"]) > div:first-of-type > div{{
        background:var(--lr-toggle-knob)!important;
    }}
    [data-testid="stCheckbox"] label[data-selected="true"]:has(input[role="switch"]) > div:first-of-type > div{{
        background:var(--lr-toggle-active-knob)!important;
    }}
    [data-testid="stAlert"]{{background:var(--lr-panel);color:var(--lr-ink);border:1px solid var(--lr-line)}}
    [data-testid="stExpander"]{{background:var(--lr-panel);border:1px solid var(--lr-line);border-radius:12px}}
    .stButton>button,.stFormSubmitButton>button,.stDownloadButton>button{{
        background:var(--lr-nav);color:var(--lr-ink);font-weight:750;
        border-radius:10px;border:1px solid var(--lr-line);min-height:44px;
    }}
    .stButton>button:hover,.stFormSubmitButton>button:hover,.stDownloadButton>button:hover{{
        background:var(--lr-nav-hover);border-color:var(--lr-accent);color:var(--lr-ink);
    }}
    [class*="st-key-results-actions-"]{{
        width:100%;max-width:800px;margin-top:.35rem;
    }}
    [class*="st-key-results-actions-"] [data-testid="stHorizontalBlock"]{{
        display:grid!important;grid-template-columns:repeat(4,minmax(0,1fr));
        gap:.5rem!important;
    }}
    [class*="st-key-results-actions-"] [data-testid="stColumn"]{{
        width:100%!important;min-width:0!important;flex:initial!important;
    }}
    [class*="st-key-results-actions-"] button{{
        width:100%;min-width:0;min-height:44px;white-space:normal;
        overflow-wrap:anywhere;line-height:1.2;padding:.45rem .35rem;
        background:var(--lr-nav)!important;color:var(--lr-ink)!important;
        border-color:var(--lr-line)!important;
    }}
    [class*="st-key-results-actions-"] button [data-testid="stMarkdownContainer"]{{
        min-width:0;flex:1 1 auto;
    }}
    [class*="st-key-results-actions-"] button p{{
        white-space:normal!important;overflow-wrap:anywhere;text-align:center;
    }}
    [class*="st-key-results-actions-"] button *,
    [class*="st-key-results-actions-"] button svg{{
        color:var(--lr-ink)!important;fill:var(--lr-ink)!important;
    }}
    [class*="st-key-results-actions-"] button:hover{{
        background:var(--lr-nav-hover)!important;color:var(--lr-ink)!important;
        border-color:var(--lr-accent)!important;
    }}
    [class*="st-key-results-actions-"] button:disabled{{
        background:var(--lr-nav)!important;color:var(--lr-muted)!important;
    }}
    [class*="st-key-results-actions-"] button:disabled *{{
        color:var(--lr-muted)!important;fill:var(--lr-muted)!important;
    }}
    .stFormSubmitButton>button[kind="primary"],.stButton>button[kind="primary"],
    .stDownloadButton>button[kind="primary"]{{
        background:var(--lr-accent)!important;color:var(--lr-accent-ink)!important;
        border-color:var(--lr-accent)!important;
    }}
    button:disabled{{background:var(--lr-nav)!important;color:var(--lr-muted)!important;opacity:.72}}
    a{{color:var(--lr-accent);font-weight:650}}
    :focus-visible{{outline:3px solid var(--lr-accent)!important;outline-offset:2px}}
    [data-testid="stVideo"]{{
        border:1px solid var(--lr-line);border-radius:12px;
        height:auto!important;width:auto!important;max-height:36vh!important;
        max-width:min(100%, 300px)!important;object-fit:contain;
    }}
    [data-testid="stVideo"] video,
    [data-testid="stVideo"] iframe{{
        display:block;height:min(36vh, 320px)!important;max-height:320px!important;
        width:auto!important;max-width:100%!important;aspect-ratio:9 / 16;
        object-fit:contain;
    }}
    [data-testid="stElementContainer"]:has(> [data-testid="stVideo"]){{display:flex;justify-content:center}}
    .lr-eyebrow{{font-size:.78rem;letter-spacing:.13em;text-transform:uppercase;font-weight:800;color:var(--lr-accent)}}
    .lr-brand{{font-size:1.7rem;font-weight:850;letter-spacing:-.045em;line-height:1.15;color:var(--lr-ink);padding:.25rem 0}}
    .lr-subtitle{{color:var(--lr-muted);font-size:1.02rem;line-height:1.55;font-weight:550;margin:.35rem 0 1.45rem}}
    .lr-panel-title{{font-size:1.08rem;font-weight:800;margin:0 0 1.05rem;color:var(--lr-ink)}}
    .lr-empty{{border:1px dashed var(--lr-line);border-radius:12px;padding:32px 18px;text-align:center;color:var(--lr-muted);background:var(--lr-panel);line-height:1.6}}
    .lr-empty strong{{color:var(--lr-ink)}}
    .lr-step{{padding:14px 0;border-bottom:1px solid var(--lr-line);line-height:1.6}}
    .lr-step:last-child{{border-bottom:0}}
    .lr-studio-subtitle{{font-size:.94rem;line-height:1.5;margin:.4rem 0 2rem;max-width:78ch}}
    @media(max-width:850px){{
        h1{{font-size:1.95rem!important}}
        [data-testid="stVideo"]{{max-height:34vh!important}}
        [data-testid="stVideo"] video,
        [data-testid="stVideo"] iframe{{height:min(34vh, 280px)!important;max-height:280px!important}}
        .lr-studio-subtitle{{margin-bottom:1.5rem}}
        [class*="st-key-results-actions-"] [data-testid="stHorizontalBlock"]{{
            grid-template-columns:repeat(2,minmax(0,1fr));
        }}
    }}
    </style>
    """


def _header(eyebrow: str, title: str, subtitle: str) -> None:
    st.markdown(f'<div class="lr-eyebrow">{eyebrow}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="lr-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def _render_generated(res) -> None:
    if res is None or res.video is None:
        st.markdown(
            '<div class="lr-empty"><strong>Your video preview will appear here</strong><br>'
            "Add property details and photos, then generate a reel.</div>",
            unsafe_allow_html=True,
        )
        return

    st.video(str(res.video))
    d1, d2 = st.columns(2)
    d1.download_button(
        "Download MP4",
        res.video.read_bytes(),
        file_name="property_video.mp4",
        mime="video/mp4",
        type="primary",
        icon=":material/download:",
        key="download_video",
        width="stretch",
    )
    d2.download_button(
        "Download captions",
        res.srt.read_bytes(),
        file_name="captions.srt",
        mime="text/plain",
        icon=":material/subtitles:",
        key="download_captions",
        width="stretch",
    )

    qa_pass = sum(check["pass"] for check in res.checks)
    st.caption(
        f":material/check_circle: {qa_pass}/{len(res.checks)} quality checks passed"
        f"  ·  {res.report['duration_s']} sec"
    )
    for warning in res.warnings:
        st.warning(warning, icon=":material/info:")


def _script_facts(res) -> list[dict]:
    if res is None:
        return []
    data = json.loads(res.script_json.read_text(encoding="utf-8"))
    facts = data.get("facts", [])
    lookup = {fact["id"]: fact["text"] for fact in facts}
    return [
        {
            "Scene": i + 1,
            "Voice-over": line.vo,
            "Supported by": " · ".join(f"{key}: {lookup.get(key, '?')}" for key in line.fact_ids),
        }
        for i, line in enumerate(res.script.lines)
    ]


def _generation_settings() -> Settings:
    return Settings(preset="preview", tts="auto", encoder_preset="ultrafast")


def _sample_image_paths(sample: dict) -> list[Path]:
    """Resolve the bundled images in the exact order declared by the sample listing."""
    paths = [SAMPLE / Path(name) for name in sample.get("images", [])]
    paths = [path for path in paths if path.is_file()]
    if paths:
        return paths
    return sorted((SAMPLE / "images").glob("*.jpg"))


def _sidebar() -> str:
    page = st.session_state.get("lr_page", "Overview")
    light = st.session_state.get("lr_light_mode", False)
    with st.sidebar:
        st.markdown('<div class="lr-brand">ListingReel</div>', unsafe_allow_html=True)
        st.caption("PROPERTY VIDEO STUDIO")
        st.divider()
        for name in PAGES:
            if st.button(
                name,
                key=f"navigate_{name}",
                icon=PAGE_ICONS[name],
                type="primary" if name == page else "secondary",
                width="stretch",
            ):
                st.session_state["lr_page"] = name
                st.rerun()
        st.divider()
        st.toggle("Light appearance", value=light, key="lr_light_mode")
    return page


def page_overview() -> None:
    _header(
        "LISTINGREEL",
        "Turn a listing into a reel",
        "Create a polished, fact-grounded property video from your details and photos.",
    )
    st.markdown('<div class="lr-panel-title">A simple path from listing to video</div>', unsafe_allow_html=True)
    st.markdown(
        "Add a few property details and photos. ListingReel builds a grounded script, "
        "narration, captions, music, and a share-ready video in one pass."
    )
    if st.button("Create a listing", type="primary", icon=":material/arrow_forward:", key="overview_create"):
        st.session_state["lr_page"] = "Studio"
        st.rerun()

    records, issues = list_history_items()
    with st.container(border=True):
        st.markdown('<div class="lr-panel-title">Latest result</div>', unsafe_allow_html=True)
        if records:
            latest = records[0]
            poster = latest.directory / "poster.jpg"
            thumbnail, summary = st.columns([0.3, 1.7], gap="medium")
            with thumbnail:
                if poster.is_file():
                    st.image(str(poster), width=112)
            with summary:
                st.markdown(f"**{latest.manifest['display_name']}**")
                st.caption(
                    f"{_format_created_at(latest.manifest.get('created_at', ''))} · "
                    f"{latest.manifest.get('duration_s', '—')} sec"
                )
                if st.button("Open results", icon=":material/movie:", key="overview_results"):
                    st.session_state["lr_selected_result_id"] = latest.directory.name
                    st.session_state["lr_page"] = "Results"
                    st.rerun()
        else:
            st.markdown(
                '<div class="lr-empty"><strong>No video yet</strong><br>'
                "Your generated videos will appear here.</div>",
                unsafe_allow_html=True,
            )
        if issues:
            st.caption(f"{len(issues)} result item(s) need attention in Results.")


def page_studio() -> None:
    sample = json.loads((SAMPLE / "property.json").read_text(encoding="utf-8")) if (SAMPLE / "property.json").exists() else {}
    st.markdown('<div class="lr-eyebrow">STUDIO</div>', unsafe_allow_html=True)
    st.title("Create your property video")
    st.markdown(
        '<div class="lr-subtitle lr-studio-subtitle">Enter the listing facts and photos. '
        'Narration and preview rendering are handled automatically.</div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns([1.04, 1], gap="large")
    with left:
        st.markdown('<div class="lr-panel-title">Property details</div>', unsafe_allow_html=True)
        with st.form("listingreel_generate", border=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Property name *", sample.get("name", ""))
            location = c2.text_input("Location *", sample.get("location", ""))
            c3, c4 = st.columns(2)
            ptype = c3.text_input("Property type *", sample.get("property_type", ""))
            price = c4.text_input("Price (optional)", sample.get("price", ""))
            feats = st.text_area(
                "Key features · one per line",
                "\n".join(sample.get("features", [])),
                height=82,
            )
            c5, c6 = st.columns(2)
            cta = c5.text_input("Call to action", sample.get("cta", "Book your site visit today"))
            contact = c6.text_input("Contact (optional)", sample.get("contact", ""))
            files = st.file_uploader(
                "Photos · 4–8 recommended",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
            )
            use_sample = st.checkbox("Use bundled sample photos when none are uploaded", value=True)
            submitted = st.form_submit_button(
                "Generate video",
                type="primary",
                icon=":material/movie_creation:",
                use_container_width=True,
            )

    with right:
        st.markdown('<div class="lr-panel-title">Video preview</div>', unsafe_allow_html=True)
        _render_generated(st.session_state.get("lr_result"))

    if submitted:
        history_dir = _ensure_history_dir()
        item_dir = history_dir / f"{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        item_dir.mkdir()
        photos_dir = item_dir / "photos"
        photos_dir.mkdir()
        images: list[Path] = []
        photo_sources = list(files or [])
        if not photo_sources and use_sample:
            photo_sources = _sample_image_paths(sample)
        for index, file in enumerate(photo_sources, 1):
            source_name = getattr(file, "name", None) or Path(file).name
            path = photos_dir / _unique_photo_name(source_name, index)
            if hasattr(file, "getbuffer"):
                path.write_bytes(file.getbuffer())
            else:
                shutil.copy2(Path(file), path)
            images.append(path)

        property_data = {
            "name": name,
            "location": location,
            "property_type": ptype,
            "price": price or None,
            "features": [line for line in feats.splitlines() if line.strip()],
            "cta": cta,
            "contact": contact or None,
        }
        started = time.time()
        progress = st.progress(0.0, text="Preparing your video…")

        def update_progress(label: str, fraction: float) -> None:
            elapsed = time.time() - started
            progress.progress(min(fraction, 1.0), text=f"{label} · {elapsed:.0f}s")

        try:
            result = generate(
                property_data,
                item_dir,
                _generation_settings(),
                image_files=images,
                progress=update_progress,
            )
        except ListingReelError as exc:
            progress.empty()
            st.error(exc.message)
            if exc.hint:
                st.info(exc.hint)
            return

        progress.empty()
        try:
            _write_history_manifest(item_dir, property_data, result, images)
        except (OSError, ValueError, TypeError) as exc:
            st.error(f"Video was generated, but its Results library record could not be saved: {exc}")
            return
        st.session_state["lr_result"] = result
        st.session_state["lr_elapsed"] = round(time.time() - started, 1)
        st.session_state["lr_selected_result_id"] = item_dir.name
        st.rerun()


def _render_history_details(item: HistoryItem) -> None:
    manifest = item.manifest
    st.subheader(manifest["display_name"])
    left, right = st.columns([0.85, 1.15], gap="large")
    video_path = item.directory / "video.mp4"
    with left:
        if video_path.is_file():
            st.video(str(video_path))
        else:
            st.warning("The MP4 file is missing from this result.")
    with right:
        st.caption(
            f"Created {_format_created_at(manifest.get('created_at', ''))} · "
            f"{manifest.get('duration_s', '—')} sec · "
            f"{manifest.get('resolution', 'Resolution unavailable')} · "
            f"{_format_bytes(manifest.get('video_size_bytes'))}"
        )
        if video_path.is_file():
            st.download_button(
                "Download MP4",
                video_path.read_bytes(),
                file_name=video_path.name,
                mime="video/mp4",
                type="primary",
                key=f"detail_download_video_{item.directory.name}",
                width="stretch",
            )

    st.markdown("### Media gallery")
    poster_column, storyboard_column = st.columns(2, gap="medium")
    gallery_items = (
        (poster_column, "Poster", "poster.jpg"),
        (storyboard_column, "Storyboard", "contact_sheet.jpg"),
    )
    for column, label, filename in gallery_items:
        with column:
            st.markdown(f"**{label}**")
            media_path = _safe_history_media(item, filename)
            if media_path is None:
                st.warning(f"{label} preview is missing or unsafe.")
                continue
            try:
                st.image(media_path.read_bytes(), caption=label, width="stretch")
            except OSError:
                st.warning(f"{label} preview could not be read.")

    st.markdown("**Source photos**")
    photos = manifest.get("photos")
    photo_records = photos if isinstance(photos, list) else []
    if not photo_records:
        st.caption("No source photos are available for this result.")
    else:
        photo_columns = st.columns(min(4, len(photo_records)), gap="small")
        for index, record in enumerate(photo_records):
            name = record.get("name") if isinstance(record, dict) else None
            media_path = _safe_history_media(item, name, photo=True)
            if media_path is None:
                st.warning(f"Source photo {name or 'preview'} is missing or unsafe.")
                continue
            try:
                with photo_columns[index % len(photo_columns)]:
                    st.image(media_path.read_bytes(), caption=name, width="stretch")
            except OSError:
                st.warning(f"Source photo {name} could not be read.")

    with st.expander("Property details"):
        property_details = []
        for key, value in manifest.get("property", {}).items():
            if value is None or value == "" or value == []:
                continue
            if isinstance(value, dict):
                value = ", ".join(f"{name}: {detail}" for name, detail in value.items())
            elif isinstance(value, (list, tuple)):
                value = ", ".join(str(detail) for detail in value)
            property_details.append({"Detail": key.replace("_", " ").title(), "Value": str(value)})
        if property_details:
            st.table(property_details)
        else:
            st.caption("No property details are available.")

    captions_path = item.directory / "captions.srt"
    with st.expander("Captions"):
        if captions_path.is_file():
            try:
                st.text(captions_path.read_text(encoding="utf-8"))
            except OSError as exc:
                st.error(f"Could not read the captions: {exc}")
        else:
            st.caption("Captions are unavailable for this result.")


def page_results() -> None:
    _header(
        "RESULTS",
        "Your video library",
        "Browse generated reels, open a preview, and manage each result.",
    )
    try:
        items, issues = list_history_items()
    except OSError as exc:
        st.error(f"Could not open the Results library: {exc}")
        return

    for issue in issues:
        st.warning(f"Some result records could not be loaded: {issue}", icon=":material/warning:")

    if not items:
        st.info("No results yet. Start in Studio to create your first reel.", icon=":material/movie:")
        if st.button("Open Studio", type="primary", icon=":material/edit_note:", key="results_studio"):
            st.session_state["lr_page"] = "Studio"
            st.rerun()
        return

    st.caption(f"{len(items)} result(s)")
    for item in items:
        manifest = item.manifest
        item_id = item.directory.name
        video = item.directory / "video.mp4"
        poster = item.directory / "poster.jpg"
        with st.container(border=True):
            thumbnail, summary = st.columns([0.65, 4.35], gap="medium")
            with thumbnail:
                if poster.is_file():
                    st.image(str(poster), width=104)
                else:
                    st.markdown(
                        '<div class="lr-empty" aria-label="Video thumbnail unavailable">'
                        '<strong>MP4</strong></div>',
                        unsafe_allow_html=True,
                    )
            with summary:
                st.markdown(f"**{manifest['display_name']}**")
                metadata = st.columns(3, gap="small")
                metadata[0].caption("Date")
                metadata[0].write(_format_created_at(manifest.get("created_at", "")))
                metadata[1].caption("Size")
                metadata[1].write(_format_bytes(video.stat().st_size if video.is_file() else None))
                metadata[2].caption("Preview size")
                metadata[2].write(manifest.get("resolution", "Resolution unavailable"))
            with st.container(key=f"results-actions-{item_id}"):
                actions = st.columns(4, gap="small")
                if actions[0].button(
                    "Open / Preview",
                    icon=":material/visibility:",
                    key=f"open_{item_id}",
                    help="Open the video preview and property details",
                    width="stretch",
                ):
                    st.session_state["lr_selected_result_id"] = item_id
                    st.rerun()
                if video.is_file():
                    actions[1].download_button(
                        "Download",
                        video.read_bytes(),
                        file_name=video.name,
                        mime="video/mp4",
                        key=f"download_{item_id}",
                        help="Download the video file",
                        icon=":material/download:",
                        width="stretch",
                    )
                else:
                    actions[1].button(
                        "Download",
                        key=f"download_missing_{item_id}",
                        disabled=True,
                        icon=":material/download:",
                        width="stretch",
                    )
                if actions[2].button(
                    "Rename",
                    icon=":material/edit:",
                    key=f"rename_{item_id}",
                    width="stretch",
                ):
                    st.session_state["lr_rename_id"] = item_id
                    st.rerun()
                if actions[3].button(
                    "Delete",
                    icon=":material/delete:",
                    key=f"delete_{item_id}",
                    width="stretch",
                ):
                    st.session_state["lr_delete_id"] = item_id
                    st.rerun()

            if st.session_state.get("lr_rename_id") == item_id:
                with st.form(f"rename_form_{item_id}"):
                    new_name = st.text_input("Result name", value=manifest["display_name"], key=f"rename_value_{item_id}")
                    save_name, cancel_name = st.columns(2)
                    if save_name.form_submit_button("Save name", type="primary"):
                        try:
                            rename_history_item(item.directory, new_name)
                        except (OSError, ValueError) as exc:
                            st.error(f"Could not rename this result: {exc}")
                        else:
                            st.session_state.pop("lr_rename_id", None)
                            st.rerun()
                    if cancel_name.form_submit_button("Cancel"):
                        st.session_state.pop("lr_rename_id", None)
                        st.rerun()

            if st.session_state.get("lr_delete_id") == item_id:
                st.warning(f"Delete “{manifest['display_name']}” and all files in this result folder? This cannot be undone.")
                confirm, cancel = st.columns(2)
                if confirm.button("Confirm delete", type="primary", key=f"confirm_delete_{item_id}"):
                    try:
                        delete_history_item(item.directory)
                    except (OSError, ValueError) as exc:
                        st.error(f"Could not delete this result: {exc}")
                    else:
                        st.session_state.pop("lr_delete_id", None)
                        if st.session_state.get("lr_selected_result_id") == item_id:
                            st.session_state.pop("lr_selected_result_id", None)
                        st.rerun()
                if cancel.button("Keep result", key=f"cancel_delete_{item_id}"):
                    st.session_state.pop("lr_delete_id", None)
                    st.rerun()

            if st.session_state.get("lr_selected_result_id") == item_id:
                st.divider()
                _render_history_details(item)


def page_script() -> None:
    _header(
        "SCRIPT & PROVENANCE",
        "Every line has a source",
        "Review the narration and the listing facts that support each scene.",
    )
    result = st.session_state.get("lr_result")
    if result is None:
        st.info("Generate a video in Studio to see its script and fact sources.", icon=":material/fact_check:")
        return
    rows = _script_facts(result)
    if rows:
        st.table(rows)
    else:
        st.info("No script lines were produced for this listing.")


def page_pipeline() -> None:
    _header(
        "PIPELINE",
        "From listing to finished reel",
        "A transparent workflow keeps the video useful, readable, and grounded in supplied facts.",
    )
    steps = [
        ("01", "Read the listing", "Validate the property details and build a source for every usable fact."),
        ("02", "Write and verify", "Create a concise script, then check every line against the supplied facts."),
        ("03", "Prepare the edit", "Assess photos, create narration and music, and plan scene timing."),
        ("04", "Render the video", "Compose animated scenes and captions into a vertical preview."),
        ("05", "Run quality checks", "Inspect the finished file and create a storyboard and report."),
    ]
    for number, title, description in steps:
        st.markdown(
            f'<div class="lr-step"><span class="lr-eyebrow">{number}</span>'
            f'<strong> &nbsp; {title}</strong><br><span>{description}</span></div>',
            unsafe_allow_html=True,
        )
    result = st.session_state.get("lr_result")
    if result:
        st.subheader("Latest run")
        st.caption(
            f"{sum(check['pass'] for check in result.checks)}/{len(result.checks)} "
            f"quality checks passed · {result.report['duration_s']} seconds"
        )


def page_how_it_works() -> None:
    _header(
        "HOW IT WORKS",
        "Designed for clear, trustworthy listings",
        "ListingReel turns your own property information into a short video without inventing features.",
    )
    with st.expander("Grounded narration", expanded=True):
        st.write(
            "The script is built from the details you provide. Each line is checked against those facts, "
            "and the Script & Provenance page shows the source facts behind each scene."
        )
    with st.expander("Photos, scenes, and captions"):
        st.write(
            "Photos are checked for usability and quality. Scene timing follows the narration, while "
            "captions and transitions are composed for vertical social-video layouts."
        )
    with st.expander("Narration and rendering"):
        st.write(
            "Narration is selected automatically from the available options. The app creates a quick "
            "preview automatically, with no voice or render settings to configure."
        )
    with st.expander("CLI options"):
        st.write(
            "For batch workflows and full-resolution output, use the ListingReel command line. "
            "The CLI retains its reels, square, landscape, and preview presets, plus explicit narration options."
        )
        st.code("listingreel generate samples/property.json --out out")


def main() -> None:
    st.set_page_config(page_title="ListingReel Studio", page_icon=":material/movie:", layout="wide")
    light = st.session_state.get("lr_light_mode", False)
    st.markdown(_styles(light), unsafe_allow_html=True)
    page = _sidebar()
    {
        "Overview": page_overview,
        "Studio": page_studio,
        "Results": page_results,
        "Script & Provenance": page_script,
        "Pipeline": page_pipeline,
        "How it works": page_how_it_works,
    }[page]()


if __name__ == "__main__":
    main()
