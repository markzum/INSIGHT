"""Уже собранные документы. Повтор того же текста не создаёт новую запись."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from parser.models import ParsedDocument


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                doc_id TEXT PRIMARY KEY,
                base_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                feed_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_parser_base ON items(base_id, revision)")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def latest(self, base_id: str) -> tuple[int, str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT revision, fingerprint FROM items WHERE base_id = ? ORDER BY revision DESC LIMIT 1",
                (base_id,),
            ).fetchone()
        if row is None:
            return None
        return int(row["revision"]), str(row["fingerprint"])

    def add(self, base_id: str, revision: int, fingerprint: str, feed_id: str, document: ParsedDocument, fetched_at: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO items (doc_id, base_id, revision, fingerprint, feed_id, payload, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (document.id, base_id, revision, fingerprint, feed_id, document.model_dump_json(), fetched_at),
            )
            self._conn.commit()

    def documents(self, limit: int = 200) -> list[ParsedDocument]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT payload FROM items AS item
                WHERE revision = (SELECT MAX(revision) FROM items WHERE base_id = item.base_id)
                ORDER BY fetched_at, doc_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [ParsedDocument.model_validate_json(row["payload"]) for row in rows]
