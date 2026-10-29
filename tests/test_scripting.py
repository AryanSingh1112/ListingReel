import json

from listingreel.intake import build_facts, parse_property
from listingreel.models import Script, ScriptLine
from listingreel.scripting import generate_script, template_script, verify_script


def _setup(prop_dict):
    p = parse_property(prop_dict)
    facts, _, feats, price = build_facts(p)
    return p, facts, feats, price


def test_template_is_grounded_and_provenanced(prop_dict):
    p, facts, feats, price = _setup(prop_dict)
    s = template_script(p, feats, price, facts)
    assert verify_script(s, facts, strict_vocab=True) == []
    assert s.lines[0].role == "hook" and s.lines[-1].role == "cta"
    assert all(l.fact_ids for l in s.lines)


def test_verifier_catches_invented_number_and_claims(prop_dict):
    p, facts, feats, price = _setup(prop_dict)
    s = template_script(p, feats, price, facts)
    s.lines[1].vo = "Enjoy a stunning 3 bedroom layout near the metro."
    probs = verify_script(s, facts)
    assert any("'3'" in x for x in probs)
    assert any("stunning" in x for x in probs)
    assert any("metro" in x for x in probs)


def test_claim_word_allowed_when_supplied(prop_dict):
    prop_dict["features"] = ["Spacious living room"]
    p, facts, feats, price = _setup(prop_dict)
    s = template_script(p, feats, price, facts)
    assert verify_script(s, facts, strict_vocab=True) == []


def test_price_must_match_exactly(prop_dict):
    p, facts, feats, price = _setup(prop_dict)
    s = template_script(p, feats, price, facts)
    next(l for l in s.lines if l.role == "price").vo = "Priced at ₹85 Cr."
    assert any("price" in x for x in verify_script(s, facts))


class FakeLLM:
    def __init__(self, payload): self.payload = payload
    def complete(self, system, user): return self.payload


def test_llm_hallucination_falls_back_to_template(prop_dict):
    p, facts, feats, price = _setup(prop_dict)
    bad = {"lines": [
        {"role": "hook", "vo": "Welcome to the most luxurious Skyline Residency.", "headline": "Skyline Residency", "fact_ids": ["name"]},
        {"role": "cta", "vo": "Only 3 units left! Call now.", "headline": "Call now", "fact_ids": ["cta"]}]}
    s, notes = generate_script(p, feats, price, facts, FakeLLM(json.dumps(bad)))
    assert s.source == "template" and any("rejected" in n for n in notes)


def test_llm_valid_output_is_accepted(prop_dict):
    p, facts, feats, price = _setup(prop_dict)
    good = {"lines": [
        {"role": "hook", "vo": "Meet Skyline Residency, a 2 BHK Apartment in Whitefield, Bengaluru.", "headline": "Skyline Residency", "fact_ids": ["name", "type", "location"]},
        {"role": "feature", "vo": "It has a modular kitchen.", "headline": "Modular kitchen", "fact_ids": ["f1"]},
        {"role": "cta", "vo": "Book your site visit today.", "headline": "Book your site visit today", "fact_ids": ["cta"]}]}
    s, notes = generate_script(p, feats, price, facts, FakeLLM("```json\n" + json.dumps(good) + "\n```"))
    assert s.source == "llm" and not notes


def test_llm_garbage_never_crashes(prop_dict):
    p, facts, feats, price = _setup(prop_dict)
    s, notes = generate_script(p, feats, price, facts, FakeLLM("sorry I can't"))
    assert s.source == "template" and notes
