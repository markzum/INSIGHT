from fastapi.testclient import TestClient

from agent.api import create_app
from agent.settings import Settings


def _app(tmp_path):
    settings = Settings(
        mock_llm=True,
        db_path=tmp_path / "agent.sqlite",
        cache_dir=tmp_path / "llm",
        index_cache=tmp_path / "index",
        concurrency=2,
    )
    return TestClient(create_app(settings))


def test_health_and_e2e_trace(tmp_path) -> None:
    client = _app(tmp_path)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mock_llm"] is True
    listed = client.get("/agent/clients")
    assert listed.status_code == 200
    assert "C_LOAN" in {item["client_id"] for item in listed.json()["clients"]}

    response = client.post(
        "/agent/insights",
        json={"client_id": "C_LOAN", "period_from": "2026-09-01", "period_to": "2026-09-30", "top_n": 3, "role": "ceo"},
    )
    assert response.status_code == 200
    insights = response.json()
    assert insights
    assert "clu_sport" not in {item["cluster_id"] for item in insights}
    first = insights[0]
    stored = client.get(f"/agent/insights/{first['insight_id']}")
    assert stored.status_code == 200
    trace = client.get(f"/traces/{first['trace_id']}")
    assert trace.status_code == 200
    names = [step["name"] for step in trace.json()["steps"]]
    assert "plan" in names
    assert "verify" in names
    assert any(step["kind"] == "tool" for step in trace.json()["steps"])

    chat = client.post("/agent/chat", json={"insight_id": first["insight_id"], "question": "Откуда этот вывод?"})
    assert chat.status_code == 200
    assert chat.json()["answer"]
    assert chat.json()["trace_id"]

    feedback = client.post("/agent/feedback", json={"insight_id": first["insight_id"], "useful": True, "comment": "по делу"})
    assert feedback.status_code == 204


def test_personalization_over_http(tmp_path) -> None:
    client = _app(tmp_path)
    body = {"period_from": "2026-09-01", "period_to": "2026-09-30", "top_n": 5}

    def rate_insight(client_id: str) -> dict:
        response = client.post("/agent/insights", json={"client_id": client_id, **body})
        assert response.status_code == 200
        return next(item for item in response.json() if item["cluster_id"] == "clu_rate")

    loan = rate_insight("C_LOAN")
    deposit = rate_insight("C_DEPOSIT")
    loan_why = " ".join(item["text"] for item in loan["why_it_matters"])
    deposit_why = " ".join(item["text"] for item in deposit["why_it_matters"])
    assert loan_why != deposit_why
    assert "кредит" in loan_why
    assert "депозит" in deposit_why
    assert loan["priority_score"] > deposit["priority_score"]

    def fx_insight(client_id: str) -> dict:
        response = client.post("/agent/insights", json={"client_id": client_id, **body})
        return next(item for item in response.json() if item["cluster_id"] == "clu_fx")

    importer = fx_insight("C_IMPORT")
    domestic = fx_insight("C_DOMESTIC")
    assert importer["priority_score"] > domestic["priority_score"]
    assert "внешнеэкономическ" in " ".join(item["text"] for item in importer["why_it_matters"])


def test_unknown_client_and_empty_period(tmp_path) -> None:
    client = _app(tmp_path)
    missing = client.post("/agent/insights", json={"client_id": "NOBODY", "period_from": "2026-09-01", "period_to": "2026-09-30"})
    assert missing.status_code == 404
    empty = client.post("/agent/insights", json={"client_id": "C_LOAN", "period_from": "2020-01-01", "period_to": "2020-01-02"})
    assert empty.status_code == 200
    assert empty.json() == []
    assert client.get("/traces/missing").status_code == 404
    assert client.post("/agent/chat", json={"insight_id": "missing", "question": "почему?"}).status_code == 404
    bad_role = client.post(
        "/agent/insights",
        json={"client_id": "C_LOAN", "period_from": "2026-09-01", "period_to": "2026-09-30", "role": "intern"},
    )
    assert bad_role.status_code == 422
