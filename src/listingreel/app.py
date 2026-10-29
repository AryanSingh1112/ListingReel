"""Streamlit application for the ListingReel property-video workflow."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import streamlit as st

from listingreel.config import Settings
from listingreel.errors import ListingReelError
from listingreel.pipeline import generate

SAMPLE = Path(__file__).resolve().parents[2] / "samples"
PAGES = ("Overview", "Studio", "Results", "Script & Provenance", "Pipeline", "How it works")
PAGE_ICONS = {
    "Overview": ":material/dashboard:",
    "Studio": ":material/edit_note:",
    "Results": ":material/movie:",
    "Script & Provenance": ":material/fact_check:",
    "Pipeline": ":material/account_tree:",
    "How it works": ":material/info:",
}


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
    h1{{font-size:2.35rem!important;font-weight:800!important;line-height:1.12!important}}
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
    .stFormSubmitButton>button[kind="primary"],.stButton>button[kind="primary"],
    .stDownloadButton>button[kind="primary"]{{
        background:var(--lr-accent)!important;color:var(--lr-accent-ink)!important;
        border-color:var(--lr-accent)!important;
    }}
    button:disabled{{background:var(--lr-nav)!important;color:var(--lr-muted)!important;opacity:.72}}
    a{{color:var(--lr-accent);font-weight:650}}
    :focus-visible{{outline:3px solid var(--lr-accent)!important;outline-offset:2px}}
    [data-testid="stVideo"] video{{border:1px solid var(--lr-line);border-radius:12px;max-height:48vh}}
    .lr-eyebrow{{font-size:.78rem;letter-spacing:.13em;text-transform:uppercase;font-weight:800;color:var(--lr-accent)}}
    .lr-brand{{font-size:1.7rem;font-weight:850;letter-spacing:-.045em;line-height:1.15;color:var(--lr-ink);padding:.25rem 0}}
    .lr-subtitle{{color:var(--lr-muted);font-size:1.02rem;line-height:1.55;font-weight:550;margin-top:-.45rem}}
    .lr-panel-title{{font-size:1.08rem;font-weight:800;margin:0 0 .55rem;color:var(--lr-ink)}}
    .lr-empty{{border:1px dashed var(--lr-line);border-radius:12px;padding:32px 18px;text-align:center;color:var(--lr-muted);background:var(--lr-panel);line-height:1.6}}
    .lr-empty strong{{color:var(--lr-ink)}}
    .lr-step{{padding:14px 0;border-bottom:1px solid var(--lr-line);line-height:1.6}}
    .lr-step:last-child{{border-bottom:0}}
    .lr-studio-subtitle{{font-size:.94rem;line-height:1.4;margin-top:0}}
    [data-testid="stVideo"]{{display:flex;justify-content:center}}
    @media(max-width:850px){{
        h1{{font-size:1.95rem!important}}
        [data-testid="stVideo"] video{{max-height:38vh}}
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
    light = st.session_state.get("lr_light_mode", True)
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
    st.write("")
    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        st.markdown('<div class="lr-panel-title">A simple path from listing to video</div>', unsafe_allow_html=True)
        st.markdown(
            "Add a few property details and photos. ListingReel builds a grounded script, "
            "narration, captions, music, and a share-ready video in one pass."
        )
        if st.button("Create a listing", type="primary", icon=":material/arrow_forward:", key="overview_create"):
            st.session_state["lr_page"] = "Studio"
            st.rerun()
    with right:
        current = st.session_state.get("lr_result")
        st.markdown('<div class="lr-panel-title">Latest result</div>', unsafe_allow_html=True)
        if current and current.video:
            st.caption(f"Ready · {current.report['duration_s']} seconds")
            st.video(str(current.video))
            if st.button("Open results", icon=":material/movie:", key="overview_results"):
                st.session_state["lr_page"] = "Results"
                st.rerun()
        else:
            st.markdown(
                '<div class="lr-empty"><strong>No video yet</strong><br>'
                "Your latest result will be available here.</div>",
                unsafe_allow_html=True,
            )


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
        tmp = Path(tempfile.mkdtemp(prefix="lr_ui_"))
        images: list[Path] = []
        for file in files or []:
            path = tmp / Path(file.name).name
            path.write_bytes(file.getbuffer())
            images.append(path)
        if not images and use_sample:
            images = _sample_image_paths(sample)

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
                tmp / "out",
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
        st.session_state["lr_result"] = result
        st.session_state["lr_elapsed"] = round(time.time() - started, 1)
        st.rerun()


def page_results() -> None:
    _header("RESULTS", "Your generated video", "Preview, download, and review the quality checks for your latest reel.")
    result = st.session_state.get("lr_result")
    if result is None or result.video is None:
        st.info("No video has been generated yet. Start in Studio to create your first reel.", icon=":material/movie:")
        if st.button("Open Studio", type="primary", icon=":material/edit_note:", key="results_studio"):
            st.session_state["lr_page"] = "Studio"
            st.rerun()
        return
    _render_generated(result)
    st.subheader("Quality report")
    st.table(result.checks)
    if result.contact_sheet:
        st.image(str(result.contact_sheet), caption="Rendered storyboard", use_container_width=True)


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
    light = st.session_state.get("lr_light_mode", True)
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
