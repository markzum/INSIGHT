# Контракты модуля `agent`

Документ для `frontend/` и `dedup/`. Агент не импортирует их код. Если формат соседа отличается, адаптер живёт внутри `agent/`.

## Инсайт

Каноническая схема: [`schemas/insight.schema.json`](../schemas/insight.schema.json).

`POST /agent/insights` возвращает JSON-массив объектов `Insight`, отсортированный по `priority_score` по убыванию.

Поле `origin` у рисков, возможностей и действий: `bank` — утверждение взято из фрагмента аналитики Альфа-Банка, `ai` — вывод модели. `bank_view` содержит только дословные фрагменты материалов банка. Необязательное поле `gaps` перечисляет, каких данных не хватило; пустой массив или отсутствие поля означает, что агент не зафиксировал пробел.

`product_hint` — код банковского продукта или `null`. Допустимые коды: `loan`, `deposit`, `guarantee`, `leasing`, `factoring`, `rko`, `fx_hedge`, `payroll`. Для роли, которой продукт не показывается, агент ставит `null`.

`priority_score` считает агент после генерации, модель его не назначает:

`relevance_score * w(impact) * w(urgency) * w(confidence)`, где `high = 1.0`, `medium = 0.6`, `low = 0.3`. `relevance_score` — оценка предотбора без LLM, от 0 до 1.

## Ответ чата

Схема: [`schemas/chat_answer.schema.json`](../schemas/chat_answer.schema.json).

`POST /agent/chat` принимает `{insight_id, question, role?}` и возвращает `ChatAnswer`. Каждое предложение ответа имеет `origin` и `source_ids`. Стартовый контекст — сохранённый инсайт и его источники.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | `/agent/insights` | `{client_id, period_from, period_to, top_n?, role?}` → массив инсайтов |
| GET | `/agent/insights/{insight_id}` | сохранённый инсайт |
| POST | `/agent/chat` | уточняющий вопрос по инсайту |
| GET | `/traces/{trace_id}` | журнал шагов этого инсайта или ответа чата |
| GET | `/health` | готовность процесса, без вызова LLM |

`role`: `ceo` (по умолчанию), `cfo`, `treasurer`. Неизвестный клиент — `404`. Период без кластеров — пустой массив.

Даты периода — `YYYY-MM-DD`. Границы включительны и сравниваются с календарной датой `published_at` кластера.

## Trace

`GET /traces/{trace_id}` возвращает объект:

- `trace_id`, `run_kind` (`insight` или `chat`), `client_id`, `cluster_id`, `insight_id`;
- `prompt_version` (сейчас `v1`);
- `plan` — JSON плана: `steps[]` с `tool`, `args`, `why`;
- `steps[]` — `kind` (`llm`, `tool`, `verify`), `name`, `args`, `result_preview`, `started_at`, `duration_ms`, `tokens`, `model`;
- `drafts[]` — промежуточные черновики;
- `verifications[]` — проблемы и решение `ok`, `retry_search`, `rewrite`, `give_up_low_confidence`;
- `features_used`, `sources_used`;
- `usage` — `llm_calls`, `prompt_tokens`, `completion_tokens`, `total_tokens`.

В `result_preview` попадает краткий результат, не полный текст статей.

## Кластеры новостей (`dedup/` → `agent/`)

Файл JSONL, путь `AGENT_NEWS_PATH`. Одна строка — один кластер:

```json
{
  "cluster_id": "string",
  "title": "string",
  "summary": "string",
  "published_at": "2026-09-11T12:00:00Z",
  "articles": [
    {
      "url": "string",
      "source": "string",
      "title": "string",
      "published_at": "2026-09-11T12:00:00Z",
      "text": "string"
    }
  ],
  "entities": ["string"],
  "tags": ["string"]
}
```

`tags` — машинные метки (`key_rate`, `fx`, `labor`, `inflation`, `commodities`, сектор). Если меток нет, агент сопоставляет заголовок и summary со словарём в `config/sensitivity_rules.yaml`. Адаптер молча пропускает битую строку и пишет предупреждение в лог.

## Профиль, который видит frontend через инсайт

`client_features_used[]`: `feature_id`, `label`, `value`, `csv_field`. Идентификаторы признаков стабильны: `sector`, `scale_revenue`, `scale_headcount`, `rate_sensitivity`, `fx_sensitivity`, `deposit_benefit`, `labor_sensitivity`, `region`, `counterparty_concentration`, `commodity_exposure`, `demand_sensitivity`. Полный список строится в `profile_features.py`.

## Идентификаторы источников

- `profile:{client_id}` — текст признаков клиента;
- `news:{cluster_id}` — сводка кластера;
- `news:{cluster_id}:{n}` — статья внутри кластера;
- `bank:{doc_id}:{fragment_id}` — фрагмент аналитики банка.

Каждый `source_ids` в утверждениях обязан встречаться в `sources` этого же объекта и в источниках, которые инструменты реально вернули в этом запуске.
