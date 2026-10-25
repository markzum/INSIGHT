"""Входной документ и результат дедупликации."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ContentKind = Literal["external_news", "bank_analysis"]
Method = Literal["identity", "url", "text_hash", "near_duplicate"]


class Document(BaseModel):
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


class DuplicateRef(BaseModel):
    id: str
    source: str
    url: str | None = None
    published_at: str | None = None
    title: str = ""


class ProvenanceEdge(BaseModel):
    document_id: str
    matched_document_id: str
    method: Method
    similarity_score: float
    threshold: float
    features: dict[str, Any] = Field(default_factory=dict)
    algorithm_version: str


class DedupResult(BaseModel):
    canonical_id: str
    cluster_id: str
    cluster_version: int
    canonical_document: Document
    duplicates: list[DuplicateRef]
    method: Method
    similarity_score: float
    provenance: list[ProvenanceEdge]
    warnings: list[str] = Field(default_factory=list)
    confirmed_facts: list[Any] = Field(default_factory=list)
    bank_analysis: list[dict[str, Any]] = Field(default_factory=list)
    model_inferences: list[Any] = Field(default_factory=list)
