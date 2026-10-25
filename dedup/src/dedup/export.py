"""Кластеры в JSONL, который читает модуль agent."""

from __future__ import annotations

import json
from pathlib import Path

from dedup.core import Deduplicator
from dedup.models import DedupResult, Document


def export_engine(engine: Deduplicator, destination: Path) -> int:
    lines = [json.dumps(cluster_record(engine, cluster), ensure_ascii=False) for cluster in engine.clusters()]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def cluster_record(engine: Deduplicator, cluster: DedupResult) -> dict:
    documents = [engine.document(doc_id) for doc_id in [cluster.canonical_id, *[item.id for item in cluster.duplicates]]]
    canonical = documents[0]
    entities: list[str] = []
    tags: list[str] = []
    for document in documents:
        _collect(document, "entities", entities)
        _collect(document, "tags", tags)
    summary = (canonical.text or canonical.title).strip()
    if len(summary) > 1200:
        summary = summary[:1200].rstrip()
    return {
        "cluster_id": cluster.cluster_id,
        "title": canonical.title,
        "summary": summary,
        "published_at": canonical.published_at or "",
        "articles": [
            {
                "url": document.url or "",
                "source": document.source,
                "title": document.title,
                "published_at": document.published_at or "",
                "text": document.text,
            }
            for document in documents
        ],
        "entities": entities,
        "tags": tags,
    }


def _collect(document: Document, key: str, bucket: list[str]) -> None:
    for item in document.raw_meta.get(key) or []:
        text = str(item)
        if text not in bucket:
            bucket.append(text)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    documents = json.loads((root / "data" / "documents.json").read_text(encoding="utf-8"))
    db_path = root / "reports" / "export.sqlite"
    if db_path.exists():
        db_path.unlink()
    engine = Deduplicator(db_path, tenant="export")
    try:
        engine.batch(documents)
        destination = root / "reports" / "clusters.jsonl"
        count = export_engine(engine, destination)
    finally:
        engine.close()
    print(f"{count} clusters -> {destination}")


if __name__ == "__main__":
    main()
