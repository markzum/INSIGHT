"""Парсер отдаёт документы, дедупликация собирает кластеры, агент читает JSONL."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

FALLBACK = """\
{"cluster_id":"clu_rate","title":"ЦБ сохранил ключевую ставку 14%","summary":"11 сентября 2026 года Банк России сохранил ключевую ставку на уровне 14%.","published_at":"2026-09-11T12:00:00Z","articles":[{"url":"https://example.test/rate","source":"Тестовый источник","title":"ЦБ сохранил ключевую ставку 14%","published_at":"2026-09-11T12:00:00Z","text":"11 сентября 2026 года Банк России сохранил ключевую ставку на уровне 14%."}],"entities":["Банк России"],"tags":["key_rate"]}
{"cluster_id":"clu_fx","title":"Курс юаня к рублю вырос на 3%","summary":"Оптовые импортёры пересматривают контракты: курс юаня вырос на 3% за неделю.","published_at":"2026-09-10T09:00:00Z","articles":[{"url":"https://example.test/fx","source":"Тестовый источник","title":"Курс юаня к рублю вырос на 3%","published_at":"2026-09-10T09:00:00Z","text":"Оптовые импортёры пересматривают контракты: курс юаня вырос на 3% за неделю."}],"entities":["юань"],"tags":["fx"]}
{"cluster_id":"clu_sport","title":"Футбольный клуб выиграл матч","summary":"Команда забила решающий гол в добавленное время.","published_at":"2026-09-12T18:00:00Z","articles":[{"url":"https://example.test/sport","source":"Тестовый источник","title":"Футбольный клуб выиграл матч","published_at":"2026-09-12T18:00:00Z","text":"Команда забила решающий гол в добавленное время."}],"entities":[],"tags":["sport"]}
"""


def refresh(parser_url: str, dedup_url: str, token: str, destination: Path, client: httpx.Client, attempts: int = 30) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    notes: list[str] = []
    try:
        _wait(client, f"{parser_url}/health", attempts)
        _wait(client, f"{dedup_url}/health", attempts)
        collected = client.post(f"{parser_url}/collect", headers=headers)
        if collected.status_code >= 400:
            notes.append(f"сбор новостей вернул {collected.status_code}")
        documents = client.get(f"{parser_url}/documents?limit=500", headers=headers)
        documents.raise_for_status()
        payload = documents.json()
        if payload:
            stored = client.post(f"{dedup_url}/batch", headers=headers, json=payload)
            if stored.status_code not in {200, 409}:
                notes.append(f"дедупликация вернула {stored.status_code}")
        exported = client.get(f"{dedup_url}/export/jsonl", headers=headers)
        exported.raise_for_status()
        body = exported.text.strip()
    except httpx.HTTPError as exc:
        notes.append(str(exc))
        body = ""
    if not body:
        body = FALLBACK.strip()
        notes.append("использована встроенная синтетическая лента")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body + "\n", encoding="utf-8")
    for note in notes:
        print(note)
    print(f"{len(body.splitlines())} clusters -> {destination}")
    return body


def _wait(client: httpx.Client, url: str, attempts: int = 30) -> None:
    last = "нет ответа"
    for _ in range(attempts):
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
            last = str(response.status_code)
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(1)
    raise httpx.HTTPError(f"{url}: {last}")


def main() -> None:
    destination = Path(os.environ.get("NEWS_PATH", "clusters.jsonl"))
    with httpx.Client(timeout=60) as client:
        refresh(
            os.environ.get("PARSER_URL", "http://127.0.0.1:8002").rstrip("/"),
            os.environ.get("DEDUP_URL", "http://127.0.0.1:8001").rstrip("/"),
            os.environ.get("DEDUP_TOKEN", "demo-token"),
            destination,
            client,
        )


if __name__ == "__main__":
    main()
