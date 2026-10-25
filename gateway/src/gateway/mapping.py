"""Перевод инсайта агента в рабочее пространство фронтенда версии 3.0."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ROLES = {
    "financial_director": "cfo",
    "ceo": "ceo",
    "treasurer": "treasurer",
}
ROLE_LABELS = {
    "financial_director": "Финансовый директор",
    "ceo": "Генеральный директор",
    "treasurer": "Казначей",
}
TYPE_LABELS = {
    "risk": "Риск",
    "opportunity": "Возможность",
    "mixed": "Риск и возможность",
    "neutral": "Событие",
}


def agent_role(role: str | None) -> str:
    return ROLES.get(role or "", "ceo")


def workspace(clients: list[dict], insights: list[dict], company_id: str, role: str) -> dict:
    companies = [company_from_client(item) for item in clients]
    known = {item["id"] for item in companies}
    resolved = company_id if company_id in known else (companies[0]["id"] if companies else company_id)
    return {
        "schema_version": "3.0",
        "is_demo": False,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": "Разбор собран агентом по профилю клиента, ленте и материалам банка. Цифры только из переданных источников.",
        "companies": companies,
        "events": [event_from_insight(item, resolved, role) for item in insights],
    }


def question_answer(payload: dict) -> dict:
    claims = payload.get("answer") or []
    source_ids: list[str] = []
    lines: list[str] = []
    for claim in claims:
        text = str(claim.get("text") or "").strip()
        if text:
            lines.append(text)
        for source_id in claim.get("source_ids") or []:
            if source_id not in source_ids:
                source_ids.append(str(source_id))
    return {"answer": "\n".join(lines), "is_demo": False, "source_ids": source_ids}


def company_from_client(client: dict) -> dict:
    features: list[dict] = []

    def add(field: str, label: str, value: Any, unit: str | None = None) -> None:
        if value is None or value == "":
            return
        item: dict[str, Any] = {"field": field, "label": label, "value": value, "source": "Профиль клиента", "is_demo": False}
        if unit:
            item["unit"] = unit
        features.append(item)

    add("MAINOKVED_NAME", "Основная деятельность", client.get("okved_name"))
    add("MAINOKVED_CODE", "Код ОКВЭД", client.get("okved_code"))
    if client.get("revenue") is not None:
        add("REVENUE_COMPANY", "Годовая выручка", client["revenue"], "RUB")
    add("REGION", "Регион", client.get("region"))
    add("CITY", "Город", client.get("city"))
    add("AMOUNT_WORKERS_FNS_GRADATION", "Численность", client.get("workers"))
    add("FTS_COUNTRY_NAME", "Страны ВЭД", client.get("fts_country"))
    add("CL_TYPE", "Тип клиента", client.get("client_type"))
    for field, label, raw in (
        ("CL_PRODUCT_LOA_MARK", "Кредитные продукты", client.get("loan")),
        ("CL_PRODUCT_DEP_MARK", "Депозиты", client.get("deposit")),
        ("CL_PRODUCT_LEA_MARK", "Лизинг", client.get("leasing")),
        ("CL_PRODUCT_GAR_MARK", "Гарантии", client.get("guarantee")),
        ("FACTORING_MARK", "Факторинг", client.get("factoring")),
        ("FTS_FLAG", "Внешнеэкономическая деятельность", client.get("fts_flag")),
    ):
        flag = _flag(raw)
        if flag is not None:
            add(field, label, flag)
    name = client.get("okved_name") or "Компания"
    client_id = str(client.get("client_id") or "")
    return {
        "id": client_id,
        "display_name": f"{name} · {client_id}",
        "short_name": client_id,
        "industry": client.get("okved_name") or "Отрасль не указана",
        "description": ", ".join(part for part in (client.get("okved_name"), client.get("region"), client.get("city")) if part),
        "features": features,
        "missing_data": [] if features else ["В профиле нет заполненных признаков"],
    }


def event_from_insight(insight: dict, company_id: str, role: str) -> dict:
    sources = _sources(insight)
    source_ids = {item["id"] for item in sources}
    facts = []
    for index, fact in enumerate((insight.get("what_happened") or {}).get("facts") or []):
        cited = next((item for item in fact.get("source_ids") or [] if item in source_ids), None)
        facts.append(
            {
                "id": f"fact-{index}",
                "text": fact.get("text") or "",
                "source_id": cited or "",
                "evidence_quote": fact.get("text") or "",
                "verification_status": "confirmed",
            }
        )
    feature_refs = [item.get("csv_field") for item in insight.get("client_features_used") or [] if item.get("csv_field")]
    bank_ids = [item["id"] for item in sources if item["type"] == "bank_analysis"]
    basis = {"fact_ids": [item["id"] for item in facts[:3]], "company_feature_refs": feature_refs[:4], "bank_source_ids": bank_ids}
    why = insight.get("why_it_matters") or []
    summary = why[0]["text"] if why else insight.get("headline") or ""
    score = float(insight.get("priority_score") or 0)
    kind = insight.get("type") if insight.get("type") in TYPE_LABELS else "neutral"
    impact = insight.get("impact") if insight.get("impact") in {"high", "medium", "low"} else "low"
    when = insight.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fragments = insight.get("bank_view") or []
    bank_summary = "\n".join(item.get("text") or "" for item in fragments).strip()
    assumptions = [item.get("text") for item in (insight.get("what_happened") or {}).get("assumptions") or [] if item.get("text")]
    return {
        "id": insight.get("insight_id") or insight.get("cluster_id") or "insight",
        "company_ids": [company_id],
        "title": insight.get("headline") or "Событие",
        "category": kind,
        "category_label": TYPE_LABELS[kind],
        "date": when,
        "priority": impact,
        "priority_reason": f"Влияние {impact}, приоритет {score:.3f}",
        "read_time": 3,
        "summary": summary,
        "impact_type": kind if kind in {"risk", "opportunity", "mixed"} else "mixed",
        "relevance": {
            company_id: {
                "score": score,
                "reason": summary,
                "feature_refs": feature_refs,
                "summary": summary,
                "role_scores": {"financial_director": score, "ceo": score, "treasurer": score},
                "role_summary": {"financial_director": summary, "ceo": summary, "treasurer": summary},
            }
        },
        "facts": facts,
        "bank_view": {
            "status": "available" if bank_summary and bank_ids else "not_loaded",
            "summary": bank_summary or None,
            "source_ids": bank_ids,
            "message": "" if bank_summary else "Релевантный фрагмент аналитики банка не найден.",
        },
        "ai_interpretation": {
            "is_demo": False,
            "status": "generated",
            "summary": summary,
            "basis": basis,
            "assumptions": assumptions,
            "risks": _impacts(insight, "risks", basis),
            "opportunities": _impacts(insight, "opportunities", basis),
            "reasoning_steps": [item.get("text") for item in why if item.get("text")],
        },
        "actions": [
            {
                "id": f"act-{index}",
                "title": (item.get("text") or "Следующий шаг")[:180],
                "owner_role": ROLE_LABELS.get(role, "Руководитель"),
                "reason": item.get("rationale") or "",
                "check_before_action": insight.get("gaps") or ["Сверить формулировку с текстом источника"],
                "related_risk_ids": [],
                "basis": basis,
            }
            for index, item in enumerate(insight.get("actions") or [])
        ],
        "missing_data": list(insight.get("gaps") or []),
        "sources": sources,
        "suggested_questions": [
            {"question": "Почему это важно именно этой компании?", "answer": summary},
            {"question": "На каких источниках основан вывод?", "answer": summary},
        ],
        "verification": {
            "status": "needs_more_data" if insight.get("gaps") or insight.get("confidence") == "low" else "checked",
            "conflicts": [],
        },
        "analysis_status": "partial" if insight.get("gaps") or not bank_summary else "ready",
    }


def _sources(insight: dict) -> list[dict]:
    sources = []
    seen: set[str] = set()
    for item in insight.get("sources") or []:
        source_id = str(item.get("source_id") or "")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        bank = item.get("kind") == "bank_analytics"
        sources.append(_source(source_id, item.get("title") or source_id, item.get("published_at"), item.get("url"), bank, _text_for(insight, source_id)))
    for fragment in insight.get("bank_view") or []:
        source_id = f"bank:{fragment.get('doc_id')}:{fragment.get('fragment_id')}"
        if source_id in seen:
            continue
        seen.add(source_id)
        sources.append(_source(source_id, fragment.get("doc_title") or "Аналитика Альфа-Банка", None, None, True, fragment.get("text") or ""))
    return sources


def _source(source_id: str, title: str, published: str | None, url: str | None, bank: bool, text: str) -> dict:
    return {
        "id": source_id,
        "type": "bank_analysis" if bank else "news",
        "is_demo": False,
        "title": title,
        "publisher": "Альфа-Банк" if bank else "Новостной источник",
        "date": (published or "")[:10],
        "url": url or ("https://alfabank.ru/" if bank else None),
        "text": text,
    }


def _text_for(insight: dict, source_id: str) -> str:
    chunks = []
    for fact in (insight.get("what_happened") or {}).get("facts") or []:
        if source_id in (fact.get("source_ids") or []):
            chunks.append(fact.get("text") or "")
    for fragment in insight.get("bank_view") or []:
        if source_id == f"bank:{fragment.get('doc_id')}:{fragment.get('fragment_id')}":
            chunks.append(fragment.get("text") or "")
    return "\n".join(chunk for chunk in chunks if chunk) or str(insight.get("headline") or "")


def _impacts(insight: dict, key: str, basis: dict) -> list[dict]:
    claims = (insight.get("consequences") or {}).get(key) or []
    return [
        {
            "id": f"{key}-{index}",
            "text": item.get("text") or "",
            "condition": "Если сведения источников относятся к этой компании.",
            "basis": basis,
        }
        for index, item in enumerate(claims)
        if item.get("text")
    ]


def _flag(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"1", "1.0", "true", "yes", "да"}:
        return True
    if text in {"0", "0.0", "false", "no", "нет"}:
        return False
    return None
