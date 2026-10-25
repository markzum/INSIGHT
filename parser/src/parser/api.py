"""HTTP-сбор. Имя вызывающего берётся только из серверного токена."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from parser import __version__
from parser.config import load_settings
from parser.fetch import make_fetcher
from parser.models import CollectResult, ParsedDocument
from parser.pipeline import collect
from parser.store import Store

log = logging.getLogger("parser.api")
app = FastAPI(title="ИНСАЙТ parser", version=__version__)
_stores: dict[str, Store] = {}


def _keys() -> dict[str, str]:
    raw = os.environ.get("PARSER_API_KEYS", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("PARSER_API_KEYS должен быть JSON-объектом токен → имя")
    return {str(key): str(value) for key, value in parsed.items()}


def actor(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="нужен bearer-токен")
    token = authorization.split(" ", 1)[1].strip()
    mapped = _keys().get(token)
    if not mapped:
        raise HTTPException(status_code=401, detail="токен не принят")
    return mapped


def store_for(path: Path) -> Store:
    key = str(path)
    if key not in _stores:
        _stores[key] = Store(path)
    return _stores[key]


@app.get("/health")
def health() -> dict:
    settings = load_settings()
    return {
        "status": "ok",
        "version": __version__,
        "offline": settings.offline,
        "feeds": [feed.id for feed in settings.feeds],
    }


@app.get("/feeds")
def feeds(name: str = Depends(actor)) -> list[dict]:
    settings = load_settings()
    log.info("parser actor=%s feeds=%s", name, len(settings.feeds))
    return [
        {"id": feed.id, "source": feed.source, "kind": feed.kind, "hosts": sorted(feed.hosts)}
        for feed in settings.feeds
    ]


@app.post("/collect", response_model=CollectResult)
def collect_route(limit: int = Query(default=0, ge=0, le=200), name: str = Depends(actor)) -> CollectResult:
    settings = load_settings()
    if limit:
        settings = replace(settings, max_items=limit)
    result = collect(
        settings,
        make_fetcher(settings.offline, settings.timeout, settings.max_bytes),
        store_for(settings.db_path),
    )
    log.info(
        "parser actor=%s new=%s skipped=%s warnings=%s",
        name,
        len(result.documents),
        result.skipped,
        len(result.warnings),
    )
    return result


@app.get("/documents", response_model=list[ParsedDocument])
def documents(limit: int = Query(default=50, ge=1, le=500), name: str = Depends(actor)) -> list[ParsedDocument]:
    del name
    settings = load_settings()
    return store_for(settings.db_path).documents(limit)
