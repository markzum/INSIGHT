"""Инкрементальный индекс дубликатов. Новый документ не пересобирает чужие подписи."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dedup.features import (
    ALGORITHM_VERSION,
    JACCARD_THRESHOLD,
    MIN_NEAR_TOKENS,
    TITLE_THRESHOLD,
    facts_conflict,
    jaccard,
    lsh_buckets,
    prepare,
    title_cosine,
    within_window,
)
from dedup.models import DedupResult, Document, DuplicateRef, ProvenanceEdge

DEFAULT_WHITELIST = {
    "rbc.ru",
    "kommersant.ru",
    "tass.ru",
    "interfax.ru",
    "cbr.ru",
    "alfabank.ru",
}


class IdentityConflict(ValueError):
    """Тот же id уже сохранён с другим содержимым."""


class Deduplicator:
    def __init__(
        self,
        db_path: str | Path,
        tenant: str = "default",
        whitelist: Iterable[str] | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.tenant = tenant
        self.whitelist = {item.lower() for item in (whitelist or DEFAULT_WHITELIST)}
        self.aliases = dict(aliases or {})
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def add(self, document: Document | dict) -> DedupResult:
        doc = document if isinstance(document, Document) else Document.model_validate(document)
        with self._lock:
            return self._add(doc)

    def batch(self, documents: Iterable[Document | dict]) -> list[DedupResult]:
        return [self.add(document) for document in documents]

    def document(self, doc_id: str) -> Document:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM documents WHERE tenant = ? AND doc_id = ?",
                (self.tenant, doc_id),
            ).fetchone()
        if row is None:
            raise KeyError(doc_id)
        return Document.model_validate_json(row["payload"])

    def clusters(self) -> list[DedupResult]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT cluster_id FROM clusters WHERE tenant = ? ORDER BY cluster_id",
                (self.tenant,),
            ).fetchall()
            return [self._result(row["cluster_id"], method="identity", score=1.0, warnings=[]) for row in rows]

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                tenant TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL,
                features TEXT NOT NULL,
                norm_url TEXT,
                text_hash TEXT,
                content_kind TEXT NOT NULL,
                published_at TEXT,
                language TEXT,
                token_count INTEGER NOT NULL,
                PRIMARY KEY (tenant, doc_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dedup_url ON documents(tenant, norm_url);
            CREATE INDEX IF NOT EXISTS idx_dedup_hash ON documents(tenant, text_hash);
            CREATE TABLE IF NOT EXISTS lsh (
                tenant TEXT NOT NULL,
                band INTEGER NOT NULL,
                bucket TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                PRIMARY KEY (tenant, band, bucket, doc_id)
            );
            CREATE TABLE IF NOT EXISTS clusters (
                tenant TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                canonical_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content_kind TEXT NOT NULL,
                PRIMARY KEY (tenant, cluster_id)
            );
            CREATE TABLE IF NOT EXISTS members (
                tenant TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                method TEXT NOT NULL,
                similarity REAL NOT NULL,
                threshold REAL NOT NULL,
                PRIMARY KEY (tenant, doc_id)
            );
            CREATE TABLE IF NOT EXISTS provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                matched_document_id TEXT NOT NULL,
                method TEXT NOT NULL,
                similarity REAL NOT NULL,
                threshold REAL NOT NULL,
                features TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS title_df (
                tenant TEXT NOT NULL,
                token TEXT NOT NULL,
                df INTEGER NOT NULL,
                PRIMARY KEY (tenant, token)
            );
            CREATE TABLE IF NOT EXISTS title_stats (
                tenant TEXT NOT NULL PRIMARY KEY,
                docs INTEGER NOT NULL
            );
            """
        )
        self._conn.commit()

    def _add(self, doc: Document) -> DedupResult:
        fingerprint = _fingerprint(doc)
        existing = self._conn.execute(
            """
            SELECT documents.fingerprint, members.cluster_id
            FROM documents JOIN members ON documents.tenant = members.tenant AND documents.doc_id = members.doc_id
            WHERE documents.tenant = ? AND documents.doc_id = ?
            """,
            (self.tenant, doc.id),
        ).fetchone()
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                raise IdentityConflict(doc.id)
            return self._result(existing["cluster_id"], method="identity", score=1.0, warnings=["повтор того же документа"])

        prepared = prepare(doc, self.aliases)
        features = _feature_payload(prepared)
        candidate = self._best_candidate(doc, prepared, doc.language)
        warnings = list(prepared.warnings)
        if candidate is None:
            cluster_id = _new_cluster_id()
            self._insert_document(doc, fingerprint, features, prepared)
            self._conn.execute(
                "INSERT INTO clusters (tenant, cluster_id, canonical_id, version, content_kind) VALUES (?, ?, ?, 1, ?)",
                (self.tenant, cluster_id, doc.id, doc.content_kind),
            )
            self._conn.execute(
                "INSERT INTO members (tenant, doc_id, cluster_id, method, similarity, threshold) VALUES (?, ?, ?, 'identity', 1, 1)",
                (self.tenant, doc.id, cluster_id),
            )
            self._index_features(doc.id, prepared)
            self._conn.commit()
            return self._result(cluster_id, method="identity", score=1.0, warnings=warnings)

        cluster_id = candidate["cluster_id"]
        if candidate.get("blocked"):
            warnings.append(f"похожий документ {candidate['matched_id']} не объединён: {candidate['blocked']}")
            cluster_id = _new_cluster_id()
            self._insert_document(doc, fingerprint, features, prepared)
            self._conn.execute(
                "INSERT INTO clusters (tenant, cluster_id, canonical_id, version, content_kind) VALUES (?, ?, ?, 1, ?)",
                (self.tenant, cluster_id, doc.id, doc.content_kind),
            )
            self._conn.execute(
                "INSERT INTO members (tenant, doc_id, cluster_id, method, similarity, threshold) VALUES (?, ?, ?, 'identity', 1, 1)",
                (self.tenant, doc.id, cluster_id),
            )
            self._index_features(doc.id, prepared)
            self._conn.commit()
            return self._result(cluster_id, method="identity", score=1.0, warnings=warnings)

        self._insert_document(doc, fingerprint, features, prepared)
        self._index_features(doc.id, prepared)
        self._conn.execute(
            "INSERT INTO members (tenant, doc_id, cluster_id, method, similarity, threshold) VALUES (?, ?, ?, ?, ?, ?)",
            (self.tenant, doc.id, cluster_id, candidate["method"], candidate["score"], candidate["threshold"]),
        )
        self._conn.execute(
            """
            INSERT INTO provenance (
                tenant, cluster_id, document_id, matched_document_id, method, similarity, threshold, features, algorithm_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.tenant,
                cluster_id,
                doc.id,
                candidate["matched_id"],
                candidate["method"],
                candidate["score"],
                candidate["threshold"],
                json.dumps(candidate["features"], ensure_ascii=False),
                ALGORITHM_VERSION,
                _now(),
            ),
        )
        version = self._conn.execute(
            "SELECT version FROM clusters WHERE tenant = ? AND cluster_id = ?",
            (self.tenant, cluster_id),
        ).fetchone()["version"]
        canonical = self._choose_canonical(cluster_id)
        self._conn.execute(
            "UPDATE clusters SET canonical_id = ?, version = ? WHERE tenant = ? AND cluster_id = ?",
            (canonical, version + 1, self.tenant, cluster_id),
        )
        self._conn.commit()
        return self._result(cluster_id, method=candidate["method"], score=candidate["score"], warnings=warnings)

    def _best_candidate(self, doc: Document, prepared, language: str | None) -> dict[str, Any] | None:
        ids = self._candidate_ids(prepared)
        if not ids:
            return None
        df, n_docs = self._title_stats()
        scored: list[dict[str, Any]] = []
        blocked: dict[str, Any] | None = None
        for doc_id in ids:
            other = self._load(doc_id)
            if other is None or other["content_kind"] != doc.content_kind:
                continue
            match = self._match(prepared, other, df, n_docs, language)
            if match is None:
                continue
            match["cluster_id"] = other["cluster_id"]
            if match.get("blocked"):
                if blocked is None or match["score"] > blocked["score"]:
                    blocked = match
                continue
            scored.append(match)
        if not scored and blocked is not None:
            return blocked
        if not scored:
            return None
        scored.sort(key=lambda item: ({"url": 3, "text_hash": 2, "near_duplicate": 1}[item["method"]], item["score"]), reverse=True)
        seen_clusters: list[str] = []
        for match in scored:
            if match["cluster_id"] in seen_clusters:
                continue
            seen_clusters.append(match["cluster_id"])
            if self._complete_link(prepared, match["cluster_id"], df, n_docs, language):
                return match
        return blocked

    def _complete_link(self, prepared, cluster_id: str, df: dict[str, int], n_docs: int, language: str | None) -> bool:
        rows = self._conn.execute(
            "SELECT doc_id FROM members WHERE tenant = ? AND cluster_id = ?",
            (self.tenant, cluster_id),
        ).fetchall()
        for row in rows:
            other = self._load(row["doc_id"])
            if other is None:
                return False
            match = self._match(prepared, other, df, n_docs, language)
            if match is None or match.get("blocked"):
                return False
        return True

    def _match(self, prepared, other: dict[str, Any], df: dict[str, int], n_docs: int, language: str | None) -> dict[str, Any] | None:
        peer = other["prepared"]
        conflict = facts_conflict(prepared, peer)
        exact_url = bool(prepared.norm_url and prepared.norm_url == peer.norm_url)
        exact_hash = bool(prepared.text_hash and prepared.text_hash == peer.text_hash)
        if exact_url or exact_hash:
            method = "url" if exact_url else "text_hash"
            payload = {
                "matched_id": other["doc_id"],
                "method": method,
                "score": 1.0,
                "threshold": 1.0,
                "features": {"cluster_policy": "complete_link", "conflict": conflict},
            }
            if conflict:
                payload["blocked"] = conflict
            return payload
        if len(prepared.tokens) < MIN_NEAR_TOKENS or len(peer.tokens) < MIN_NEAR_TOKENS:
            return None
        if prepared.published is None or peer.published is None or not within_window(prepared.published, peer.published):
            return None
        if not language or not other["language"] or language != other["language"]:
            return None
        score_j = jaccard(set(prepared.shingles), set(peer.shingles))
        score_t = title_cosine(list(prepared.title_stems), list(peer.title_stems), df, n_docs)
        if score_j < JACCARD_THRESHOLD or score_t < TITLE_THRESHOLD:
            return None
        payload = {
            "matched_id": other["doc_id"],
            "method": "near_duplicate",
            "score": round(min(score_j, score_t), 4),
            "threshold": JACCARD_THRESHOLD,
            "features": {
                "cluster_policy": "complete_link",
                "jaccard": round(score_j, 4),
                "title_cosine": round(score_t, 4),
                "jaccard_threshold": JACCARD_THRESHOLD,
                "title_threshold": TITLE_THRESHOLD,
                "conflict": conflict,
            },
        }
        if conflict:
            payload["blocked"] = conflict
        return payload

    def _candidate_ids(self, prepared) -> set[str]:
        found: set[str] = set()
        if prepared.norm_url:
            rows = self._conn.execute(
                "SELECT doc_id FROM documents WHERE tenant = ? AND norm_url = ?",
                (self.tenant, prepared.norm_url),
            ).fetchall()
            found.update(row["doc_id"] for row in rows)
        if prepared.text_hash:
            rows = self._conn.execute(
                "SELECT doc_id FROM documents WHERE tenant = ? AND text_hash = ?",
                (self.tenant, prepared.text_hash),
            ).fetchall()
            found.update(row["doc_id"] for row in rows)
        for band, bucket in lsh_buckets(list(prepared.signature)):
            rows = self._conn.execute(
                "SELECT doc_id FROM lsh WHERE tenant = ? AND band = ? AND bucket = ?",
                (self.tenant, band, bucket),
            ).fetchall()
            found.update(row["doc_id"] for row in rows)
        return found

    def _load(self, doc_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT documents.doc_id, documents.features, documents.payload, documents.content_kind, documents.language,
                   members.cluster_id
            FROM documents JOIN members USING (tenant, doc_id)
            WHERE documents.tenant = ? AND documents.doc_id = ?
            """,
            (self.tenant, doc_id),
        ).fetchone()
        if row is None:
            return None
        from dedup.features import Prepared

        raw = json.loads(row["features"])
        prepared = Prepared(
            tokens=tuple(raw["tokens"]),
            title_stems=tuple(raw["title_stems"]),
            shingles=tuple(raw["shingles"]),
            signature=tuple(raw["signature"]),
            text_hash=raw.get("text_hash"),
            norm_url=raw.get("norm_url"),
            domain=raw.get("domain"),
            numbers=frozenset(raw.get("numbers") or []),
            dates=frozenset(raw.get("dates") or []),
            entities=frozenset(raw.get("entities") or []),
            polarities=frozenset(raw.get("polarities") or []),
            published=_parse_feature_time(raw.get("published")),
            warnings=tuple(raw.get("warnings") or []),
        )
        return {
            "doc_id": row["doc_id"],
            "prepared": prepared,
            "content_kind": row["content_kind"],
            "language": row["language"],
            "cluster_id": row["cluster_id"],
            "payload": json.loads(row["payload"]),
        }

    def _insert_document(self, doc: Document, fingerprint: str, features: dict, prepared) -> None:
        self._conn.execute(
            """
            INSERT INTO documents (
                tenant, doc_id, fingerprint, payload, features, norm_url, text_hash, content_kind, published_at, language, token_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.tenant,
                doc.id,
                fingerprint,
                doc.model_dump_json(),
                json.dumps(features, ensure_ascii=False),
                prepared.norm_url,
                prepared.text_hash,
                doc.content_kind,
                doc.published_at,
                doc.language,
                len(prepared.tokens),
            ),
        )

    def _index_features(self, doc_id: str, prepared) -> None:
        for band, bucket in lsh_buckets(list(prepared.signature)):
            self._conn.execute(
                "INSERT OR IGNORE INTO lsh (tenant, band, bucket, doc_id) VALUES (?, ?, ?, ?)",
                (self.tenant, band, bucket, doc_id),
            )
        if prepared.title_stems:
            stats = self._conn.execute("SELECT docs FROM title_stats WHERE tenant = ?", (self.tenant,)).fetchone()
            docs = int(stats["docs"]) if stats else 0
            self._conn.execute(
                "INSERT INTO title_stats (tenant, docs) VALUES (?, ?) ON CONFLICT(tenant) DO UPDATE SET docs = excluded.docs",
                (self.tenant, docs + 1),
            )
            for token in set(prepared.title_stems):
                self._conn.execute(
                    """
                    INSERT INTO title_df (tenant, token, df) VALUES (?, ?, 1)
                    ON CONFLICT(tenant, token) DO UPDATE SET df = df + 1
                    """,
                    (self.tenant, token),
                )

    def _title_stats(self) -> tuple[dict[str, int], int]:
        stats = self._conn.execute("SELECT docs FROM title_stats WHERE tenant = ?", (self.tenant,)).fetchone()
        n_docs = int(stats["docs"]) if stats else 0
        rows = self._conn.execute("SELECT token, df FROM title_df WHERE tenant = ?", (self.tenant,)).fetchall()
        return {row["token"]: int(row["df"]) for row in rows}, n_docs

    def _choose_canonical(self, cluster_id: str) -> str:
        rows = self._conn.execute(
            """
            SELECT documents.doc_id, documents.payload, documents.features, documents.published_at, documents.token_count
            FROM members JOIN documents USING (tenant, doc_id)
            WHERE members.tenant = ? AND members.cluster_id = ?
            """,
            (self.tenant, cluster_id),
        ).fetchall()

        def sort_key(row) -> tuple:
            payload = json.loads(row["payload"])
            features = json.loads(row["features"])
            domain = features.get("domain") or ""
            preferred = 0 if domain in self.whitelist else 1
            published = row["published_at"] or "9999-99-99T99:99:99Z"
            entities = len(features.get("entities") or [])
            return (preferred, published, -int(row["token_count"]), -entities, payload["id"])

        return sorted(rows, key=sort_key)[0]["doc_id"]

    def _result(self, cluster_id: str, method: str, score: float, warnings: list[str]) -> DedupResult:
        cluster = self._conn.execute(
            "SELECT canonical_id, version, content_kind FROM clusters WHERE tenant = ? AND cluster_id = ?",
            (self.tenant, cluster_id),
        ).fetchone()
        rows = self._conn.execute(
            """
            SELECT documents.payload FROM members JOIN documents USING (tenant, doc_id)
            WHERE members.tenant = ? AND members.cluster_id = ?
            """,
            (self.tenant, cluster_id),
        ).fetchall()
        documents = [Document.model_validate_json(row["payload"]) for row in rows]
        by_id = {item.id: item for item in documents}
        canonical = by_id[cluster["canonical_id"]]
        duplicates = [
            DuplicateRef(id=item.id, source=item.source, url=item.url, published_at=item.published_at, title=item.title)
            for item in documents
            if item.id != canonical.id
        ]
        edges = self._conn.execute(
            """
            SELECT document_id, matched_document_id, method, similarity, threshold, features, algorithm_version
            FROM provenance WHERE tenant = ? AND cluster_id = ? ORDER BY id
            """,
            (self.tenant, cluster_id),
        ).fetchall()
        provenance = [
            ProvenanceEdge(
                document_id=row["document_id"],
                matched_document_id=row["matched_document_id"],
                method=row["method"],
                similarity_score=row["similarity"],
                threshold=row["threshold"],
                features=json.loads(row["features"]),
                algorithm_version=row["algorithm_version"],
            )
            for row in edges
        ]
        bank = []
        if cluster["content_kind"] == "bank_analysis":
            bank = [
                {"id": item.id, "source": item.source, "url": item.url, "title": item.title, "published_at": item.published_at}
                for item in documents
            ]
        return DedupResult(
            canonical_id=canonical.id,
            cluster_id=cluster_id,
            cluster_version=int(cluster["version"]),
            canonical_document=canonical,
            duplicates=duplicates,
            method=method,  # type: ignore[arg-type]
            similarity_score=score,
            provenance=provenance,
            warnings=warnings,
            bank_analysis=bank,
        )


def _fingerprint(doc: Document) -> str:
    payload = json.dumps(doc.model_dump(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _feature_payload(prepared) -> dict[str, Any]:
    return {
        "tokens": list(prepared.tokens),
        "title_stems": list(prepared.title_stems),
        "shingles": list(prepared.shingles),
        "signature": list(prepared.signature),
        "text_hash": prepared.text_hash,
        "norm_url": prepared.norm_url,
        "domain": prepared.domain,
        "numbers": sorted(prepared.numbers),
        "dates": sorted(prepared.dates),
        "entities": sorted(prepared.entities),
        "polarities": sorted(prepared.polarities),
        "published": prepared.published.isoformat() if prepared.published else None,
        "warnings": list(prepared.warnings),
    }


def _parse_feature_time(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


def _new_cluster_id() -> str:
    return "clu_" + uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
