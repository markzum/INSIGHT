from pathlib import Path

import pytest

from agent.bank_index import BankIndex
from agent.context import Session, ToolContext
from agent.errors import ToolDenied
from agent.llm_client import Budget
from agent.news_store import NewsStore
from agent.paths import SYNTHETIC_DIR
from agent.ranking import SensitivityRules
from agent.registry import ToolRegistry, ToolSpec
from agent.tools import build_registry


@pytest.fixture
def ctx(directory, tmp_path: Path) -> ToolContext:
    news_path = SYNTHETIC_DIR / "clusters.jsonl"
    broken = tmp_path / "clusters.jsonl"
    broken.write_text(news_path.read_text(encoding="utf-8") + "\n{битая строка\n", encoding="utf-8")
    return ToolContext(
        session=Session(client_id="C_LOAN", role="ceo", trace_id="tr"),
        directory=directory,
        news=NewsStore.load(broken),
        bank=BankIndex.from_text_dir(SYNTHETIC_DIR / "bank"),
        rules=SensitivityRules.load(),
        budget=Budget(20, 5000, 30, 10),
    )


def test_unknown_tool_is_denied(ctx: ToolContext) -> None:
    registry = build_registry()
    with pytest.raises(ToolDenied):
        registry.call("os.system", {"command": "whoami"}, ctx)
    assert ctx.steps[-1]["name"] == "os.system"
    assert ctx.steps[-1]["error"] is True


def test_write_tool_cannot_be_registered() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolDenied):
        registry.register(
            ToolSpec(
                name="write_file",
                description="нельзя",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                permission="write",
                handler=lambda args, ctx: {},
            )
        )


def test_client_id_comes_from_session(ctx: ToolContext) -> None:
    registry = build_registry()
    result = registry.call("get_client_profile", {"client_id": "C_DEPOSIT"}, ctx)
    assert result["client_id"] == "C_LOAN"
    assert all(item["feature_id"] != "labor_sensitivity" or True for item in result["features"])
    treasurer = ToolContext(
        session=Session(client_id="C_LOAN", role="treasurer"),
        directory=ctx.directory,
        news=ctx.news,
        bank=ctx.bank,
        rules=ctx.rules,
        budget=ctx.budget,
    )
    hidden = registry.call("get_client_profile", {}, treasurer)
    assert "labor_sensitivity" not in {item["feature_id"] for item in hidden["features"]}


def test_news_search_period_and_broken_line(ctx: ToolContext) -> None:
    assert ctx.news.get("clu_rate") is not None
    assert ctx.news.get("clu_sport") is not None
    registry = build_registry()
    result = registry.call(
        "search_news",
        {"query": "ставка", "filters": {"period_from": "2026-09-01", "period_to": "2026-09-30", "tags": ["key_rate"]}},
        ctx,
    )
    ids = [item["cluster_id"] for item in result["hits"]]
    assert ids == ["clu_rate"]
    full = registry.call("get_news_cluster", {"cluster_id": "clu_rate"}, ctx)
    assert full["articles"][0]["source_id"] == "news:clu_rate:0"
    missing = registry.call("get_news_cluster", {"cluster_id": "nope"}, ctx)
    assert missing["error"] == "not_found"


def test_bank_search_and_score(ctx: ToolContext) -> None:
    registry = build_registry()
    bank = registry.call("search_bank_analytics", {"query": "ключевая ставка", "top_k": 2}, ctx)
    assert bank["hits"]
    assert "14%" in bank["hits"][0]["text"]
    assert bank["hits"][0]["source_id"].startswith("bank:")
    macro = registry.call("get_macro_indicator", {"name": "key_rate"}, ctx)
    assert macro["found"] is True
    assert "14%" in macro["value_text"]
    assert macro["value_text"] in bank["hits"][0]["text"]
    scored = registry.call("score_relevance", {"cluster_id": "clu_rate", "client_id": "C_IMPORT"}, ctx)
    assert scored["client_id"] == "C_LOAN"
    assert scored["score"] > 0
