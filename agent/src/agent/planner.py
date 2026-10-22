"""План шагов. Модель выбирает инструменты, реестр потом отсекает всё лишнее."""

from __future__ import annotations

import json
from typing import Any

from agent.llm_client import default_plan
from agent.news_model import NewsCluster
from agent.prompts import load_prompt, wrap_untrusted
from agent.registry import ToolRegistry

_INDICATOR_BY_TAG = {
    "key_rate": "key_rate",
    "monetary_policy": "key_rate",
    "inflation": "inflation",
    "fx": "usd_rub",
    "labor": "unemployment",
    "commodities": "commodities",
}


def guess_indicator(cluster: NewsCluster) -> str | None:
    for tag in cluster.tags:
        name = _INDICATOR_BY_TAG.get(tag.lower())
        if name:
            return name
    return None


def plan_messages(cluster: NewsCluster, features: list[dict], registry: ToolRegistry) -> list[dict[str, str]]:
    visible = [
        {"feature_id": item["feature_id"], "label": item["label"], "value": item["value"], "active": item.get("active")}
        for item in features
    ]
    payload = {
        "tools": registry.catalog(),
        "client_features": visible,
        "cluster": wrap_untrusted(
            "news",
            json.dumps(
                {"cluster_id": cluster.cluster_id, "title": cluster.title, "summary": cluster.summary, "tags": cluster.tags},
                ensure_ascii=False,
            ),
        ),
        "suggested_indicator": guess_indicator(cluster),
    }
    return [
        {"role": "system", "content": load_prompt("plan")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def coerce_plan(payload: dict[str, Any] | None, registry: ToolRegistry, fallback_context: dict[str, Any]) -> tuple[dict, bool]:
    steps = []
    for step in (payload or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "")
        if tool not in registry.names():
            continue
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        args = {key: value for key, value in args.items() if key != "client_id"}
        steps.append({"tool": tool, "args": args, "why": str(step.get("why") or "")})
    if not steps:
        return default_plan(fallback_context), True
    return {"steps": steps[:8]}, False
