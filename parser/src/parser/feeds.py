"""RSS, Atom, HTML и нормализация полей документа."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

_QUERY_DROP = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "yclid", "gclid", "_openstat", "from", "ref",
}
_SKIP_TAGS = {"script", "style", "noscript"}
_BREAK_TAGS = {"p", "br", "div", "h1", "h2", "h3", "li", "tr", "blockquote"}


class FeedParseError(ValueError):
    """Лента не разобралась как RSS или Atom."""


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str
    guid: str
    published_raw: str
    summary: str
    content: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in _SKIP_TAGS:
            self._skip += 1
        if self._skip:
            return
        if name == "title":
            self._in_title = True
        if name in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name == "title":
            self._in_title = False
        if name in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if self._skip == 0 and name in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
        else:
            self.parts.append(data)


def html_to_text(raw: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(raw or "")
    parser.close()
    return clean_text("".join(parser.parts)), clean_text("".join(parser.title))


def clean_text(value: str) -> str:
    text = unescape(value or "").replace("\xa0", " ").replace("\r", "")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def decode_bytes(raw: bytes, content_type: str | None = None) -> str:
    if content_type:
        match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, re.IGNORECASE)
        if match:
            return raw.decode(match.group(1), errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    head = raw[:180].lower()
    if b"windows-1251" in head or b"cp1251" in head:
        return raw.decode("cp1251", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def normalize_xml_declaration(text: str) -> str:
    head, sep, tail = text.partition("?>")
    if not sep or "encoding=" not in head.lower():
        return text
    head = re.sub(r"encoding\s*=\s*['\"][^'\"]+['\"]", 'encoding="utf-8"', head, count=1, flags=re.IGNORECASE)
    return head + sep + tail


def parse_feed(xml_text: str, limit: int = 200) -> list[FeedItem]:
    prepared = normalize_xml_declaration(xml_text or "")
    try:
        root = ElementTree.fromstring(prepared)
    except ElementTree.ParseError as exc:
        raise FeedParseError("лента не является XML") from exc
    name = _local(root.tag)
    if name == "rss":
        channel = next((child for child in root if _local(child.tag) == "channel"), None)
        nodes = [] if channel is None else [child for child in channel if _local(child.tag) == "item"]
        items = [_rss_item(node) for node in nodes]
    elif name == "feed":
        items = [_atom_entry(node) for node in root if _local(node.tag) == "entry"]
    elif name == "rdf":
        items = [_rss_item(node) for node in root if _local(node.tag) == "item"]
    else:
        raise FeedParseError("ожидался RSS или Atom")
    return [item for item in items if item.title or item.link or item.summary or item.content][:limit]


def to_utc_z(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    parsed = None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_language(text: str) -> str | None:
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 20:
        return None
    cyrillic = sum(1 for char in letters if "а" <= char.lower() <= "я" or char.lower() == "ё")
    ratio = cyrillic / len(letters)
    if ratio >= 0.6:
        return "ru"
    if ratio <= 0.2:
        return "en"
    return None


def canonicalize_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    parts = urlsplit(str(url).strip())
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


def _rss_item(node: ElementTree.Element) -> FeedItem:
    fields = {_local(child.tag): (child.text or "") for child in list(node)}
    content = fields.get("encoded") or ""
    return FeedItem(
        title=clean_text(fields.get("title") or ""),
        link=(fields.get("link") or "").strip(),
        guid=(fields.get("guid") or "").strip(),
        published_raw=(fields.get("pubdate") or fields.get("date") or fields.get("published") or "").strip(),
        summary=fields.get("description") or "",
        content=content,
    )


def _atom_entry(node: ElementTree.Element) -> FeedItem:
    title = ""
    link = ""
    guid = ""
    published = ""
    summary = ""
    content = ""
    for child in list(node):
        name = _local(child.tag)
        if name == "title":
            title = child.text or ""
        elif name == "id":
            guid = (child.text or "").strip()
        elif name == "updated" and not published:
            published = (child.text or "").strip()
        elif name == "published":
            published = (child.text or "").strip()
        elif name == "summary":
            summary = child.text or ""
        elif name == "content":
            content = child.text or ""
        elif name == "link" and not link:
            rel = child.attrib.get("rel", "alternate")
            if rel == "alternate":
                link = child.attrib.get("href", "").strip()
    return FeedItem(clean_text(title), link, guid, published, summary, content)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()
