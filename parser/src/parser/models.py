"""Документ, который принимает модуль dedup."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ContentKind = Literal["external_news", "bank_analysis"]


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    source: str = ""
    title: str = ""
    text: str = ""
    published_at: str | None = None
    language: str | None = None
    url: str | None = None
    raw_meta: dict[str, Any] = Field(default_factory=dict)
    content_kind: ContentKind = "external_news"


class CollectResult(BaseModel):
    documents: list[ParsedDocument]
    skipped: int = 0
    warnings: list[str] = Field(default_factory=list)
