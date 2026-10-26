from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.api import app
from gateway.mapping import agent_role, question_answer, workspace
from gateway.pipeline import refresh


def test_role_and_workspace_shape() -> None:
    assert agent_role("financial_director") == "cfo"
    dataset = workspace([_client()], [_insight()], "C_LOAN", "financial_director")
    assert dataset["schema_version"] == "3.0"
    assert dataset["is_demo"] is False
    event = dataset["events"][0]
    assert event["company_ids"] == ["C_LOAN"]
    assert event["priority"] == "high"
    assert event["facts"][0]["source_id"] == "news:clu_rate:0"
    assert event["bank_view"]["status"] == "available"
    assert any(item["type"] == "bank_analysis" and item["url"].startswith("https://") for item in event["sources"])
    assert "REVENUE_COMPANY" in {item["field"] for item in dataset["companies"][0]["features"]}
    assert any(item["field"] == "SAMPLE_SHARE" for item in dataset["companies"][0]["features"])
    assert event["trace_id"] == "tr-1"
    assert event["ai_interpretation"]["risks"][0]["origin"] == "ai"
    assert any("выборки" in item for item in dataset["companies"][0]["missing_data"])
    answer = question_answer({"answer": [{"text": "Ставка сохранена.", "source_ids": ["news:clu_rate:0"], "origin": "ai"}]})
    assert answer["is_demo"] is False
    assert answer["source_ids"] == ["news:clu_rate:0"]


def test_workspace_route_uses_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/clients":
            return httpx.Response(200, json={"clients": [_client()]})
        if request.url.path == "/agent/insights":
            assert request.read()
            return httpx.Response(200, json=[_insight()])
        return httpx.Response(404)

    monkeypatch.setattr("gateway.api._client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    from gateway import api as api_module

    api_module._cache.clear()
    response = TestClient(app).get("/workspace", params={"company_id": "MISSING", "role": "ceo"})
    assert response.status_code == 200
    body = response.json()
    assert body["companies"][0]["id"] == "C_LOAN"
    assert body["events"][0]["company_ids"] == ["C_LOAN"]


def test_feedback_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/feedback":
            seen["body"] = request.read().decode()
            return httpx.Response(204)
        return httpx.Response(404)

    monkeypatch.setattr("gateway.api._client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    response = TestClient(app).post("/insights/ins-1/feedback", json={"useful": True, "comment": ""})
    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert "ins-1" in seen["body"]


def test_refresh_stores_clusters_and_falls_back(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/collect":
            return httpx.Response(200, json={"documents": []})
        if request.url.path == "/documents":
            return httpx.Response(200, json=[{"id": "n1"}])
        if request.url.path == "/batch":
            return httpx.Response(200, json=[])
        if request.url.path == "/export/jsonl":
            return httpx.Response(200, text='{"cluster_id":"clu_live"}\n')
        return httpx.Response(404)

    destination = tmp_path / "clusters.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        body = refresh("http://parser", "http://dedup", "demo-token", destination, client, attempts=1)
    assert "clu_live" in body
    assert destination.read_text(encoding="utf-8").startswith("{")

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    fallback = tmp_path / "fallback.jsonl"
    with httpx.Client(transport=httpx.MockTransport(broken)) as client:
        body = refresh("http://parser", "http://dedup", "demo-token", fallback, client, attempts=1)
    assert "clu_rate" in body


def _client() -> dict:
    return {
        "client_id": "C_LOAN",
        "okved_name": "Строительство",
        "okved_code": "41",
        "region": "Иркутская область",
        "city": "Иркутск",
        "revenue": 20_000_000_000,
        "loan": "1",
        "deposit": "0",
        "leasing": "",
        "guarantee": "",
        "factoring": "",
        "fts_flag": "0",
        "fts_country": "",
        "workers": "101-250",
        "client_type": "CLIENT",
        "group_id": "group_loan",
        "market_position": "4-е место из 4 в отрасли «Оптовая торговля» по выручке внутри выборки клиентов банка",
        "sample_share": "20.6% выручки выборки клиентов в отрасли «Оптовая торговля». Это не доля всего рынка: объём рынка вне выборки не передан",
        "group_summary": "group_loan: 2 компании в выборке",
        "related_companies": ["C_GROUPMATE"],
    }


def _insight() -> dict:
    return {
        "insight_id": "ins-1",
        "client_id": "C_LOAN",
        "cluster_id": "clu_rate",
        "generated_at": "2026-09-11T12:00:00Z",
        "headline": "Ключевая ставка сохранена",
        "type": "risk",
        "urgency": "medium",
        "impact": "high",
        "confidence": "medium",
        "priority_score": 0.42,
        "what_happened": {"facts": [{"text": "Ставка сохранена на уровне 14%.", "source_ids": ["news:clu_rate:0"], "status": "confirmed"}], "assumptions": []},
        "why_it_matters": [{"text": "У компании есть кредиты.", "client_features": ["loan"], "source_ids": ["profile:C_LOAN"]}],
        "consequences": {"risks": [{"text": "Стоимость кредита не снижается.", "source_ids": ["news:clu_rate:0"], "origin": "ai"}], "opportunities": [], "factors_to_watch": []},
        "actions": [{"text": "Пересчитать график платежей.", "rationale": "Ставка осталась высокой.", "origin": "ai", "product_hint": None}],
        "bank_view": [{"text": "Банк сохраняет оценку ставки.", "doc_id": "policy", "doc_title": "Обзор ставки", "fragment_id": "f1"}],
        "sources": [{"source_id": "news:clu_rate:0", "kind": "news", "title": "Ставка", "url": "https://example.test/rate", "published_at": "2026-09-11T12:00:00Z"}],
        "client_features_used": [{"feature_id": "loan", "label": "Кредиты", "value": "да", "csv_field": "CL_PRODUCT_LOA_MARK"}],
        "trace_id": "tr-1",
        "gaps": [],
    }
