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
