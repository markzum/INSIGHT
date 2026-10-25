"""Сбор лент и локальных файлов в документы."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from parser.classify import Taxonomy, classify, load_taxonomy
from parser.config import FeedSpec, LocalSpec, Settings
from parser.feeds import (
    FeedItem,
    FeedParseError,
    canonicalize_url,
    clean_text,
    decode_bytes,
    detect_language,
    html_to_text,
    parse_feed,
    to_utc_z,
)
from parser.fetch import FetchDenied, FetchFailed, Fetcher
from parser.models import CollectResult, ParsedDocument
from parser.store import Store

log = logging.getLogger("parser.pipeline")
_TEXT_LIMIT = 50_000
_TITLE_LIMIT = 300
_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z]")


def collect(settings: Settings, fetcher: Fetcher, store: Store) -> CollectResult:
    taxonomy = load_taxonomy(settings.taxonomy_path)
    documents: list[ParsedDocument] = []
    warnings: list[str] = []
    skipped = 0
    for feed in settings.feeds:
        batch, feed_skipped, notes = _ingest_feed(feed, settings, fetcher, store, taxonomy)
        documents.extend(batch)
        skipped += feed_skipped
        warnings.extend(notes)
        log.info("parser feed=%s new=%s skipped=%s", feed.id, len(batch), feed_skipped)
    for local in settings.local_dirs:
        batch, local_skipped, notes = _ingest_dir(local, store, taxonomy)
        documents.extend(batch)
        skipped += local_skipped
        warnings.extend(notes)
    return CollectResult(documents=documents, skipped=skipped, warnings=warnings)


def _ingest_feed(
    feed: FeedSpec,
    settings: Settings,
    fetcher: Fetcher,
    store: Store,
    taxonomy: Taxonomy,
) -> tuple[list[ParsedDocument], int, list[str]]:
    try:
        xml = _read_feed(feed, settings, fetcher)
        items = parse_feed(xml)
    except (FetchDenied, FetchFailed, FeedParseError, OSError) as exc:
        return [], 0, [f"{feed.id}: {exc}"]
    documents: list[ParsedDocument] = []
    skipped = 0
    notes: list[str] = []
    for item in items[: settings.max_items]:
        text, item_notes = _item_text(feed, item, settings, fetcher)
        notes.extend(item_notes)
        document, status = _remember(
            store,
            feed_id=feed.id,
            source=feed.source,
            kind=feed.kind,
            title=item.title,
            text=text,
            link=item.link,
            guid=item.guid,
            published_raw=item.published_raw,
            taxonomy=taxonomy,
        )
        if status == "skip":
            skipped += 1
        elif document is not None:
            documents.append(document)
    return documents, skipped, notes


def _ingest_dir(local: LocalSpec, store: Store, taxonomy: Taxonomy) -> tuple[list[ParsedDocument], int, list[str]]:
    if not local.path.is_dir():
        return [], 0, [f"{local.id}: каталог не найден"]
    root = local.path.resolve()
    documents: list[ParsedDocument] = []
    skipped = 0
    notes: list[str] = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".txt", ".html", ".htm"} or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            notes.append(f"{local.id}: файл вне каталога пропущен")
            continue
        title, published_raw, text = _read_local(resolved)
        document, status = _remember(
            store,
            feed_id=local.id,
            source=local.source,
            kind=local.kind,
            title=title,
            text=text,
            link="",
            guid=f"{local.id}:{path.name}",
            published_raw=published_raw or "",
            taxonomy=taxonomy,
        )
        if status == "skip":
            skipped += 1
        elif document is not None:
            documents.append(document)
    return documents, skipped, notes


def _read_feed(feed: FeedSpec, settings: Settings, fetcher: Fetcher) -> str:
    if settings.offline:
        if feed.fixture is None:
            raise FetchFailed("нет локальной копии ленты")
        return decode_bytes(feed.fixture.read_bytes())
    raw, _final = fetcher.get(feed.url, feed.hosts)
    return decode_bytes(raw)


def _item_text(feed: FeedSpec, item: FeedItem, settings: Settings, fetcher: Fetcher) -> tuple[str, list[str]]:
    summary = _prefer_text(item.summary, item.content)
    if settings.offline or not feed.fetch_body or not item.link:
        return summary, []
    try:
        raw, _final = fetcher.get(item.link, feed.hosts)
    except (FetchDenied, FetchFailed) as exc:
        return summary, [f"{feed.id}: полный текст не загружен ({exc})"]
    page, _title = html_to_text(decode_bytes(raw))
    if len(page) > len(summary):
        return page, []
    return summary, []


def _prefer_text(summary: str, content: str) -> str:
    choices = []
    for raw in (summary, content):
        if not raw or not raw.strip():
            continue
        text, _title = html_to_text(raw) if _HTML_TAG.search(raw) else (clean_text(raw), "")
        if text:
            choices.append(text)
    if not choices:
        return ""
    return max(choices, key=len)


def _remember(
    store: Store,
    *,
    feed_id: str,
    source: str,
    kind: str,
    title: str,
    text: str,
    link: str,
    guid: str,
    published_raw: str,
    taxonomy: Taxonomy,
) -> tuple[ParsedDocument | None, str]:
    title = clean_text(title)[:_TITLE_LIMIT]
    text = text[:_TEXT_LIMIT]
    published = to_utc_z(published_raw)
    canonical = canonicalize_url(link)
    base_id = _base_id(canonical, guid, title, source)
    digest = _fingerprint(title, text, published)
    current = store.latest(base_id)
    if current is not None and current[1] == digest:
        return None, "skip"
    revision = 1 if current is None else current[0] + 1
    doc_id = base_id if revision == 1 else f"{base_id}-r{revision}"
    notes = _quality_notes(title, text, canonical, link)
    meta = {
        "feed_id": feed_id,
        "guid": guid,
        "warnings": notes,
        **classify(title, text, taxonomy),
    }
    if revision > 1:
        meta["supersedes"] = base_id
        notes.append("текст по этому адресу изменился")
        meta["warnings"] = notes
    if link and canonical and link != canonical:
        meta["original_url"] = link
    document = ParsedDocument(
        id=doc_id,
        source=source,
        title=title,
        text=text,
        published_at=published,
        language=detect_language(f"{title}\n{text}"),
        url=canonical,
        raw_meta=meta,
        content_kind=kind,  # type: ignore[arg-type]
    )
    store.add(base_id, revision, digest, feed_id, document, _now())
    return document, "new"


def _read_local(path: Path) -> tuple[str, str | None, str]:
    if path.suffix.lower() in {".html", ".htm"}:
        text, title = html_to_text(decode_bytes(path.read_bytes()))
        return title, None, text
    raw = decode_bytes(path.read_bytes())
    lines = raw.splitlines()
    index = 0
    title = ""
    published = None
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        index = 1
    if index < len(lines) and lines[index].lower().startswith("published_at:"):
        published = lines[index].split(":", 1)[1].strip()
        index += 1
    body = "\n".join(lines[index:]).strip()
    if not title and body:
        title = body.split("\n", 1)[0][:_TITLE_LIMIT]
    return title, published, clean_text(body)


def _quality_notes(title: str, text: str, canonical: str | None, link: str) -> list[str]:
    notes = []
    if not title:
        notes.append("нет заголовка")
    if not text:
        notes.append("пустой текст")
    elif len(text) < 40:
        notes.append("короткий текст")
    if link and not canonical:
        notes.append("адрес материала не разобран")
    return notes


def _base_id(canonical: str | None, guid: str, title: str, source: str) -> str:
    key = canonical or guid or f"{source}\n{title}"
    return "news-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _fingerprint(title: str, text: str, published: str | None) -> str:
    payload = json.dumps({"published": published, "text": text, "title": title}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
