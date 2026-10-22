"""Детерминированная проверка инсайта. Критик на LLM подключается в цикле."""

from __future__ import annotations

import re
from typing import Any

from agent.schemas.models import schema_errors

_NUMBER = re.compile(r"\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?%?")
STRUCTURAL = {"schema", "source_id", "number", "feature", "bank_view"}


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def _norm_number(token: str) -> str:
    cleaned = token.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    percent = cleaned.endswith("%")
    body = cleaned[:-1] if percent else cleaned
    if "." in body:
        body = body.rstrip("0").rstrip(".")
    return body + ("%" if percent else "")


def numbers_of(text: str) -> set[str]:
    return {_norm_number(match.group(0)) for match in _NUMBER.finditer(text or "")}


def _claim_texts(draft: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
    found: list[tuple[str, str, list[str]]] = []
    found.append(("headline", str(draft.get("headline") or ""), []))
    happened = draft.get("what_happened") or {}
    for index, fact in enumerate(happened.get("facts") or []):
        found.append((f"facts[{index}]", str(fact.get("text") or ""), list(fact.get("source_ids") or [])))
    for index, item in enumerate(happened.get("assumptions") or []):
        found.append((f"assumptions[{index}]", str(item.get("text") or ""), list(item.get("source_ids") or [])))
    for index, item in enumerate(draft.get("why_it_matters") or []):
        found.append((f"why[{index}]", str(item.get("text") or ""), list(item.get("source_ids") or [])))
    consequences = draft.get("consequences") or {}
    for bucket in ("risks", "opportunities"):
        for index, item in enumerate(consequences.get(bucket) or []):
            found.append((f"{bucket}[{index}]", str(item.get("text") or ""), list(item.get("source_ids") or [])))
    for index, item in enumerate(consequences.get("factors_to_watch") or []):
        found.append((f"factors[{index}]", str(item), []))
    for index, item in enumerate(draft.get("actions") or []):
        found.append((f"actions[{index}]", str(item.get("text") or "") + " " + str(item.get("rationale") or ""), []))
    for index, item in enumerate(draft.get("bank_view") or []):
        found.append((f"bank_view[{index}]", str(item.get("text") or ""), []))
    for index, item in enumerate(draft.get("gaps") or []):
        found.append((f"gaps[{index}]", str(item), []))
    return found


def check_insight(
    draft: dict[str, Any],
    *,
    allowed_sources: dict[str, str],
    feature_values: dict[str, str],
    bank_fragments: list[dict[str, Any]],
) -> list[dict[str, str]]:
    errors = schema_errors(draft, "insight.schema.json")
    if errors:
        return [{"code": "schema", "detail": errors[0]}]

    issues: list[dict[str, str]] = []
    declared = {item.get("source_id") for item in draft.get("sources") or []}
    corpus = set()
    for text in allowed_sources.values():
        corpus |= numbers_of(text)
    corpus |= numbers_of("\n".join(feature_values.values()))

    for path, text, source_ids in _claim_texts(draft):
        for source_id in source_ids:
            if source_id not in declared or source_id not in allowed_sources:
                issues.append({"code": "source_id", "detail": f"{path}: источник {source_id} не использовался"})
        for number in numbers_of(text) - corpus:
            issues.append({"code": "number", "detail": f"{path}: число {number} нет в источниках"})

    fragments = {item.get("fragment_id"): item for item in bank_fragments}
    for item in draft.get("bank_view") or []:
        fragment = fragments.get(item.get("fragment_id"))
        if fragment is None or fragment.get("doc_id") != item.get("doc_id"):
            issues.append({"code": "bank_view", "detail": f"фрагмент {item.get('fragment_id')} не возвращался поиском"})
            continue
        if _squash(item.get("text") or "") not in _squash(fragment.get("text") or ""):
            issues.append({"code": "bank_view", "detail": "текст bank_view не дословный фрагмент банка"})

    if draft.get("confidence") == "high" and not draft.get("bank_view"):
        issues.append({"code": "confidence", "detail": "без фрагмента банка confidence не может быть high"})

    for item in draft.get("why_it_matters") or []:
        for feature_id in item.get("client_features") or []:
            if feature_id not in feature_values:
                issues.append({"code": "feature", "detail": f"признак {feature_id} отсутствует в профиле"})
    for item in draft.get("client_features_used") or []:
        feature_id = item.get("feature_id")
        if feature_id not in feature_values:
            issues.append({"code": "feature", "detail": f"признак {feature_id} отсутствует в профиле"})
        elif feature_values[feature_id] != item.get("value"):
            issues.append({"code": "feature", "detail": f"значение признака {feature_id} не совпадает с профилем"})
    return issues


def check_chat(
    draft: dict[str, Any],
    *,
    allowed_sources: dict[str, str],
    bank_fragments: list[dict[str, Any]],
) -> list[dict[str, str]]:
    errors = schema_errors(draft, "chat_answer.schema.json")
    if errors:
        return [{"code": "schema", "detail": errors[0]}]
    issues: list[dict[str, str]] = []
    declared = {item.get("source_id") for item in draft.get("sources") or []}
    corpus: set[str] = set()
    for text in allowed_sources.values():
        corpus |= numbers_of(text)
    fragment_text = _squash("\n".join(item.get("text") or "" for item in bank_fragments))
    for index, claim in enumerate(draft.get("answer") or []):
        for source_id in claim.get("source_ids") or []:
            if source_id not in declared or source_id not in allowed_sources:
                issues.append({"code": "source_id", "detail": f"answer[{index}]: источник {source_id} не использовался"})
        for number in numbers_of(claim.get("text") or "") - corpus:
            issues.append({"code": "number", "detail": f"answer[{index}]: число {number} нет в источниках"})
        if claim.get("origin") == "bank" and _squash(claim.get("text") or "") not in fragment_text:
            issues.append({"code": "bank_view", "detail": "ответ с origin=bank не совпадает с фрагментом"})
    return issues


def next_action(issues: list[dict[str, str]], critic_decision: str | None, iteration: int, max_iters: int) -> str:
    codes = {item["code"] for item in issues}
    structural = codes & STRUCTURAL
    if structural:
        if iteration >= max_iters:
            return "give_up_low_confidence"
        if structural <= {"bank_view"}:
            return "retry_search"
        return "rewrite"
    if "confidence" in codes:
        return "rewrite" if iteration < max_iters else "give_up_low_confidence"
    if critic_decision in {None, "ok"}:
        return "ok"
    if iteration >= max_iters:
        return "give_up_low_confidence"
    if critic_decision in {"retry_search", "rewrite", "give_up_low_confidence"}:
        return critic_decision
    return "rewrite"
