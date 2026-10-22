"""Pydantic-модели контракта. Источник правды — JSON Schema в agent/schemas/."""

from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


class Level(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class InsightType(str, Enum):
    risk = "risk"
    opportunity = "opportunity"
    mixed = "mixed"
    neutral = "neutral"


class Origin(str, Enum):
    ai = "ai"
    bank = "bank"


class SourceKind(str, Enum):
    news = "news"
    bank_analytics = "bank_analytics"
    client_profile = "client_profile"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(StrictModel):
    source_id: str
    kind: SourceKind
    title: str
    url: str | None = None
    published_at: str | None = None


class Fact(StrictModel):
    text: str
    source_ids: list[str] = Field(min_length=1)
    status: str = "confirmed"


class Assumption(StrictModel):
    text: str
    source_ids: list[str] = Field(min_length=1)
    status: str = "unconfirmed"


class WhatHappened(StrictModel):
    facts: list[Fact] = Field(min_length=1)
    assumptions: list[Assumption] = Field(default_factory=list)


class WhyItem(StrictModel):
    text: str
    client_features: list[str] = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class OriginClaim(StrictModel):
    text: str
    source_ids: list[str] = Field(min_length=1)
    origin: Origin


class Consequences(StrictModel):
    risks: list[OriginClaim] = Field(default_factory=list)
    opportunities: list[OriginClaim] = Field(default_factory=list)
    factors_to_watch: list[str] = Field(default_factory=list)


class Action(StrictModel):
    text: str
    rationale: str
    origin: Origin
    product_hint: str | None = None


class BankFragmentView(StrictModel):
    text: str
    doc_id: str
    doc_title: str
    fragment_id: str


class ClientFeatureUsed(StrictModel):
    feature_id: str
    label: str
    value: str
    csv_field: str


class Insight(StrictModel):
    insight_id: str
    client_id: str
    cluster_id: str
    generated_at: str
    headline: str
    type: InsightType
    urgency: Level
    impact: Level
    confidence: Level
    priority_score: float = Field(ge=0, le=1)
    what_happened: WhatHappened
    why_it_matters: list[WhyItem] = Field(min_length=1)
    consequences: Consequences
    actions: list[Action] = Field(min_length=1)
    bank_view: list[BankFragmentView] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    client_features_used: list[ClientFeatureUsed] = Field(default_factory=list)
    trace_id: str
    gaps: list[str] = Field(default_factory=list)


class ChatClaim(StrictModel):
    text: str
    origin: Origin
    source_ids: list[str] = Field(min_length=1)


class ChatAnswer(StrictModel):
    insight_id: str
    question: str
    answer: list[ChatClaim] = Field(min_length=1)
    sources: list[Source] = Field(min_length=1)
    confidence: Level
    trace_id: str
    gaps: list[str] = Field(default_factory=list)


class InsightsRequest(StrictModel):
    client_id: str = Field(min_length=1)
    period_from: str = Field(min_length=1)
    period_to: str = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1, le=50)
    role: str | None = None


class ChatRequest(StrictModel):
    insight_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    role: str | None = None


@lru_cache(maxsize=8)
def load_json_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Не найдена схема {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    schema = load_json_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def schema_errors(payload: dict[str, Any], schema_name: str) -> list[str]:
    validator = _validator(schema_name)
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    rendered: list[str] = []
    for err in errors:
        location = ".".join(str(part) for part in err.path) or "$"
        rendered.append(f"{location}: {err.message}")
    return rendered


def parse_insight(payload: dict[str, Any]) -> Insight:
    errors = schema_errors(payload, "insight.schema.json")
    if errors:
        raise ValueError("; ".join(errors))
    return Insight.model_validate(payload)


def parse_chat_answer(payload: dict[str, Any]) -> ChatAnswer:
    errors = schema_errors(payload, "chat_answer.schema.json")
    if errors:
        raise ValueError("; ".join(errors))
    return ChatAnswer.model_validate(payload)
