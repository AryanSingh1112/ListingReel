"""Script generation with a hard grounding guarantee.

Two generators (deterministic template, optional LLM) feed ONE verifier. Nothing reaches the
renderer unless every number, proper noun and "claim word" in it is traceable to the fact sheet.
If the LLM output fails verification we fall back to the template and record why.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

from .errors import InputError
from .models import FactSheet, PropertyInput, Script, ScriptLine

log = logging.getLogger(__name__)

WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Marketing words that assert something the sales team did not tell us. Allowed ONLY if the
# agent's own input contains them (so "spacious" in a feature is fine; invented "spacious" is not).
CLAIM_WORDS = {
    "luxury", "luxurious", "spacious", "stunning", "breathtaking", "best", "perfect", "ideal", "dream",
    "prime", "premium", "guaranteed", "bargain", "investment", "appreciation", "returns", "roi", "rera",
    "vastu", "ready", "fully", "furnished", "view", "views", "heritage", "safe", "quiet", "peaceful",
    "affordable", "cheap", "lowest", "exclusive", "limited", "hurry", "last", "only", "rare", "unmatched",
    "top", "famous", "award", "winning", "iconic", "unbeatable", "sea-facing", "metro", "airport", "school",
    "schools", "hospital", "hospitals", "mall", "gated", "huge", "large", "big", "modern", "elegant",
    "beautiful", "gorgeous", "amazing", "incredible", "world-class", "state-of-the-art", "sought-after",
}

# Closed vocabulary of the deterministic template (strict mode): template text may use ONLY these + fact words.
GLUE_VOCAB = {
    "introducing", "a", "an", "the", "in", "at", "priced", "highlights", "highlight", "include", "includes",
    "plus", "and", "along", "with", "featuring", "call", "contact", "write", "to", "us", "now", "enquire",
    "price",
}


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


# ----------------------------------------------------------------------------------------- helpers
def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


def _words(text: str) -> set[str]:
    out: set[str] = set()
    for w in WORD.findall(text):
        w = w.lower().strip("-'’")
        out.add(w)
        out.update(p for p in re.split(r"[-'’]", w) if p)
    return out


def smart_lower(s: str) -> str:
    """Lower-case a phrase but keep acronyms (BHK) and tokens with digits intact."""
    parts = []
    for tok in s.split(" "):
        parts.append(tok if (tok.isupper() and len(tok) > 1) or any(c.isdigit() for c in tok) else tok.lower())
    return " ".join(parts)


def article(phrase: str) -> str:
    first = phrase.strip().split(" ")[0]
    if first[:1].isdigit():
        return "an" if re.match(r"(8|11|18)\b", first) else "a"
    return "an" if first[:1].lower() in "aeiou" else "a"


def is_phone(s: str) -> bool:
    return sum(c.isdigit() for c in s) >= 7 and "@" not in s


# ---------------------------------------------------------------------------------- template engine
LEADS = ["Plus, {f}.", "Along with {f}.", "And {f}."]


def template_script(prop: PropertyInput, feats: list[str], price: str | None, facts: FactSheet) -> Script:
    t_low = smart_lower(prop.property_type)
    lines: list[ScriptLine] = [
        ScriptLine(
            role="hook",
            vo=f"Introducing {prop.name}, {article(t_low)} {t_low} in {prop.location}.",
            headline=prop.name, subline=prop.location, kicker=prop.property_type,
            fact_ids=["name", "type", "location"],
        )
    ]
    for i, f in enumerate(feats):
        lead = "Highlights include {f}." if i == 0 else LEADS[(i - 1) % len(LEADS)]
        lines.append(ScriptLine(
            role="feature", vo=lead.format(f=smart_lower(f)), headline=f, kicker="HIGHLIGHT",
            fact_ids=[f"f{i + 1}"],
        ))
    if price:
        lines.append(ScriptLine(role="price", vo=f"Priced at {price}.", headline=price, kicker="PRICE",
                                subline=prop.property_type, fact_ids=["price", "type"]))
    cta = prop.cta.rstrip(" .!")
    vo = f"{cta}."
    ids = ["cta"]
    sub = None
    if prop.contact:
        ids.append("contact")
        sub = prop.contact
        if "@" in prop.contact:
            vo += f" Write to us at {prop.contact}."
        elif is_phone(prop.contact):
            vo += f" Call {prop.contact}."
        else:
            vo += f" Contact {prop.contact}."
    lines.append(ScriptLine(role="cta", vo=vo, headline=cta, subline=sub, kicker="ENQUIRE NOW", fact_ids=ids))
    return Script(lines=lines, source="template")


# --------------------------------------------------------------------------------------- verifier
def verify_script(script: Script, facts: FactSheet, strict_vocab: bool = False) -> list[str]:
    """Returns a list of violations (empty == grounded)."""
    problems: list[str] = []
    corpus = facts.corpus()
    fact_words = _words(corpus)
    fact_nums = {_norm_num(n) for n in NUM.findall(corpus)}
    known_ids = facts.ids()
    corpus_l = corpus.lower()

    if not script.lines or script.lines[0].role != "hook" or script.lines[-1].role != "cta":
        problems.append("script must start with a hook and end with a cta")

    for n, ln in enumerate(script.lines, 1):
        tag = f"line {n} ({ln.role})"
        for fid in ln.fact_ids:
            if fid not in known_ids:
                problems.append(f"{tag}: cites unknown fact id '{fid}'")
        if not ln.fact_ids:
            problems.append(f"{tag}: has no supporting fact ids")
        if len(ln.vo.split()) > 26:
            problems.append(f"{tag}: voice-over longer than 26 words")
        if len(ln.headline.split()) > 12:
            problems.append(f"{tag}: headline longer than 12 words")

        shown = [ln.vo, ln.headline] + ([ln.subline] if ln.subline else [])
        for text in shown:
            for num in NUM.findall(text):
                if _norm_num(num) not in fact_nums:
                    problems.append(f"{tag}: number '{num}' is not in the supplied facts")
            for w in _words(text):
                if w in CLAIM_WORDS and w not in fact_words:
                    problems.append(f"{tag}: unsupported claim word '{w}'")
            # mid-sentence capitalised tokens look like proper nouns -> must come from the facts
            for sent in re.split(r"(?<=[.!?])\s+", text):
                toks = WORD.findall(sent)
                for tok in toks[1:]:
                    if tok[:1].isupper() and tok.lower() not in fact_words and tok.lower() not in GLUE_VOCAB:
                        problems.append(f"{tag}: unknown proper noun '{tok}'")
        vocab_check = shown + ([ln.kicker] if ln.kicker else [])
        if strict_vocab:
            for text in vocab_check:
                for w in _words(text):
                    if w not in fact_words and w not in GLUE_VOCAB:
                        problems.append(f"{tag}: word '{w}' is neither a fact nor template vocabulary")
        # price must be quoted exactly
        if ln.role == "price" and "price" in known_ids:
            p = facts.get("price").text  # type: ignore[union-attr]
            if p.lower() not in (ln.vo + " " + ln.headline).lower():
                problems.append(f"{tag}: price not quoted exactly as supplied ('{p}')")
    del corpus_l
    return problems


# ------------------------------------------------------------------------------------------ LLM
LLM_SYSTEM = """You write 20-40 second voice-over scripts for real-estate promo videos.
HARD RULES:
- Use ONLY the facts provided. Never add amenities, distances, adjectives that imply quality (luxury, spacious,
  stunning...), urgency, investment claims, or any number/name not present in the facts.
