"""Фронтенд ходит сюда. Шлюз вызывает агента и отдаёт рабочее пространство версии 3.0."""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from gateway import __version__
from gateway.mapping import agent_role, question_answer, workspace

app = FastAPI(title="ИНСАЙТ gateway", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
_cache: dict[tuple[str, str, str, str], list[dict]] = {}


class QuestionRequest(BaseModel):
    company_id: str = ""
    role: str = "financial_director"
    question: str = Field(min_length=1)


class FeedbackBody(BaseModel):
    useful: bool
    comment: str = ""


def _agent() -> str:
    return os.environ.get("AGENT_URL", "http://127.0.0.1:8000").rstrip("/")


def _period() -> tuple[str, str]:
    return os.environ.get("GATEWAY_PERIOD_FROM", "2026-09-01"), os.environ.get("GATEWAY_PERIOD_TO", "2026-10-31")


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(360.0, connect=5.0))


@app.get("/health")
def health() -> dict:
    try:
        with _client() as client:
            response = client.get(f"{_agent()}/health")
            response.raise_for_status()
            agent = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="агент недоступен") from exc
    return {"status": "ok", "version": __version__, "agent": agent}


@app.get("/workspace")
def get_workspace(
    company_id: str = Query(default=""),
    role: str = Query(default="financial_director"),
) -> dict:
    period_from, period_to = _period()
    try:
        with _client() as client:
            listed = client.get(f"{_agent()}/agent/clients", params={"client_id": company_id, "limit": 12})
            listed.raise_for_status()
            clients = listed.json().get("clients") or []
            known = {item["client_id"] for item in clients}
            resolved = company_id if company_id in known else (clients[0]["client_id"] if clients else "")
            insights = _insights(client, resolved, agent_role(role), period_from, period_to)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="не удалось собрать рабочее пространство") from exc
    return workspace(clients, insights, resolved or company_id, role)


@app.post("/insights/{insight_id}/questions")
def ask(insight_id: str, body: QuestionRequest) -> dict:
    try:
        with _client() as client:
            response = client.post(
                f"{_agent()}/agent/chat",
                json={"insight_id": insight_id, "question": body.question, "role": agent_role(body.role)},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="агент не ответил") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="событие не найдено")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="агент не ответил")
    return question_answer(response.json())


@app.post("/insights/{insight_id}/feedback")
def send_feedback(insight_id: str, body: FeedbackBody) -> dict:
    try:
        with _client() as client:
            response = client.post(
                f"{_agent()}/agent/feedback",
                json={"insight_id": insight_id, "useful": body.useful, "comment": body.comment or None},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="агент не принял отзыв") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="событие не найдено")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="агент не принял отзыв")
    return {"status": "saved"}


def _insights(client: httpx.Client, client_id: str, role: str, period_from: str, period_to: str) -> list[dict[str, Any]]:
    if not client_id:
        return []
    key = (client_id, role, period_from, period_to)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    response = client.post(
        f"{_agent()}/agent/insights",
        json={
            "client_id": client_id,
            "period_from": period_from,
            "period_to": period_to,
            "top_n": int(os.environ.get("GATEWAY_TOP_N", "3")),
            "role": role,
        },
    )
    if response.status_code == 404:
        _cache[key] = []
        return []
    response.raise_for_status()
    payload = response.json()
    _cache[key] = payload
    return payload
