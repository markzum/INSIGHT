"""Список источников. Новые ленты добавляются в config/feeds.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

HOME = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class FeedSpec:
    id: str
    source: str
    kind: str
    url: str
    hosts: frozenset[str]
    fixture: Path | None
    fetch_body: bool


@dataclass(frozen=True)
class LocalSpec:
    id: str
    source: str
    kind: str
    path: Path


@dataclass(frozen=True)
class Settings:
    home: Path
    offline: bool
    db_path: Path
    feeds: tuple[FeedSpec, ...]
    local_dirs: tuple[LocalSpec, ...]
    taxonomy_path: Path
    max_items: int
    max_bytes: int
    timeout: float
    feed_ids: frozenset[str] | None


def load_settings(home: Path | None = None) -> Settings:
    root = Path(home or os.environ.get("PARSER_HOME") or HOME)
    offline = os.environ.get("PARSER_OFFLINE", "1") != "0"
    catalog = yaml.safe_load((root / "config" / "feeds.yaml").read_text(encoding="utf-8")) or {}
    selected = os.environ.get("PARSER_FEED_IDS", "").strip()
    feed_ids = frozenset(part.strip() for part in selected.split(",") if part.strip()) or None
    feeds = tuple(
        spec
        for spec in (_feed(raw, root) for raw in catalog.get("feeds") or [])
        if spec is not None and _enabled(spec, offline) and (feed_ids is None or spec.id in feed_ids)
    )
    local = [_local(raw, root) for raw in catalog.get("local_dirs") or []]
    extra = os.environ.get("PARSER_BANK_DIR", "").strip()
    if extra:
        local.append(LocalSpec("bank-dir", "Альфа-Банк", "bank_analysis", Path(extra)))
    if feed_ids is not None:
        local = [item for item in local if item.id in feed_ids or item.id == "bank-dir"]
    db_raw = os.environ.get("PARSER_DB", "").strip()
    db_path = Path(db_raw) if db_raw else root / "data" / "parser.sqlite"
    return Settings(
        home=root,
        offline=offline,
        db_path=db_path,
        feeds=feeds,
        local_dirs=tuple(item for item in local if item is not None),
        taxonomy_path=root / "config" / "taxonomy.yaml",
        max_items=_int_env("PARSER_MAX_ITEMS", 30, 1, 200),
        max_bytes=_int_env("PARSER_MAX_BYTES", 1_000_000, 10_000, 5_000_000),
        timeout=float(os.environ.get("PARSER_TIMEOUT", "10")),
        feed_ids=feed_ids,
    )


def _feed(raw: dict, root: Path) -> FeedSpec | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    kind = str(raw.get("kind") or "external_news")
    if kind not in {"external_news", "bank_analysis"}:
        return None
    hosts = frozenset(_apex(str(host)) for host in raw.get("hosts") or [] if str(host).strip())
    fixture_raw = str(raw.get("fixture") or "").strip()
    fixture = (root / fixture_raw) if fixture_raw else None
    return FeedSpec(
        id=str(raw["id"]),
        source=str(raw.get("source") or raw["id"]),
        kind=kind,
        url=str(raw.get("url") or "").strip(),
        hosts=hosts,
        fixture=fixture,
        fetch_body=bool(raw.get("fetch_body", True)),
    )


def _local(raw: dict, root: Path) -> LocalSpec | None:
    if not isinstance(raw, dict) or not raw.get("path"):
        return None
    kind = str(raw.get("kind") or "bank_analysis")
    if kind not in {"external_news", "bank_analysis"}:
        return None
    return LocalSpec(
        id=str(raw.get("id") or "local"),
        source=str(raw.get("source") or "Альфа-Банк"),
        kind=kind,
        path=root / str(raw["path"]),
    )


def _enabled(feed: FeedSpec, offline: bool) -> bool:
    if offline:
        return feed.fixture is not None
    return feed.url.startswith("https://")


def _apex(host: str) -> str:
    name = host.lower().rstrip(".")
    return name[4:] if name.startswith("www.") else name


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, low), high)
