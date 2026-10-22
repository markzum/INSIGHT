"""Кластер новостей в формате контракта dedup."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Article:
    url: str
    source: str
    title: str
    published_at: str
    text: str

    @classmethod
    def from_dict(cls, payload: dict) -> "Article":
        return cls(
            url=str(payload.get("url") or ""),
            source=str(payload.get("source") or ""),
            title=str(payload.get("title") or ""),
            published_at=str(payload.get("published_at") or ""),
            text=str(payload.get("text") or ""),
        )


@dataclass
class NewsCluster:
    cluster_id: str
    title: str
    summary: str
    published_at: str
    articles: list[Article] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> "NewsCluster":
        articles = [Article.from_dict(item) for item in payload.get("articles") or [] if isinstance(item, dict)]
        return cls(
            cluster_id=str(payload.get("cluster_id") or ""),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            published_at=str(payload.get("published_at") or ""),
            articles=articles,
            entities=[str(item) for item in payload.get("entities") or []],
            tags=[str(item) for item in payload.get("tags") or []],
        )

    def searchable_text(self) -> str:
        parts = [self.title, self.summary, " ".join(self.entities), " ".join(self.tags)]
        parts.extend(article.text for article in self.articles)
        return "\n".join(part for part in parts if part)

    def calendar_day(self) -> date | None:
        raw = (self.published_at or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                return None

    def in_period(self, period_from: str | None, period_to: str | None) -> bool:
        day = self.calendar_day()
        if day is None:
            return False
        if period_from:
            if day < date.fromisoformat(period_from[:10]):
                return False
        if period_to:
            if day > date.fromisoformat(period_to[:10]):
                return False
        return True
