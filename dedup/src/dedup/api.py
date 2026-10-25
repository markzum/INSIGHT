"""HTTP-доступ к индексу. Клиент определяется только серверным токеном."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response

from dedup import __version__
from dedup.core import Deduplicator, IdentityConflict
from dedup.export import cluster_record
from dedup.models import DedupResult, Document

log = logging.getLogger("dedup.api")

app = FastAPI(title="ИНСАЙТ dedup", version=__version__)
_engines: dict[str, Deduplicator] = {}


def _keys() -> dict[str, str]:
    raw = os.environ.get("DEDUP_API_KEYS", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("DEDUP_API_KEYS должен быть JSON-объектом токен → tenant")
    return {str(key): str(value) for key, value in parsed.items()}


def tenant(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="нужен bearer-токен")
    token = authorization.split(" ", 1)[1].strip()
    mapped = _keys().get(token)
    if not mapped:
        raise HTTPException(status_code=401, detail="токен не принят")
    return mapped


def engine_for(name: str) -> Deduplicator:
    if name not in _engines:
        path = Path(os.environ.get("DEDUP_DB", "dedup.sqlite"))
        _engines[name] = Deduplicator(path, tenant=name)
    return _engines[name]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.post("/documents", response_model=DedupResult)
def add_document(body: Document, name: str = Depends(tenant)) -> DedupResult:
    try:
        result = engine_for(name).add(body)
    except IdentityConflict as exc:
        raise HTTPException(status_code=409, detail=f"id {exc} уже сохранён с другим текстом") from exc
    log.info("dedup tenant=%s doc=%s cluster=%s method=%s", name, body.id, result.cluster_id, result.method)
    return result


@app.post("/batch", response_model=list[DedupResult])
def add_batch(body: list[Document], name: str = Depends(tenant)) -> list[DedupResult]:
    try:
        return engine_for(name).batch(body)
    except IdentityConflict as exc:
        raise HTTPException(status_code=409, detail=f"id {exc} уже сохранён с другим текстом") from exc


@app.get("/export/jsonl")
def export_jsonl(name: str = Depends(tenant)) -> Response:
    engine = engine_for(name)
    lines = [json.dumps(cluster_record(engine, cluster), ensure_ascii=False) for cluster in engine.clusters()]
    body = ("\n".join(lines) + "\n") if lines else ""
    log.info("dedup tenant=%s export_clusters=%s", name, len(lines))
    return Response(body, media_type="application/x-ndjson")


@app.get("/clusters", response_model=list[DedupResult])
def list_clusters(name: str = Depends(tenant)) -> list[DedupResult]:
    return engine_for(name).clusters()


@app.get("/clusters/{cluster_id}", response_model=DedupResult)
def get_cluster(cluster_id: str, name: str = Depends(tenant)) -> DedupResult:
    for cluster in engine_for(name).clusters():
        if cluster.cluster_id == cluster_id:
            return cluster
    raise HTTPException(status_code=404, detail="кластер не найден")
