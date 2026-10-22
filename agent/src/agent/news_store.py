"""Чтение кластеров dedup. Битая строка пропускается, процесс не падает."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.news_model import NewsCluster
from agent.bank_index import _overlap, tokenize

log = logging.getLogger("agent.news")


class NewsStore:
    def __init__(self, clusters: list[NewsCluster]) -> None:
        self.clusters = [item for item in clusters if item.cluster_id]
        self.by_id = {item.cluster_id: item for item in self.clusters}

    @classmethod
    def load(cls, path: Path) -> "NewsStore":
        clusters: list[NewsCluster] = []
        if not path.is_file():
            log.warning("файл новостей не найден: %s", path)
            return cls(clusters)
        for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                log.warning("пропущена битая строка новостей %s:%s", path.name, line_no)
                continue
            if not isinstance(payload, dict) or not payload.get("cluster_id"):
                log.warning("пропущена строка без cluster_id %s:%s", path.name, line_no)
                continue
            clusters.append(NewsCluster.from_dict(payload))
        return cls(clusters)

    def get(self, cluster_id: str) -> NewsCluster | None:
        return self.by_id.get(cluster_id)

    def search(
        self,
        query: str = "",
        *,
        period_from: str | None = None,
        period_to: str | None = None,
        tags: list[str] | None = None,
        region: str | None = None,
        sector: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        wanted_tags = {tag.lower() for tag in tags or []}
        query_tokens = tokenize(query)
        region_token = (region or "").lower()
        sector_token = (sector or "").lower()
        ranked: list[tuple[float, NewsCluster]] = []
        for cluster in self.clusters:
            if not cluster.in_period(period_from, period_to):
                continue
            cluster_tags = {tag.lower() for tag in cluster.tags}
            if wanted_tags and not (wanted_tags & cluster_tags):
                continue
            text = cluster.searchable_text().lower()
            if region_token and region_token not in text:
                continue
            if sector_token and sector_token not in cluster_tags and sector_token not in text:
                continue
            overlap = _overlap(query_tokens, tokenize(text)) if query_tokens else 1.0
            if query_tokens and overlap == 0:
                continue
            ranked.append((float(overlap), cluster))
        ranked.sort(key=lambda item: (item[0], item[1].published_at), reverse=True)
        return [_public_cluster(cluster, score) for score, cluster in ranked[:limit]]


def _public_cluster(cluster: NewsCluster, score: float) -> dict:
    return {
        "cluster_id": cluster.cluster_id,
        "title": cluster.title,
        "summary": cluster.summary,
        "published_at": cluster.published_at,
        "tags": list(cluster.tags),
        "entities": list(cluster.entities),
        "score": score,
        "source_id": f"news:{cluster.cluster_id}",
    }


def cluster_payload(cluster: NewsCluster) -> dict:
    return {
        "cluster_id": cluster.cluster_id,
        "title": cluster.title,
        "summary": cluster.summary,
        "published_at": cluster.published_at,
        "entities": list(cluster.entities),
        "tags": list(cluster.tags),
        "source_id": f"news:{cluster.cluster_id}",
        "source_text": cluster.summary or cluster.title,
        "articles": [
            {
                "url": article.url,
                "source": article.source,
                "title": article.title,
                "published_at": article.published_at,
                "text": article.text,
                "source_id": f"news:{cluster.cluster_id}:{index}",
            }
            for index, article in enumerate(cluster.articles)
        ],
    }
