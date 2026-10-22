"""Индекс аналитики банка. PDF-загрузка добавляется отдельно, поиск уже общий."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return [token.lower().replace("ё", "е") for token in _TOKEN.findall(text or "")]


def _overlap(query_tokens: list[str], text_tokens: list[str]) -> float:
    if not query_tokens:
        return 0.0
    hits = 0
    for query in query_tokens:
        if any(_same_stem(query, token) for token in text_tokens):
            hits += 1
    return float(hits)


def _same_stem(left: str, right: str) -> bool:
    """Общее начало длиной от 4 и короткое окончание. «ставка» совпадает со «ставку», но не с «поставка»."""
    if left == right:
        return True
    if len(left) < 4 or len(right) < 4:
        return False
    shared = 0
    for char_left, char_right in zip(left, right):
        if char_left != char_right:
            break
        shared += 1
    if shared < 4:
        return False
    return (len(left) - shared) <= 3 and (len(right) - shared) <= 3


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= size:
        return [cleaned]
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        chunks.append(cleaned[start : start + size])
        if start + size >= len(cleaned):
            break
        start += size - overlap
    return chunks


@dataclass
class Fragment:
    fragment_id: str
    doc_id: str
    doc_title: str
    published_at: str | None
    section: str
    text: str

    @property
    def source_id(self) -> str:
        return f"bank:{self.doc_id}:{self.fragment_id}"

    def public(self, score: float | None = None) -> dict:
        payload = {
            "fragment_id": self.fragment_id,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "published_at": self.published_at,
            "section": self.section,
            "text": self.text,
            "source_id": self.source_id,
        }
        if score is not None:
            payload["score"] = round(float(score), 4)
        return payload


class BankIndex:
    def __init__(self, fragments: list[Fragment]) -> None:
        self.fragments = fragments
        self.by_id = {item.fragment_id: item for item in fragments}
        corpus = [tokenize(item.text) or ["пусто"] for item in fragments]
        self._bm25 = BM25Okapi(corpus) if fragments else None

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        if not self.fragments or not (query or "").strip() or self._bm25 is None:
            return []
        query_tokens = tokenize(query)
        scores = [float(item) for item in self._bm25.get_scores(query_tokens)]
        # У rank-bm25 на корпусе из пары документов IDF редкого слова схлопывается в ноль.
        # Тогда остаётся пересечение токенов; на полном индексе PDF работает BM25.
        if not scores or max(scores) <= 0:
            scores = [_overlap(query_tokens, tokenize(item.text)) for item in self.fragments]
        ranked = sorted(enumerate(scores), key=lambda item: float(item[1]), reverse=True)
        results = []
        for index, score in ranked:
            if score <= 0:
                continue
            results.append(self.fragments[index].public(float(score)))
            if len(results) >= top_k:
                break
        return results

    def macro(self, name: str) -> dict:
        return {
            "found": False,
            "name": name,
            "reason": "показатель не найден в материалах банка и новостях",
            "matches": [],
        }

    @classmethod
    def load(cls, path: Path) -> "BankIndex":
        """Каталог .txt сейчас. PDF подключаются тем же методом позже."""
        return cls.from_text_dir(path)

    @classmethod
    def from_text_dir(cls, path: Path) -> "BankIndex":
        fragments: list[Fragment] = []
        if not path.is_dir():
            return cls(fragments)
        for file in sorted(path.glob("*.txt")):
            raw = file.read_text(encoding="utf-8").strip()
            if not raw:
                continue
            lines = raw.splitlines()
            title = lines[0].strip() or file.stem
            body = "\n".join(lines[1:]).strip() or raw
            for index, piece in enumerate(chunk_text(body)):
                fragments.append(
                    Fragment(
                        fragment_id=f"{file.stem}-{index}",
                        doc_id=file.stem,
                        doc_title=title,
                        published_at=None,
                        section="document",
                        text=piece,
                    )
                )
        return cls(fragments)
