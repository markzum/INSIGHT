"""Индекс аналитики банка: текст PDF или .txt, поиск BM25, макропоказатель только цитатой."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from rank_bm25 import BM25Okapi

from agent.paths import CONFIG_DIR, load_yaml

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
    def __init__(self, fragments: list[Fragment], unreadable: list[str] | None = None) -> None:
        self.fragments = fragments
        self.unreadable = list(unreadable or [])
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
        documents = [
            {
                "text": item.text,
                "source_id": item.source_id,
                "title": item.doc_title,
                "published_at": item.published_at,
                "kind": "bank_analytics",
                "doc_id": item.doc_id,
                "fragment_id": item.fragment_id,
            }
            for item in self.fragments
        ]
        result = match_indicator(name, documents)
        if not result["found"] and self.unreadable:
            result["reason"] = (
                "в PDF нет текстового слоя, одноимённый .txt не найден, "
                "и в уже прочитанных материалах нет фразы с этим показателем"
            )
            result["unreadable"] = list(self.unreadable)
        return result

    @classmethod
    def load(cls, path: Path, cache_dir: Path | None = None, catalog_path: Path | None = None) -> "BankIndex":
        catalog = _catalog(catalog_path or CONFIG_DIR / "bank_docs.yaml")
        fingerprint = _fingerprint(path)
        if cache_dir is not None:
            cached = _read_cache(cache_dir, fingerprint)
            if cached is not None:
                return cached
        fragments = cls.from_text_dir(path).fragments
        unreadable: list[str] = []
        if path.is_dir():
            for pdf in sorted(path.glob("*.pdf")):
                meta = catalog.get(pdf.name, {})
                body = _pdf_text(pdf)
                if not body.strip():
                    sidecar = pdf.with_suffix(".txt")
                    if sidecar.is_file():
                        body = sidecar.read_text(encoding="utf-8")
                if not body.strip():
                    unreadable.append(pdf.name)
                    continue
                doc_id = str(meta.get("doc_id") or pdf.stem)
                title = str(meta.get("title") or pdf.stem)
                published = meta.get("date")
                for index, piece in enumerate(chunk_text(body)):
                    fragments.append(
                        Fragment(
                            fragment_id=f"{doc_id}-{index}",
                            doc_id=doc_id,
                            doc_title=title,
                            published_at=str(published) if published else None,
                            section="document",
                            text=piece,
                        )
                    )
        index = cls(fragments, unreadable=unreadable)
        if cache_dir is not None:
            _write_cache(cache_dir, fingerprint, index)
        return index

    @classmethod
    def from_text_dir(cls, path: Path) -> "BankIndex":
        fragments: list[Fragment] = []
        if not path.is_dir():
            return cls(fragments)
        for file in sorted(path.glob("*.txt")):
            # Одноимённый .txt рядом с PDF — это расшифровка того же материала.
            # Его подхватит обход PDF и присвоит doc_id из каталога.
            if file.with_suffix(".pdf").is_file():
                continue
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


def match_indicator(name: str, documents: list[dict]) -> dict:
    spec = _patterns().get(name)
    if spec is None:
        return {
            "found": False,
            "name": name,
            "reason": f"показатель {name} не описан в config/macro_patterns.yaml",
            "matches": [],
        }
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in spec.get("regexes") or []]
    matches = []
    for document in documents:
        text = document.get("text") or ""
        for regex in compiled:
            found = regex.search(text)
            if not found:
                continue
            sentence = _sentence(text, found.start(), found.end())
            if sentence not in text and sentence not in " ".join(text.split()):
                continue
            matches.append(
                {
                    "text": sentence,
                    "source_id": document.get("source_id"),
                    "title": document.get("title"),
                    "published_at": document.get("published_at"),
                    "kind": document.get("kind") or "bank_analytics",
                    "doc_id": document.get("doc_id"),
                    "fragment_id": document.get("fragment_id"),
                }
            )
            break
    matches.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    if not matches:
        return {
            "found": False,
            "name": name,
            "label": spec.get("label"),
            "reason": "в материалах банка и новостях нет фразы с этим показателем",
            "matches": [],
        }
    return {
        "found": True,
        "name": name,
        "label": spec.get("label"),
        "reason": None,
        "value_text": matches[0]["text"],
        "matches": matches[:3],
    }


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start))
    right_dot = text.find(".", end)
    right_nl = text.find("\n", end)
    candidates = [pos for pos in (right_dot, right_nl) if pos != -1]
    right = min(candidates) if candidates else len(text)
    if right == right_dot:
        right += 1
    return " ".join(text[left + 1 : right].split())


def _patterns() -> dict:
    data = load_yaml(CONFIG_DIR / "macro_patterns.yaml")
    return dict(data.get("indicators") or {})


def _catalog(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    data = load_yaml(path)
    return {str(item["file"]): item for item in data.get("documents") or [] if item.get("file")}


def _pdf_text(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
    except Exception:
        return ""
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(pages).strip()


def _fingerprint(path: Path) -> str:
    parts = []
    if path.is_dir():
        for file in sorted(path.glob("*")):
            if file.suffix.lower() in {".pdf", ".txt"} and file.is_file():
                stat = file.stat()
                parts.append(f"{file.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _read_cache(cache_dir: Path, fingerprint: str) -> BankIndex | None:
    path = cache_dir / "bank_index.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    fragments = [Fragment(**item) for item in payload.get("fragments") or []]
    return BankIndex(fragments, unreadable=list(payload.get("unreadable") or []))


def _write_cache(cache_dir: Path, fingerprint: str, index: BankIndex) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint": fingerprint,
        "unreadable": index.unreadable,
        "fragments": [
            {
                "fragment_id": item.fragment_id,
                "doc_id": item.doc_id,
                "doc_title": item.doc_title,
                "published_at": item.published_at,
                "section": item.section,
                "text": item.text,
            }
            for item in index.fragments
        ],
    }
    (cache_dir / "bank_index.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
