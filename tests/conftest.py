import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")


def make_photo(path: Path, size=(1600, 1000), seed=0) -> Path:
    """Distinct textured synthetic photo: random coloured blocks + gradient + noise (so pHash/sharpness/saliency are meaningful)."""
    rng = np.random.default_rng(seed + 100)
    h, w = size[1], size[0]
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.stack([(xx / w * rng.uniform(50, 250)) % 255, (yy / h * rng.uniform(50, 250)) % 255,
                    120 + 80 * np.sin(xx / rng.uniform(40, 200))], -1)
    for _ in range(14):
        x0, y0 = int(rng.integers(0, w - 200)), int(rng.integers(0, h - 150))
        img[y0:y0 + int(rng.integers(80, 400)), x0:x0 + int(rng.integers(100, 500))] = rng.integers(0, 255, 3)
    img += rng.normal(0, 8, img.shape)
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, quality=92)
    return path


@pytest.fixture
def photos(tmp_path):
    return [make_photo(tmp_path / f"p{i}.jpg", seed=i * 3 + 1) for i in range(5)]


@pytest.fixture
def prop_dict():
    return {
        "name": "Skyline Residency", "location": "Whitefield, Bengaluru", "property_type": "2 BHK Apartment",
        "price": "₹85 Lakh", "features": ["Modular kitchen", "Private balcony", "24x7 security"],
        "cta": "Book your site visit today", "contact": "+91 98765 43210",
    }
