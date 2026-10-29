"""Pydantic data contracts. Everything crossing a module boundary is typed."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PropertyInput(BaseModel):
    """What the sales team supplies. Unknown keys are rejected so typos are caught."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=80)
    location: str = Field(min_length=2, max_length=100)
    property_type: str = Field(min_length=2, max_length=60)
    price: str | int | float | None = None
    currency: str = Field(default="₹", max_length=4)
    features: list[str] = Field(default_factory=list, max_length=30)
    cta: str = Field(default="Book your site visit today", min_length=2, max_length=80)
    contact: str | None = Field(default=None, max_length=60)
    images: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    id: str
    label: str
    text: str


class FactSheet(BaseModel):
    """The ONLY source of truth the script may draw from."""

    facts: list[Fact]

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def ids(self) -> set[str]:
        return {f.id for f in self.facts}

    def corpus(self) -> str:
        return " \n".join(f.text for f in self.facts)


Role = Literal["hook", "feature", "price", "cta"]


class ScriptLine(BaseModel):
    role: Role
    vo: str
    headline: str
    subline: str | None = None
    kicker: str | None = None
    fact_ids: list[str] = Field(default_factory=list)  # provenance


class Script(BaseModel):
    lines: list[ScriptLine]
    source: Literal["template", "llm"] = "template"
    notes: list[str] = Field(default_factory=list)
