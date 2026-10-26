"""Инструменты профиля, графа, новостей, банка, макро и скоринга."""

from __future__ import annotations

from agent.bank_index import BankIndex, match_indicator
from agent.context import ToolContext
from agent.news_store import cluster_payload
from agent.profile_features import build_profile
from agent.ranking import score_relevance
from agent.registry import ToolRegistry, ToolSpec

_OBJECT = {"type": "object"}


def _client_schema(extra: dict | None = None, required: list[str] | None = None) -> dict:
    properties = {"client_id": {"type": "string", "minLength": 1}}
    if extra:
        properties.update(extra)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": ["client_id", *(required or [])],
    }


def register_all(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="get_client_profile",
            description="Профиль клиента и производные признаки с полем-источником из CSV. client_id берётся из сессии.",
            input_schema=_client_schema(),
            output_schema=_OBJECT,
            permission="read",
            handler=get_client_profile,
            binds_client=True,
        )
    )
    registry.register(
        ToolSpec(
            name="get_client_graph",
            description="Контрагенты по платежам, компании группы и связи через общих физлиц. Глубина 1 или 2.",
            input_schema=_client_schema({"depth": {"type": "integer", "minimum": 1, "maximum": 2}}, required=[]),
            output_schema=_OBJECT,
            permission="read",
            handler=get_client_graph,
            binds_client=True,
        )
    )
    registry.register(
        ToolSpec(
            name="search_news",
            description="Поиск кластеров новостей по запросу, периоду, тегам, региону и сектору.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "filters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "period_from": {"type": "string"},
                            "period_to": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "region": {"type": "string"},
                            "sector": {"type": "string"},
                        },
                    },
                },
            },
            output_schema=_OBJECT,
            permission="read",
            handler=search_news,
        )
    )
    registry.register(
        ToolSpec(
            name="get_news_cluster",
            description="Полный кластер новостей с текстами статей и идентификаторами источников.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["cluster_id"],
                "properties": {"cluster_id": {"type": "string", "minLength": 1}},
            },
            output_schema=_OBJECT,
            permission="read",
            handler=get_news_cluster,
        )
    )
    registry.register(
        ToolSpec(
            name="search_bank_analytics",
            description="Фрагменты аналитики Альфа-Банка с документом, разделом и идентификатором фрагмента.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 8},
                },
            },
            output_schema=_OBJECT,
            permission="read",
            handler=search_bank_analytics,
        )
    )
    registry.register(
        ToolSpec(
            name="get_macro_indicator",
            description="Макропоказатель только цитатой из материалов банка или новостей. Если цитаты нет, показатель не возвращается.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {"name": {"type": "string", "minLength": 1}},
            },
            output_schema=_OBJECT,
            permission="read",
            handler=get_macro_indicator,
        )
    )
    registry.register(
        ToolSpec(
            name="score_relevance",
            description="Детерминированная релевантность кластера клиенту без LLM, с расшифровкой по признакам.",
            input_schema=_client_schema({"cluster_id": {"type": "string", "minLength": 1}}, required=["cluster_id"]),
            output_schema=_OBJECT,
            permission="read",
            handler=score_relevance_tool,
            binds_client=True,
        )
    )


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_all(registry)
    return registry


def get_client_profile(args: dict, ctx: ToolContext) -> dict:
    profile = build_profile(ctx.directory, args["client_id"])
    features = []
    for feature in profile.for_role(ctx.session.role):
        payload = feature.public()
        payload["active"] = feature.active
        features.append(payload)
    return {
        "client_id": profile.client_id,
        "role": ctx.session.role,
        "features": features,
        "source_id": f"profile:{profile.client_id}",
        "source_text": profile.source_text(ctx.session.role),
    }


def get_client_graph(args: dict, ctx: ToolContext) -> dict:
    depth = int(args.get("depth") or 1)
    view = ctx.directory.graph(args["client_id"], depth=depth)
    return {
        "client_id": view.client_id,
        "depth": depth,
        "payments": [_party(item) for item in view.payments],
        "group_members": [_party(item) for item in view.group_members],
        "person_links": [_party(item) for item in view.person_links],
        "top_share": view.top_share,
    }


def search_news(args: dict, ctx: ToolContext) -> dict:
    filters = args.get("filters") or {}
    hits = ctx.news.search(
        args.get("query") or "",
        period_from=filters.get("period_from"),
        period_to=filters.get("period_to"),
        tags=filters.get("tags"),
        region=filters.get("region"),
        sector=filters.get("sector"),
        limit=int(args.get("limit") or 10),
    )
    return {"hits": hits}


def get_news_cluster(args: dict, ctx: ToolContext) -> dict:
    cluster = ctx.news.get(args["cluster_id"])
    if cluster is None:
        return {"error": "not_found", "cluster_id": args["cluster_id"]}
    return cluster_payload(cluster)


def search_bank_analytics(args: dict, ctx: ToolContext) -> dict:
    top_k = int(args.get("top_k") or 3)
    return {"hits": ctx.bank.search(args["query"], top_k=top_k)}


def get_macro_indicator(args: dict, ctx: ToolContext) -> dict:
    result = ctx.bank.macro(str(args["name"]))
    if result.get("found"):
        return result
    documents = [
        {
            "text": cluster.searchable_text(),
            "source_id": f"news:{cluster.cluster_id}",
            "title": cluster.title,
            "published_at": cluster.published_at,
            "kind": "news",
            "doc_id": cluster.cluster_id,
            "fragment_id": None,
        }
        for cluster in ctx.news.clusters
    ]
    from_news = match_indicator(str(args["name"]), documents)
    if from_news.get("found"):
        return from_news
    return result


def score_relevance_tool(args: dict, ctx: ToolContext) -> dict:
    cluster = ctx.news.get(args["cluster_id"])
    if cluster is None:
        return {"error": "not_found", "cluster_id": args["cluster_id"]}
    profile = build_profile(ctx.directory, args["client_id"])
    return score_relevance(profile, cluster, ctx.rules, ctx.session.role).as_dict()


def _party(item) -> dict:
    return {
        "counterparty_id": item.counterparty_id,
        "relation": item.relation,
        "direction": item.direction,
        "amount": item.amount,
        "count": item.count,
        "share": None if item.share is None else round(item.share, 4),
        "okved_code": item.okved_code,
        "okved_name": item.okved_name,
        "sector_id": item.sector_id,
        "sector_label": item.sector_label,
        "region": item.region,
        "city": item.city,
    }


def empty_bank() -> BankIndex:
    return BankIndex([])