- Quote the price exactly as given. Keep each voice-over line <= 20 words, each headline <= 8 words.
- Every line must list the fact ids it relies on in "fact_ids".
- Structure: first line role "hook", then one "feature" line per feature, optional "price" line, last line "cta".
Return ONLY JSON: {"lines":[{"role":"hook|feature|price|cta","vo":"...","headline":"...","subline":null,"fact_ids":["..."]}]}"""


class AnthropicLLM:
    """Thin wrapper; imported lazily so the base install has no network/SDK dependency."""

    def __init__(self, model: str | None = None):
        try:
            import anthropic  # type: ignore
        except ImportError:
            raise InputError("The 'anthropic' package is not installed.", "pip install 'listingreel[llm]'") from None
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise InputError("ANTHROPIC_API_KEY is not set.", "export it, or run with --llm none for the offline template")
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("LISTINGREEL_LLM_MODEL", "claude-sonnet-5-5")

    def complete(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model, max_tokens=1200, temperature=0.4, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _parse_llm_json(raw: str) -> Script:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON object in LLM output")
    data = json.loads(m.group(0))
    s = Script(lines=data["lines"], source="llm")
    for ln in s.lines:
        ln.kicker = {"feature": "HIGHLIGHT", "price": "PRICE", "cta": "ENQUIRE NOW"}.get(ln.role)
    return s


def llm_script(facts: FactSheet, llm: LLM) -> Script:
    user = "FACTS:\n" + "\n".join(f"[{f.id}] {f.label}: {f.text}" for f in facts.facts)
    return _parse_llm_json(llm.complete(LLM_SYSTEM, user))


def generate_script(
    prop: PropertyInput, feats: list[str], price: str | None, facts: FactSheet, llm: LLM | None = None
) -> tuple[Script, list[str]]:
    """Returns (verified script, warnings). Always succeeds: template is the safety net."""
    notes: list[str] = []
    if llm is not None:
        try:
            cand = llm_script(facts, llm)
            problems = verify_script(cand, facts, strict_vocab=False)
            if not problems:
                cand.notes.append("LLM script passed the grounding verifier.")
                return cand, notes
            notes.append("LLM script rejected by grounding verifier, template used: " + "; ".join(problems[:4]))
        except Exception as e:  # network, JSON, schema: never crash the pipeline
            notes.append(f"LLM unavailable/invalid ({type(e).__name__}); template used.")
    script = template_script(prop, feats, price, facts)
    problems = verify_script(script, facts, strict_vocab=True)
    if problems:  # a bug in our own template must be loud
        raise InputError("Internal script template failed grounding checks: " + "; ".join(problems[:3]))
    script.notes.append("Template script passed the strict grounding verifier.")
    return script, notes
