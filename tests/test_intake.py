import json

import pytest

from listingreel.errors import InputError
from listingreel.intake import (build_facts, clean_features, collect_image_paths, format_price,
                                load_property, parse_property)


def test_valid_property(prop_dict):
    p = parse_property(prop_dict)
    facts, warns, feats, price = build_facts(p)
    assert price == "₹85 Lakh" and len(feats) == 3 and not warns
    assert {"name", "location", "type", "price", "f1", "cta", "contact"} <= facts.ids()


def test_missing_required_field_is_friendly(prop_dict):
    del prop_dict["name"]
    with pytest.raises(InputError) as e:
        parse_property(prop_dict)
    assert "name" in e.value.message and "Traceback" not in str(e.value)


def test_typo_key_rejected(prop_dict):
    prop_dict["feature"] = ["x"]
    with pytest.raises(InputError):
        parse_property(prop_dict)


def test_bad_json(tmp_path):
    f = tmp_path / "p.json"
    f.write_text("{not json")
    with pytest.raises(InputError):
        load_property(f)


def test_missing_optional_details_degrade_gracefully(prop_dict):
    for k in ("price", "contact", "features"):
        prop_dict.pop(k)
    _, warns, feats, price = build_facts(parse_property(prop_dict))
    assert price is None and feats == [] and len(warns) == 3


def test_price_formats():
    assert format_price(8500000, "₹") == "₹85,00,000"
    assert format_price(850000, "$") == "$850,000"
    assert format_price("  ₹85 Lakh ", "₹") == "₹85 Lakh"


def test_feature_cleaning():
    out, w = clean_features(["- Pool", "pool", "x" * 90, "", "  Gym  "], 5)
    assert out[0] == "Pool" and "Gym" in out and len(out) == 3 and len(w) == 2


def test_missing_images_skipped_with_warning(prop_dict, tmp_path, photos):
    prop_dict["images"] = [str(photos[0]), "nope.jpg", "readme.txt"]
    (tmp_path / "readme.txt").write_text("x")
    paths, warns = collect_image_paths(parse_property(prop_dict), tmp_path)
    assert paths == [photos[0]] and len(warns) == 2
