"""Офлайн- или сетевой сбор в data/documents.json."""

from __future__ import annotations

import json

from parser.config import load_settings
from parser.fetch import make_fetcher
from parser.pipeline import collect
from parser.store import Store


def main() -> None:
    settings = load_settings()
    store = Store(settings.db_path)
    try:
        result = collect(
            settings,
            make_fetcher(settings.offline, settings.timeout, settings.max_bytes),
            store,
        )
        destination = settings.home / "data" / "documents.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump() for item in store.documents(limit=5000)]
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        store.close()
    print(
        f"{len(result.documents)} new, {result.skipped} skipped, {len(payload)} stored -> {destination}"
    )
    for note in result.warnings:
        print(note)


if __name__ == "__main__":
    main()
