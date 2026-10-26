"""OpenAI-совместимый клиент: mock, дисковый и sqlite-кэш, лимиты."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from agent.errors import BudgetExceeded
from agent.settings import Settings

log = logging.getLogger("agent.llm")

_WHY = {
    "rate_sensitivity": "У компании подключены кредитные или лизинговые продукты, поэтому цена заёмных денег для неё не снижается, пока ставка остаётся высокой.",
    "deposit_benefit": "У компании есть депозиты, поэтому текущий уровень ставки поддерживает доход от размещения свободных денег.",
    "fx_sensitivity": "Компания ведёт внешнеэкономическую деятельность, поэтому движение курса меняет стоимость её контрактов и расчётов.",
    "labor_sensitivity": "Зарплатный проект и численность связывают компанию с рынком труда, поэтому сдвиг спроса на персонал меняет фонд оплаты.",
    "commodity_exposure": "Себестоимость клиента зависит от сырья, поэтому ценовой шок проходит в маржу.",
    "demand_sensitivity": "Выручка клиента завязана на спрос в его канале сбыта, поэтому охлаждение спроса бьёт по обороту.",
    "sector": "Событие попадает в отрасль клиента и читается через его вид деятельности, а не как общая новость.",
    "region": "Компания привязана к своему региону, поэтому местный эффект события для неё важнее общей сводки.",
    "counterparty_concentration": "Платежи сосредоточены на небольшом круге контрагентов, поэтому сбой у них быстро доходит до клиента.",
    "scale_revenue": "Масштаб выручки делает даже умеренное движение рынка заметным в денежном выражении.",
    "market_position": "Место компании среди клиентов банка в той же отрасли меняет масштаб последствия: лидер выборки и компания с меньшим местом читают одно событие по-разному.",
    "sample_share": "Доля выручки внутри выборки клиентов отрасли показывает вес компании среди известных клиентов банка. Это не доля всего рынка: его объём в данных не передан.",
    "group": "Событие стоит читать через группу компаний, а не только через одно юридическое лицо: у связанных компаний другие отрасли и своя выручка.",
    "related_via_persons": "Общее физлицо связывает компанию с другой организацией, поэтому событие в отрасли связанной компании может дойти до клиента по контуру управления.",
}

_ACTIONS = {
    "rate_sensitivity": (
        "Сверить долю кредитов и лизинга с плавающей ставкой и ближайшую дату пересмотра.",
        "От этого зависит, сохранится ли текущая долговая нагрузка.",
        "loan",
    ),
    "deposit_benefit": (
        "Сравнить действующие депозиты с условиями из обзора банка и решить, какие короткие размещения продлевать.",
        "При высокой ставке короткий депозит и длинный депозит дают разный результат.",
        "deposit",
    ),
    "fx_sensitivity": (
        "Разложить валютные поступления и платежи по валютам и срокам и отметить, где курс уже меняет маржу.",
        "Без этой раскладки курсовая новость не превращается в действие.",
        "fx_hedge",
    ),
    "labor_sensitivity": (
        "Спросить у кадров, какие вакансии и индекс оплаты уже сдвигаются, и сверить это с фондом оплаты.",
        "Новость о рынке труда полезна только вместе с внутренним планом найма.",
        "payroll",
    ),
    "market_position": (
        "Сверить, держится ли место компании в отрасли на собственных продажах или на компаниях группы.",
        "Без этой сверки новость об отрасли не показывает, кого она задевает сильнее.",
        None,
    ),
    "group": (
        "Проверить, у какой компании группы событие меняет выручку, закупки или долг, и не переносить вывод автоматически на всю группу.",
        "У компаний одной группы разные отрасли и продукты, поэтому эффект не общий.",
        None,
    ),
    "related_via_persons": (
        "Уточнить, какая связь через физлицо реально влияет на договоры и платежи, а какая только числится в профиле.",
        "Идентификатор физлица сам по себе не доказывает операционную зависимость.",
        None,
    ),
    "default": (
        "Уточнить у профильной функции, какой показатель компании это событие двигает в ближайшем квартале.",
        "Без внутреннего показателя вывод останется общим.",
        None,
    ),
}

_FEATURE_ORDER = [
    "rate_sensitivity",
    "fx_sensitivity",
    "deposit_benefit",
    "labor_sensitivity",
    "commodity_exposure",
    "demand_sensitivity",
    "counterparty_concentration",
    "sector",
    "group",
    "related_via_persons",
    "market_position",
    "sample_share",
    "region",
    "scale_revenue",
]
# Добор, если у события меньше двух совпавших признаков. Сюда не входят
# кредиты и валюта: иначе новость про курс получает вывод про ставку.
_PAD_ORDER = [
    "market_position",
    "sample_share",
    "scale_revenue",
    "group",
    "related_via_persons",
    "sector",
    "region",
    "counterparty_concentration",
]


@dataclass
class Budget:
    max_steps: int
    max_tokens: int
    max_seconds: int
    max_llm_calls: int
    steps: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    started: float = field(default_factory=time.monotonic)

    def remaining_seconds(self) -> float:
        return self.max_seconds - (time.monotonic() - self.started)

    def ensure_time(self) -> None:
        if self.remaining_seconds() <= 0:
            raise BudgetExceeded("исчерпан лимит времени запроса")

    def charge_step(self) -> None:
        self.ensure_time()
        if self.steps >= self.max_steps:
            raise BudgetExceeded("исчерпан лимит шагов")
        self.steps += 1

    def ensure_llm(self) -> None:
        self.ensure_time()
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded("исчерпан лимит вызовов LLM")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded("исчерпан лимит токенов")

    def add_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.llm_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.tokens += prompt_tokens + completion_tokens


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached: bool
    kind: str


class ResponseCache:
    """Тело ответа лежит в файле по хэшу, sqlite хранит тот же ключ."""

    def __init__(self, directory: Path, db_path: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache (cache_key TEXT PRIMARY KEY, path TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, payload: dict[str, Any]) -> None:
        path = self.directory / f"{key}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, path, created_at) VALUES (?, ?, ?)",
                (key, str(path), datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()


Transport = Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]


class LLMClient:
    def __init__(self, settings: Settings, transport: Transport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._cache = ResponseCache(settings.cache_dir, settings.db_path)
        self._http = httpx.Client(timeout=httpx.Timeout(150.0, connect=10.0))
        if settings.llm_api_key:
            logging.getLogger("agent").addFilter(_SecretFilter(settings.llm_api_key))

    def close(self) -> None:
        self._http.close()

    def complete(
        self,
        *,
        kind: str,
        role: str,
        messages: list[dict[str, str]],
        budget: Budget,
        temperature: float | None = None,
        mock_context: dict[str, Any] | None = None,
    ) -> LLMResult:
        model = self.settings.model_for(role)
        temperature = self.settings.temperature if temperature is None else temperature
        if self.settings.mock_llm:
            budget.ensure_llm()
            text = self._mock(kind, mock_context or {})
            prompt_tokens, completion_tokens = 8, max(8, len(text) // 4)
            budget.add_usage(prompt_tokens, completion_tokens)
            log.info("llm mock kind=%s model=%s tokens=%s", kind, model, prompt_tokens + completion_tokens)
            return LLMResult(text, model, prompt_tokens, completion_tokens, False, kind)

        key = _cache_key(model, temperature, messages)
        cached = self._cache.get(key)
        if cached is not None:
            log.info("llm cache hit kind=%s model=%s", kind, model)
            return LLMResult(
                cached["text"],
                model,
                int(cached.get("prompt_tokens") or 0),
                int(cached.get("completion_tokens") or 0),
                True,
                kind,
            )

        budget.ensure_llm()
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        response = self._post(payload)
        text, prompt_tokens, completion_tokens = _read_completion(response, messages)
        budget.add_usage(prompt_tokens, completion_tokens)
        self._cache.put(
            key,
            {"text": text, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        )
        log.info("llm call kind=%s model=%s tokens=%s", kind, model, prompt_tokens + completion_tokens)
        return LLMResult(text, model, prompt_tokens, completion_tokens, False, kind)

    def complete_json(
        self,
        *,
        kind: str,
        role: str,
        messages: list[dict[str, str]],
        budget: Budget,
        mock_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, LLMResult, str | None]:
        result = self.complete(kind=kind, role=role, messages=messages, budget=budget, mock_context=mock_context)
        parsed, error = _parse_json(result.text)
        if parsed is not None or self.settings.mock_llm:
            return parsed, result, error
        retry_messages = messages + [
            {
                "role": "user",
                "content": "Предыдущий ответ не был валидным JSON. Верни только JSON-объект без пояснений. Ошибка: " + (error or ""),
            }
        ]
        try:
            second = self.complete(kind=kind, role=role, messages=retry_messages, budget=budget, mock_context=mock_context)
        except BudgetExceeded:
            return None, result, error
        parsed, error = _parse_json(second.text)
        return parsed, second, error

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        if self._transport is not None:
            return self._transport(payload, headers)
        url = f"{self.settings.llm_base_url}/chat/completions"
        response = self._request(url, headers, payload)
        if response.status_code == 400 and "response_format" in payload:
            reduced = {key: value for key, value in payload.items() if key != "response_format"}
            response = self._request(url, headers, reduced)
        response.raise_for_status()
        return response.json()

    def _request(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
        """Жёсткий предел: зависший сокет прокси не должен держать запрос дольше лимита."""
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["response"] = self._http.post(url, headers=headers, json=payload)
            except Exception as exc:  # noqa: BLE001 - ошибка уходит вызывающему коду
                box["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(150)
        if thread.is_alive():
            self._http.close()
            self._http = httpx.Client(timeout=httpx.Timeout(150.0, connect=10.0))
            raise httpx.TimeoutException("модель не ответила за 150 секунд")
        if "error" in box:
            raise box["error"]
        return box["response"]

    def _mock(self, kind: str, context: dict[str, Any]) -> str:
        if kind == "plan":
            return json.dumps(_mock_plan(context), ensure_ascii=False)
        if kind == "analyze":
            return json.dumps(_mock_analyze(context), ensure_ascii=False)
        if kind == "critic":
            return json.dumps(_mock_critic(context), ensure_ascii=False)
        if kind == "chat":
            return json.dumps(_mock_chat(context), ensure_ascii=False)
        raise ValueError(f"неизвестный тип промпта: {kind}")


class _SecretFilter(logging.Filter):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secret and self.secret in record.getMessage():
            record.msg = record.getMessage().replace(self.secret, "***")
            record.args = ()
        return True


def _cache_key(model: str, temperature: float, messages: list[dict[str, str]]) -> str:
    raw = json.dumps({"model": model, "temperature": temperature, "messages": messages}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "корень JSON должен быть объектом"
    return payload, None


def _read_completion(response: dict[str, Any], messages: list[dict[str, str]]) -> tuple[str, int, int]:
    text = response["choices"][0]["message"]["content"]
    usage = response.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or _estimate(messages))
    completion_tokens = int(usage.get("completion_tokens") or max(1, len(text) // 4))
    return text, prompt_tokens, completion_tokens


def _estimate(messages: list[dict[str, str]]) -> int:
    return max(1, sum(len(item.get("content") or "") for item in messages) // 4)


def default_plan(context: dict[str, Any]) -> dict[str, Any]:
    return _mock_plan(context)


def grounded_draft(context: dict[str, Any]) -> dict[str, Any]:
    return _mock_analyze(context)


def grounded_chat(context: dict[str, Any]) -> dict[str, Any]:
    return _mock_chat(context)


def _mock_plan(context: dict[str, Any]) -> dict[str, Any]:
    title = str(context.get("title") or "событие")
    indicator = context.get("indicator")
    steps = [
        {
            "tool": "search_bank_analytics",
            "args": {"query": title, "top_k": 3},
            "why": "Нужна позиция Альфа-Банка по этому событию, а не пересказ новости.",
        }
    ]
    if indicator:
        steps.append(
            {
                "tool": "get_macro_indicator",
                "args": {"name": indicator},
                "why": "Цифра показателя берётся только из материалов банка или новостей.",
            }
        )
    steps.append(
        {
            "tool": "get_news_cluster",
            "args": {"cluster_id": context.get("cluster_id")},
            "why": "Нужны первоисточники кластера, чтобы отделить факт от предположения.",
        }
    )
    return {"steps": steps}


def _pick_features(context: dict[str, Any]) -> list[dict[str, Any]]:
    features = list(context.get("features") or [])
    by_id = {item["feature_id"]: item for item in features}
    ordered: list[str] = []

    def take(feature_id: str) -> None:
        feature = by_id.get(feature_id)
        if feature and feature.get("active") and feature_id not in ordered:
            ordered.append(feature_id)

    matched = list(context.get("matched_feature_ids") or [])
    for feature_id in matched:
        take(feature_id)
    if not matched:
        for feature_id in _FEATURE_ORDER:
            take(feature_id)
            if len(ordered) >= 2:
                break
    elif len(ordered) < 2:
        for feature_id in _PAD_ORDER:
            take(feature_id)
            if len(ordered) >= 2:
                break
    if len(ordered) < 2:
        for feature in features:
            take(feature["feature_id"])
            if len(ordered) >= 2:
                break
    return [by_id[feature_id] for feature_id in ordered[:3]]


def _mock_analyze(context: dict[str, Any]) -> dict[str, Any]:
    cluster_id = str(context.get("cluster_id") or "cluster")
    client_id = str(context.get("client_id") or "client")
    title = str(context.get("title") or "Событие")
    summary = str(context.get("summary") or title)
    news_id = f"news:{cluster_id}"
    profile_id = f"profile:{client_id}"
    chosen = _pick_features(context)
    why_items = []
    for feature in chosen[:2]:
        template = _WHY.get(feature["feature_id"], "Признак клиента меняет чтение этого события.")
        why_items.append(
            {
                "text": template,
                "client_features": [feature["feature_id"]],
                "source_ids": [profile_id, news_id],
            }
        )
    if len(why_items) == 1 and chosen:
        why_items[0]["client_features"] = [item["feature_id"] for item in chosen[:2]]
    primary = chosen[0]["feature_id"] if chosen else "default"
    action = _ACTIONS.get(primary, _ACTIONS["default"])
    fragments = list(context.get("bank_fragments") or [])
    bank_view = []
    sources = [
        {"source_id": news_id, "kind": "news", "title": title, "url": context.get("url"), "published_at": context.get("published_at")},
        {"source_id": profile_id, "kind": "client_profile", "title": f"Профиль {client_id}", "url": None, "published_at": None},
    ]
    risks = []
    opportunities = []
    if "rate_sensitivity" in {item["feature_id"] for item in chosen}:
        risks.append(
            {
                "text": "Стоимость долга не снижается, пока ставка на паузе.",
                "source_ids": [news_id, profile_id],
                "origin": "ai",
            }
        )
    if "deposit_benefit" in {item["feature_id"] for item in chosen}:
        opportunities.append(
            {
                "text": "Доход от размещения остаётся высоким, пока ставка не снижена.",
                "source_ids": [news_id, profile_id],
                "origin": "ai",
            }
        )
    if "fx_sensitivity" in {item["feature_id"] for item in chosen}:
        risks.append(
            {
                "text": "Курсовая переоценка контрактов может сдвинуть маржу.",
                "source_ids": [news_id, profile_id],
                "origin": "ai",
            }
        )
    if not risks and not opportunities:
        risks.append(
            {
                "text": "Прямой финансовый эффект пока не подтверждён и зависит от недостающих признаков клиента.",
                "source_ids": [profile_id],
                "origin": "ai",
            }
        )
    if fragments:
        fragment = fragments[0]
        bank_view.append(
            {
                "text": fragment["text"],
                "doc_id": fragment["doc_id"],
                "doc_title": fragment["doc_title"],
                "fragment_id": fragment["fragment_id"],
            }
        )
        sources.append(
            {
                "source_id": fragment.get("source_id") or f"bank:{fragment['doc_id']}:{fragment['fragment_id']}",
                "kind": "bank_analytics",
                "title": fragment["doc_title"],
                "url": None,
                "published_at": fragment.get("published_at"),
            }
        )
    ids = {item["feature_id"] for item in chosen}
    if "rate_sensitivity" in ids or "fx_sensitivity" in ids:
        insight_type = "risk"
        urgency = "high"
        impact = "high"
    elif "deposit_benefit" in ids:
        insight_type = "opportunity"
        urgency = "medium"
        impact = "medium"
    else:
        insight_type = "neutral"
        urgency = "low"
        impact = "low"
    gaps = list(context.get("gaps") or [])
    confidence = "low" if gaps or not bank_view or len(chosen) < 2 else "medium"
    return {
        "insight_id": "pending",
        "client_id": client_id,
        "cluster_id": cluster_id,
        "generated_at": "2026-09-11T12:00:00+00:00",
        "headline": title,
        "type": insight_type,
        "urgency": urgency,
        "impact": impact,
        "confidence": confidence,
        "priority_score": 0.0,
        "what_happened": {
            "facts": [{"text": summary, "source_ids": [news_id], "status": "confirmed"}],
            "assumptions": [],
        },
        "why_it_matters": why_items or [
            {
                "text": "Связь события с компанией слабая: в профиле не хватает признаков.",
                "client_features": [chosen[0]["feature_id"]] if chosen else ["sector"],
                "source_ids": [profile_id],
            }
        ],
        "consequences": {
            "risks": risks,
            "opportunities": opportunities,
            "factors_to_watch": ["появление прямой позиции банка по этому событию"] if not bank_view else ["следующее решение по этому фактору"],
        },
        "actions": [{"text": action[0], "rationale": action[1], "origin": "ai", "product_hint": action[2]}],
        "bank_view": bank_view,
        "sources": sources,
        "client_features_used": [
            {
                "feature_id": item["feature_id"],
                "label": item["label"],
                "value": item["value"],
                "csv_field": item["csv_field"],
            }
            for item in chosen[:4]
        ],
        "trace_id": str(context.get("trace_id") or "pending"),
        "gaps": gaps,
    }


def _mock_critic(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("force_decision"):
        return {"issues": list(context.get("issues") or []), "decision": context["force_decision"]}
    draft = context.get("draft") or {}
    issues = []
    features: set[str] = set()
    for item in draft.get("why_it_matters") or []:
        features.update(item.get("client_features") or [])
    if len(features) < 2:
        issues.append({"code": "few_features", "detail": "в объяснении меньше двух признаков клиента"})
    actions = draft.get("actions") or []
    if not actions or len(str(actions[0].get("text") or "")) < 40:
        issues.append({"code": "vague_action", "detail": "действие слишком общее"})
    sections = ("what_happened", "why_it_matters", "consequences", "actions", "bank_view", "sources")
    if any(name not in draft for name in sections):
        issues.append({"code": "incomplete", "detail": "черновик не закрывает все шесть вопросов"})
    if not draft.get("bank_view"):
        issues.append({"code": "no_bank", "detail": "нет фрагмента аналитики банка"})
    if any(item["code"] == "few_features" for item in issues):
        decision = "rewrite"
    elif any(item["code"] == "no_bank" for item in issues) and not context.get("bank_searched_again"):
        decision = "retry_search"
    elif issues:
        decision = "give_up_low_confidence"
    else:
        decision = "ok"
    return {"issues": issues, "decision": decision}


def _mock_chat(context: dict[str, Any]) -> dict[str, Any]:
    insight = context.get("insight") or {}
    question = str(context.get("question") or "")
    why = (insight.get("why_it_matters") or [{}])[0]
    text = why.get("text") or "Сохранённый инсайт не содержит отдельного вывода по этому вопросу."
    source_ids = list(why.get("source_ids") or [])
    claims = [{"text": text, "origin": "ai", "source_ids": source_ids or ["profile:unknown"]}]
    fragments = list(context.get("bank_fragments") or [])
    gaps: list[str] = []
    if fragments:
        fragment = fragments[0]
        claims.append(
            {
                "text": fragment["text"],
                "origin": "bank",
                "source_ids": [fragment.get("source_id") or f"bank:{fragment['doc_id']}:{fragment['fragment_id']}"],
            }
        )
    elif "источник" in question.lower() or "откуда" in question.lower():
        gaps.append("в материалах банка нет отдельного фрагмента под этот вопрос")
    return {
        "insight_id": insight.get("insight_id") or context.get("insight_id"),
        "question": question,
        "answer": claims,
        "sources": list(context.get("sources") or insight.get("sources") or []),
        "confidence": "medium" if fragments else "low",
        "trace_id": context.get("trace_id") or "pending",
        "gaps": gaps,
    }
