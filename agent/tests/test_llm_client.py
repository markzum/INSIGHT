import json

from agent.llm_client import Budget, LLMClient
from agent.profile_features import build_profile
from agent.settings import Settings


def _settings(tmp_path, **kwargs) -> Settings:
    base = dict(
        mock_llm=True,
        db_path=tmp_path / "agent.sqlite",
        cache_dir=tmp_path / "llm",
        llm_api_key="secret-key",
    )
    base.update(kwargs)
    return Settings(**base)


def test_mock_analyze_differs_by_client(tmp_path, directory) -> None:
    client = LLMClient(_settings(tmp_path))
    budget = Budget(12, 1000, 30, 5)

    def context(client_id: str) -> dict:
        profile = build_profile(directory, client_id)
        features = [item.public() | {"active": item.active} for item in profile.features]
        return {"client_id": client_id, "cluster_id": "clu_rate", "title": "Ставка", "summary": "Ставка 14%.", "features": features}

    loan = json.loads(client.complete(kind="analyze", role="reasoning", messages=[], budget=budget, mock_context=context("C_LOAN")).text)
    deposit = json.loads(client.complete(kind="analyze", role="reasoning", messages=[], budget=budget, mock_context=context("C_DEPOSIT")).text)
    loan_text = " ".join(item["text"] for item in loan["why_it_matters"])
    deposit_text = " ".join(item["text"] for item in deposit["why_it_matters"])
    assert loan_text != deposit_text
    assert "кредит" in loan_text
    assert "депозит" in deposit_text


def test_budget_blocks_extra_call(tmp_path) -> None:
    client = LLMClient(_settings(tmp_path))
    budget = Budget(max_steps=5, max_tokens=1000, max_seconds=30, max_llm_calls=1)
    client.complete(kind="plan", role="light", messages=[], budget=budget, mock_context={"title": "A", "cluster_id": "c"})
    try:
        client.complete(kind="plan", role="light", messages=[], budget=budget, mock_context={"title": "A", "cluster_id": "c"})
        raised = False
    except Exception as exc:  # noqa: BLE001 - проверяем тип ниже
        raised = True
        assert exc.__class__.__name__ == "BudgetExceeded"
    assert raised


def test_disk_and_sqlite_cache(tmp_path) -> None:
    calls = {"n": 0}

    def transport(payload, headers):
        calls["n"] += 1
        assert "secret-key" in headers["Authorization"]
        return {
            "choices": [{"message": {"content": "{\"ok\": true}"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    settings = _settings(tmp_path, mock_llm=False, llm_base_url="http://llm.test/v1")
    client = LLMClient(settings, transport=transport)
    budget = Budget(5, 1000, 30, 5)
    messages = [{"role": "user", "content": "ping"}]
    first = client.complete(kind="plan", role="reasoning", messages=messages, budget=budget)
    second = client.complete(kind="plan", role="reasoning", messages=messages, budget=budget)
    assert first.cached is False
    assert second.cached is True
    assert calls["n"] == 1
    assert budget.llm_calls == 1
    assert list(settings.cache_dir.glob("*.json"))
    import sqlite3

    conn = sqlite3.connect(settings.db_path)
    count = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    assert count == 1


def test_invalid_json_retries_once(tmp_path) -> None:
    calls = {"n": 0}

    def transport(payload, headers):
        calls["n"] += 1
        content = "не json" if calls["n"] == 1 else "{\"steps\": []}"
        return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    client = LLMClient(_settings(tmp_path, mock_llm=False), transport=transport)
    parsed, _result, error = client.complete_json(
        kind="plan",
        role="reasoning",
        messages=[{"role": "user", "content": "plan"}],
        budget=Budget(5, 1000, 30, 5),
    )
    assert parsed == {"steps": []}
    assert error is None
    assert calls["n"] == 2
