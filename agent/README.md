# Агент ИНСАЙТ

Модуль строит персональный разбор новости для клиента Альфа-Банка: что произошло, почему это важно именно этой компании, какие риски и действия, и что по этому поводу написал банк. Агентный цикл, инструменты и проверка написаны в этом репозитории. LangChain, LangGraph, CrewAI и другие агентные фреймворки не используются.

Соседние модули `parser/`, `dedup/` и `frontend/` сюда не импортируются. Новости читаются из JSONL, контракт описан в [docs/contracts.md](docs/contracts.md).

## Запуск без ключа

Из каталога `agent/`, Python 3.12:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\uvicorn agent.api:create_app --factory --port 8000
```

Обычный запуск вызывает разрешённые модели. `MOCK_LLM=1` оставляет детерминированные ответы для тестов и не открывает сеть. Данные — синтетические клиенты `C_LOAN`, `C_DEPOSIT`, `C_IMPORT`, `C_DOMESTIC`.

Через общий compose из корня репозитория:

```bash
docker compose up --build agent
```

Compose монтирует CSV и PDF кейса в `/data` и вызывает модель. Ключ берётся из `agent/.env`: compose подхватывает файл, если он есть. Перед агентом одноразовый шаг пишет кластеры парсера и дедупликации в `AGENT_NEWS_PATH`. Если сбор пуст, туда попадает короткая синтетическая лента. Если CSV на диске нет, процесс пишет предупреждение и читает синтетику из образа. Одноимённые `.txt` уже лежат рядом с PDF, поэтому цитаты банка берутся из них. Если расшифровки нет, файл виден в `/health` как `bank_unreadable`. Локальный запуск без этих путей использует синтетические фрагменты, и `bank_view` в mock-прогоне заполнен.

## Переменные окружения

Образец — [.env.example](.env.example). Ключ и имена моделей только оттуда:

- `LLM_BASE_URL`, `LLM_API_KEY`
- `LLM_MODEL_LIGHT` — критик и простые проверки
- `LLM_MODEL_REASONING` — план и черновик инсайта
- `LLM_MODEL_DEFAULT` — чат
- `MOCK_LLM=1` — детерминированные ответы для тестов
- `AGENT_TOP_N` (10), `AGENT_MAX_ITERS` (2), `AGENT_MAX_STEPS`, `AGENT_MAX_TOKENS`, `AGENT_MAX_SECONDS`, `AGENT_MAX_LLM_CALLS`, `AGENT_CONCURRENCY`

Пути к CSV, новостям и PDF пустые в примере: тогда берётся `data/synthetic`. Для полного кейса укажите `df_Uid_data_hack.csv`, `df_Uid_payment_hack.csv`, `df_Fid_Uid_link_hack.csv` и каталог PDF.

Имена моделей по умолчанию: `google/gemma-4-26b-a4b-it`, `z-ai/glm-5.3-flash`, `qwen/qwen3.6-35b-a3b`. Их можно переопределить через `LLM_MODEL_*`.

## Сценарий проверки

1. `GET /health` — `status: ok`, без вызова модели.
2. Инсайты заёмщика:

```bash
curl -s -X POST http://localhost:8000/agent/insights ^
  -H "Content-Type: application/json" ^
  -d "{\"client_id\":\"C_LOAN\",\"period_from\":\"2026-09-01\",\"period_to\":\"2026-09-30\",\"top_n\":5}"
```

3. Тот же период для `C_DEPOSIT`. У новости про ставку 14% другой `why_it_matters` и более низкий `priority_score`: у депозитного клиента нет кредитов.
4. `C_IMPORT` и `C_DOMESTIC` на новости про курс юаня: у импортёра выше ранг и в тексте есть внешнеэкономическая деятельность.
5. `GET /traces/{trace_id}` — в шагах есть `plan`, вызовы инструментов и `verify`.
6. `POST /agent/chat` с `insight_id` и вопросом «Откуда этот вывод?». В ответе у каждого утверждения есть `origin` и `source_ids`.
7. `POST /agent/feedback` с `{"insight_id","useful":true}` — сигнал для метрик, не для дообучения модели.

Неизвестный `client_id` возвращает 404. Период без кластеров — пустой массив.

На реальных CSV крупный клиент с кредитом и ВЭД, например `U_9250` (стройка, Иркутская область). Новостную ленту для него даёт `AGENT_NEWS_PATH`.

## Аналитика банка

PDF из поставки кейса — постраничные картинки без текстового слоя. Рядом с каждым из девяти файлов лежит одноимённый `.txt`: текст страниц, который индекс цитирует дословно. Подписи осей, которые не складывались в фразу, в эти файлы не переносились. Синтетические фрагменты в `data/synthetic/bank/` остаются для тестов и mock-прогона без путей к кейсу.

## Тесты

```bash
.venv\Scripts\python -m pytest
```

Покрыты признаки профиля, скоринг, реестр инструментов, проверка источников и чисел, цикл с `MOCK_LLM=1`, персонализация через HTTP.

## Документы

- [docs/architecture.md](docs/architecture.md) — поток и формула ранга
- [docs/contracts.md](docs/contracts.md) — JSON для frontend и формат кластера
- [docs/security.md](docs/security.md) — белый список, изоляция клиента, лимиты
- [docs/metrics.md](docs/metrics.md) — как мерить качество
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — модели, библиотеки, данные
