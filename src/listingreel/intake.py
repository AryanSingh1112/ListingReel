"""Input loading, validation, normalisation. Fails with friendly InputErrors, never tracebacks."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .errors import InputError
from .models import Fact, FactSheet, PropertyInput

log = logging.getLogger(__name__)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MAX_FEATURE_CHARS = 60


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def load_property(path: str | Path) -> tuple[PropertyInput, Path]:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"Property file not found: {p}", "pass a .json or .yaml file, see samples/property.json")
    try:
        text = p.read_text(encoding="utf-8")
        data = yaml.safe_load(text) if p.suffix.lower() in {".yml", ".yaml"} else json.loads(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise InputError(f"Could not parse {p.name}: {e}", "check commas/quotes in the file") from None
    except UnicodeDecodeError:
        raise InputError(f"{p.name} is not valid UTF-8 text.") from None
    return parse_property(data), p.parent


def parse_property(data: object) -> PropertyInput:
    if not isinstance(data, dict):
        raise InputError("Property data must be an object/dictionary with keys like name, location, ...")
    try:
        return PropertyInput(**data)
    except ValidationError as e:
        lines = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "input"
            lines.append(f"  - {loc}: {err['msg']}")
        raise InputError(
            "Invalid property details:\n" + "\n".join(lines),
            "required: name, location, property_type. optional: price, features, cta, contact, images",
        ) from None


def discover_images(folder: str | Path) -> list[Path]:
    f = Path(folder)
    if not f.is_dir():
        raise InputError(f"Image folder not found: {f}")
    return sorted((p for p in f.iterdir() if p.suffix.lower() in IMAGE_EXT), key=_natural_key)


def collect_image_paths(
    prop: PropertyInput, base_dir: Path, images_dir: str | Path | None = None, extra: list[Path] | None = None
) -> tuple[list[Path], list[str]]:
    """Resolve image paths from (in priority) explicit list, --images folder, or `images` in the JSON.
    Missing/unsupported files are skipped with a warning, not fatal."""
    warnings: list[str] = []
    candidates: list[Path] = list(extra or [])
    if not candidates and images_dir:
        candidates = discover_images(images_dir)
    if not candidates:
        for s in prop.images:
            q = Path(s)
            candidates.append(q if q.is_absolute() else base_dir / q)
    ok: list[Path] = []
    for c in candidates:
        if not c.is_file():
            warnings.append(f"Image not found, skipped: {c.name}")
        elif c.suffix.lower() not in IMAGE_EXT:
            warnings.append(f"Unsupported image type, skipped: {c.name}")
        else:
            ok.append(c)
    return ok, warnings


def indian_group(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"{head},{tail}"


def format_price(price: str | int | float | None, currency: str) -> str | None:
    if price is None or (isinstance(price, str) and not price.strip()):
        return None
    if isinstance(price, str):
        return price.strip()
    n = int(price) if float(price).is_integer() else price
    if isinstance(n, int):
        body = indian_group(n) if currency == "₹" else f"{n:,}"
    else:
        body = f"{n:,.2f}"
    return f"{currency}{body}"


def clean_features(raw: list[str], max_features: int) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    out: list[str] = []
    seen: set[str] = set()
    for f in raw:
        f = re.sub(r"^[\s\-•*·\d.)]+(?=[A-Za-z₹$€£])", "", f) if re.match(r"^\s*[-•*·]", f) else f
        f = re.sub(r"\s+", " ", f).strip(" .;,")
        if not f:
            continue
        if len(f) > MAX_FEATURE_CHARS:
            cut = f[:MAX_FEATURE_CHARS].rsplit(" ", 1)[0]
            warnings.append(f"Feature too long for a caption, shortened: '{f[:30]}…' -> '{cut}'")
            f = cut
        key = f.lower()
        if key in seen:
            warnings.append(f"Duplicate feature ignored: {f}")
            continue
        seen.add(key)
        out.append(f)
    if len(out) > max_features:
        warnings.append(f"{len(out)} features given; using the first {max_features} to keep the video short.")
        out = out[:max_features]
    return out, warnings


def build_facts(prop: PropertyInput, max_features: int = 5) -> tuple[FactSheet, list[str], list[str], str | None]:
    """Returns (facts, warnings, cleaned_features, price_text)."""
    warnings: list[str] = []
    feats, w = clean_features(prop.features, max_features)
    warnings += w
    price = format_price(prop.price, prop.currency)
    if price is None:
        warnings.append("No price supplied: the price scene is skipped.")
    if not feats:
        warnings.append("No features supplied: the video will only contain title, price and enquiry scenes.")
    if not prop.contact:
        warnings.append("No contact supplied: the closing scene shows only the call-to-action text.")
    facts = [
        Fact(id="name", label="Property name", text=prop.name),
        Fact(id="location", label="Location", text=prop.location),
        Fact(id="type", label="Property type", text=prop.property_type),
    ]
    if price:
        facts.append(Fact(id="price", label="Price", text=price))
    facts += [Fact(id=f"f{i + 1}", label=f"Feature {i + 1}", text=t) for i, t in enumerate(feats)]
    facts.append(Fact(id="cta", label="Call to action", text=prop.cta))
    if prop.contact:
        facts.append(Fact(id="contact", label="Contact", text=prop.contact))
    return FactSheet(facts=facts), warnings, feats, price
