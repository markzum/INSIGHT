"""Метки и факты из текста. Словаря совпадает с признаками агента, модель не вызывается."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_QUOTE = re.compile(r"«([^»]{2,80})»")


@dataclass(frozen=True)
class Taxonomy:
    tags: tuple[tuple[str, tuple[str, ...]], ...]
    entities: tuple[str, ...]


def load_taxonomy(path: Path) -> Taxonomy:
    if not path.is_file():
        return Taxonomy((), ())
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tags = []
    for name, words in (raw.get("tags") or {}).items():
        cleaned = tuple(str(word).lower().replace("ё", "е") for word in words or [] if str(word).strip())
        if cleaned:
            tags.append((str(name), cleaned))
    entities = tuple(str(item).strip() for item in raw.get("entities") or [] if str(item).strip())
    return Taxonomy(tuple(tags), entities)


def classify(title: str, text: str, taxonomy: Taxonomy) -> dict[str, list[str]]:
    blob = f"{title}\n{text}".lower().replace("ё", "е")
    tags = [name for name, words in taxonomy.tags if any(word in blob for word in words)]
    entities: list[str] = []
    seen: set[str] = set()
    for name in taxonomy.entities:
        key = name.lower().replace("ё", "е")
        if key in blob and key not in seen:
            seen.add(key)
            entities.append(name)
    for quoted in _QUOTE.findall(f"{title}\n{text}"):
        key = quoted.lower().replace("ё", "е")
        if key not in seen:
            seen.add(key)
            entities.append(quoted.strip())
    facts: list[str] = []
    source = clean_sentence(f"{title}. {text}" if title else text)
    for sentence in _SENTENCE.split(source):
        piece = sentence.strip()
        if piece and any(char.isdigit() for char in piece):
            facts.append(piece[:300])
        if len(facts) >= 5:
            break
    return {"tags": tags, "entities": entities, "key_facts": facts}


def clean_sentence(value: str) -> str:
    return " ".join((value or "").split())
