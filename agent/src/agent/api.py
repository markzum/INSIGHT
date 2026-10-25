"""HTTP API модуля. OpenAPI строится из типов FastAPI."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent import __version__
from agent.errors import ClientNotFound, InsightNotFound
from agent.loop import AgentRuntime
from agent.schemas.models import ChatAnswer, ChatRequest, Insight, InsightsRequest
from agent.settings import Settings


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str = Field(min_length=1)
    useful: bool
    comment: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    runtime = AgentRuntime.build(settings)
    app = FastAPI(title="ИНСАЙТ agent", version=__version__)
    app.state.settings = settings
    app.state.runtime = runtime

    @app.get("/agent/clients")
    def list_clients(client_id: str | None = None, limit: int = 12) -> dict:
        bounded = max(1, min(int(limit), 30))
        records = _pick_clients(runtime.directory, client_id, bounded)
        return {"clients": [_client_card(item) for item in records]}

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "mock_llm": settings.mock_llm,
            "clients": len(runtime.directory.clients),
            "news_clusters": len(runtime.news.clusters),
            "bank_fragments": len(runtime.bank.fragments),
            "bank_unreadable": len(runtime.bank.unreadable),
        }

    @app.post("/agent/insights", response_model=list[Insight])
    def create_insights(body: InsightsRequest) -> list[dict]:
        _check_period(body.period_from, body.period_to)
        _check_role(body.role)
        try:
            return runtime.run_insights(body.client_id, body.period_from, body.period_to, body.top_n, body.role)
        except ClientNotFound as exc:
            raise HTTPException(status_code=404, detail=f"клиент {exc} не найден") from exc

    @app.get("/agent/insights/{insight_id}", response_model=Insight)
    def get_insight(insight_id: str) -> dict:
        insight = runtime.traces.get_insight(insight_id)
        if insight is None:
            raise HTTPException(status_code=404, detail="инсайт не найден")
        return insight

    @app.post("/agent/chat", response_model=ChatAnswer)
    def chat(body: ChatRequest) -> dict:
        _check_role(body.role)
        try:
            return runtime.chat(body.insight_id, body.question, body.role)
        except InsightNotFound as exc:
            raise HTTPException(status_code=404, detail=f"инсайт {exc} не найден") from exc

    @app.get("/traces/{trace_id}")
    def get_trace(trace_id: str) -> dict:
        trace = runtime.traces.get_trace(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="trace не найден")
        return trace

    @app.post("/agent/feedback", status_code=204)
    def feedback(body: FeedbackRequest) -> None:
        if runtime.traces.get_insight(body.insight_id) is None:
            raise HTTPException(status_code=404, detail="инсайт не найден")
        runtime.traces.save_feedback(body.insight_id, body.useful, body.comment)

    return app


_PINNED_CLIENTS = (
    "C_LOAN",
    "C_DEPOSIT",
    "C_IMPORT",
    "C_DOMESTIC",
    "C_SUPPLIER",
    "C_GROUPMATE",
    "C_SPARSE",
    "U_9250",
)


def _pick_clients(directory, client_id: str | None, limit: int) -> list:
    chosen = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        if not candidate or candidate in seen:
            return
        record = directory.get(candidate)
        if record is None:
            return
        seen.add(record.client_id)
        chosen.append(record)

    add(client_id)
    for pinned in _PINNED_CLIENTS:
        add(pinned)
    if len(chosen) < limit and len(directory.clients) <= 40:
        for record in directory.clients.values():
            add(record.client_id)
            if len(chosen) >= limit:
                break
    elif len(chosen) < limit:
        ranked = sorted(directory.clients.values(), key=lambda item: item.revenue or 0, reverse=True)
        for record in ranked:
            add(record.client_id)
            if len(chosen) >= limit:
                break
    return chosen[:limit]


def _client_card(record) -> dict:
    return {
        "client_id": record.client_id,
        "okved_name": record.okved_name,
        "okved_code": record.okved_code,
        "region": record.region,
        "city": record.city,
        "revenue": record.revenue,
        "loan": record.loan,
        "deposit": record.deposit,
        "leasing": record.leasing,
        "guarantee": record.guarantee,
        "factoring": record.factoring,
        "fts_flag": record.fts_flag,
        "fts_country": record.fts_country,
        "workers": record.workers_fns or record.workers_spark,
        "client_type": record.client_type,
        "group_id": record.group_id,
    }


def _check_role(role: str | None) -> None:
    if role is not None and role not in {"ceo", "cfo", "treasurer"}:
        raise HTTPException(status_code=422, detail="role должен быть ceo, cfo или treasurer")


def _check_period(period_from: str, period_to: str) -> None:
    try:
        start = date.fromisoformat(period_from[:10])
        end = date.fromisoformat(period_to[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="период задаётся как YYYY-MM-DD") from exc
    if start > end:
        raise HTTPException(status_code=422, detail="period_from позже period_to")
