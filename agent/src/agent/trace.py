"""Журнал запусков и сохранённые инсайты."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS insights (
                insight_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_id TEXT NOT NULL,
                useful INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def save_insight(self, insight: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO insights (insight_id, client_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    insight["insight_id"],
                    insight["client_id"],
                    json.dumps(insight, ensure_ascii=False),
                    _now(),
                ),
            )
            self._conn.commit()

    def get_insight(self, insight_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT payload FROM insights WHERE insight_id = ?", (insight_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_trace(self, trace: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO traces (trace_id, payload, created_at) VALUES (?, ?, ?)",
                (trace["trace_id"], json.dumps(trace, ensure_ascii=False), _now()),
            )
            self._conn.commit()

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT payload FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_feedback(self, insight_id: str, useful: bool, comment: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO feedback (insight_id, useful, comment, created_at) VALUES (?, ?, ?, ?)",
                (insight_id, 1 if useful else 0, comment, _now()),
            )
            self._conn.commit()

    def feedback_summary(self) -> dict[str, float | int]:
        row = self._conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(useful), 0) AS yes FROM feedback").fetchone()
        total = int(row["n"])
        useful = int(row["yes"])
        return {"total": total, "useful": useful, "useful_rate": (useful / total) if total else 0.0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
