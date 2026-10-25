"""Нормализация URL и текста, MinHash и сходство заголовков. Сеть не используется."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALGORITHM_VERSION = "1.0.0"
MIN_HASH_TOKENS = 12
MIN_NEAR_TOKENS = 30
JACCARD_THRESHOLD = 0.72
TITLE_THRESHOLD = 0.65
NEAR_WINDOW_DAYS = 7
MINHASH_SIZE = 64
BANDS = 16
ROWS = 4
PRIME = 2_147_483_647

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?%?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DMY = re.compile(r"\b(\d{1,2})[.](\d{1,2})[.](\d{4})\b")
_RUS_DATE = re.compile(
    r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
    re.IGNORECASE,
)
_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_RU_ENDINGS = (
    "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими",
    "ах", "ях", "ов", "ев", "ей", "ий", "ый", "ой", "ая", "яя",
    "ое", "ее", "ые", "ие", "ам", "ям", "ом", "ем",
    "у", "ю", "а", "я", "ы", "и", "е", "о",
)
_EN_ENDINGS = ("ing", "ed", "es", "s")
_QUERY_DROP = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "yclid", "gclid", "_openstat", "from", "ref",
}
_POLARITY = {
    "снизил": "cut", "снизила": "cut", "снизили": "cut",
    "понизил": "cut", "понизила": "cut", "понизили": "cut",
    "повысил": "raise", "повысила": "raise", "повысили": "raise",
    "сохранил": "hold", "сохранила": "hold", "сохранили": "hold",
    "сохранен": "hold", "сохранено": "hold",
    "оставил": "hold", "оставила": "hold", "оставили": "hold",
}

# Фиксированные перестановки: результат не зависит от процесса.
_PERMS: list[tuple[int, int]] = []
_seed = 20_240_911
for _ in range(MINHASH_SIZE):
    _seed = (1103515245 * _seed + 12345) % PRIME
    coeff = _seed or 1
    _seed = (1103515245 * _seed + 12345) % PRIME
    shift = _seed
    _PERMS.append((coeff, shift))


@dataclass(frozen=True)
class Prepared:
    tokens: tuple[str, ...]
    title_stems: tuple[str, ...]
    shingles: tuple[str, ...]
    signature: tuple[int, ...]
    text_hash: str | None
    norm_url: str | None
    domain: str | None
    numbers: frozenset[str]
    dates: frozenset[str]
    entities: frozenset[str]
    polarities: frozenset[str]
    published: datetime | None
    warnings: tuple[str, ...]


def canonicalize_url(url: str | None, aliases: dict[str, str] | None = None) -> str | None:
    if not url or not str(url).strip():
        return None
    raw = str(url).strip()
    if aliases:
        raw = aliases.get(raw, raw)
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _QUERY_DROP
    ]
    query.sort()
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host or None


def tokens_of(text: str) -> list[str]:
    return [token.lower().replace("ё", "е") for token in _TOKEN.findall(text or "")]


def stem(token: str) -> str:
    if token.isdigit() or len(token) < 5:
        return token
    if re.search(r"[а-я]", token):
        for ending in _RU_ENDINGS:
            if token.endswith(ending) and len(token) - len(ending) >= 4:
                return token[: -len(ending)]
        return token
    for ending in _EN_ENDINGS:
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            return token[: -len(ending)]
    return token


def shingles_of(words: list[str], width: int = 3) -> list[str]:
    if len(words) < width:
        return []
    return [" ".join(words[index : index + width]) for index in range(len(words) - width + 1)]


def text_hash(words: list[str]) -> str | None:
    if len(words) < MIN_HASH_TOKENS:
        return None
    payload = " ".join(words).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _shingle_hash(value: str) -> int:
    digest = hashlib.sha1(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % PRIME


def minhash(shingles: list[str]) -> list[int]:
    if not shingles:
        return []
    hashed = [_shingle_hash(item) for item in shingles]
    signature = []
    for coeff, shift in _PERMS:
        signature.append(min((coeff * item + shift) % PRIME for item in hashed))
    return signature


def lsh_buckets(signature: list[int]) -> list[tuple[int, str]]:
    if len(signature) != MINHASH_SIZE:
        return []
    buckets = []
    for band in range(BANDS):
        chunk = signature[band * ROWS : (band + 1) * ROWS]
        raw = ",".join(str(item) for item in chunk).encode("utf-8")
        buckets.append((band, hashlib.sha1(raw).hexdigest()))
    return buckets


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    if union == 0:
        return 0.0
    return len(left & right) / union


def title_cosine(left: list[str], right: list[str], df: dict[str, int], n_docs: int) -> float:
    if not left or not right:
        return 0.0

    def weights(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        total = sum(counts.values())
        scored = {}
        for token, count in counts.items():
            idf = math.log((n_docs + 1) / (df.get(token, 0) + 1)) + 1.0
            scored[token] = (count / total) * idf
        return scored

    first = weights(left)
    second = weights(right)
    keys = set(first) | set(second)
    dot = sum(first.get(key, 0.0) * second.get(key, 0.0) for key in keys)
    left_norm = math.sqrt(sum(value * value for value in first.values()))
    right_norm = math.sqrt(sum(value * value for value in second.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def extract_numbers(text: str) -> set[str]:
    found = set()
    for raw in _NUMBER.findall(text or ""):
        percent = raw.endswith("%")
        body = raw[:-1].replace(",", ".") if percent else raw.replace(",", ".")
        try:
            value = float(body)
        except ValueError:
            continue
        token = f"{value:.4f}".rstrip("0").rstrip(".")
        found.add(f"{token}%" if percent else token)
    return found


def extract_dates(text: str) -> set[str]:
    found = set(_ISO_DATE.findall(text or ""))
    for day, month, year in _DMY.findall(text or ""):
        found.add(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    for day, month_name, year in _RUS_DATE.findall(text or ""):
        month = _MONTHS[month_name.lower()]
        found.add(f"{int(year):04d}-{month:02d}-{int(day):02d}")
    return found


def extract_polarities(words: list[str]) -> set[str]:
    return {_POLARITY[word] for word in words if word in _POLARITY}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def within_window(left: datetime | None, right: datetime | None, days: int = NEAR_WINDOW_DAYS) -> bool:
    if left is None or right is None:
        return False
    return abs((left - right).total_seconds()) <= days * 86400


def prepare(document, aliases: dict[str, str] | None = None) -> Prepared:
    words = tokens_of(f"{document.title}\n{document.text}")
    title = [stem(token) for token in tokens_of(document.title)]
    grams = shingles_of(words)
    url = canonicalize_url(document.url, aliases)
    notes = []
    if not (document.text or "").strip():
        notes.append("пустой текст: общее сравнение отключено")
    elif len(words) < MIN_NEAR_TOKENS:
        notes.append("короткий текст: near-duplicate отключён")
    if not url:
        notes.append("нет канонического URL")
    if not document.language:
        notes.append("язык не указан: межъязыковое сравнение отключено")
    entities = document.raw_meta.get("entities") if isinstance(document.raw_meta, dict) else None
    entity_set = frozenset(str(item).strip().lower() for item in entities or [] if str(item).strip())
    return Prepared(
        tokens=tuple(words),
        title_stems=tuple(title),
        shingles=tuple(grams),
        signature=tuple(minhash(grams)),
        text_hash=text_hash(words),
        norm_url=url,
        domain=domain_of(url),
        numbers=frozenset(extract_numbers(f"{document.title}\n{document.text}")),
        dates=frozenset(extract_dates(f"{document.title}\n{document.text}")),
        entities=entity_set,
        polarities=frozenset(extract_polarities(words)),
        published=parse_time(document.published_at),
        warnings=tuple(notes),
    )


def facts_conflict(left: Prepared, right: Prepared) -> str | None:
    if left.numbers and right.numbers and left.numbers != right.numbers:
        return "числа различаются"
    if left.dates and right.dates and left.dates != right.dates:
        return "даты различаются"
    if left.entities and right.entities and not (left.entities & right.entities):
        return "сущности не пересекаются"
    if left.polarities and right.polarities and left.polarities != right.polarities:
        return "оценка события различается"
    return None
