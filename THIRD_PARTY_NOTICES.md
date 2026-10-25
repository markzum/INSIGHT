# Сторонние компоненты

Подробности по модулям: `agent/THIRD_PARTY_NOTICES.md`, `parser/THIRD_PARTY_NOTICES.md`, `dedup/THIRD_PARTY_NOTICES.md`, `frontend/THIRD_PARTY_NOTICES.md`.

## Модели

В коде нет имён моделей. Подключаются только те, что переданы в `LLM_MODEL_LIGHT`, `LLM_MODEL_REASONING` и `LLM_MODEL_DEFAULT`, и только из списка кейса:

- Gemma 4 26B A4B — критик
- GLM-5.3-Flash — план и черновик инсайта
- Qwen3.6 35B A3B — чат
- DeepSeek-V4.1-Flash, Qwen3-VL-30B-A3B-Instruct и Kimi-K2.6 можно поставить в те же переменные. Зрительная модель не вызывается: PDF без текстового слоя читаются из соседнего `.txt`, а не распознаются

Доступ — HTTP организаторов, совместимый с OpenAI (`LLM_BASE_URL`). Своего ключа провайдера в репозитории нет. При `MOCK_LLM=1` модель не вызывается.

LangChain, LangGraph, CrewAI, AutoGen и другие агентные каркасы не используются.

## Библиотеки

- Python 3.12: fastapi, uvicorn, pydantic, httpx, pyyaml, jsonschema, pypdf, rank-bm25, pandas. Версии в lock-файлах модулей
- Дедупликация и парсер не используют sklearn, faiss, datasketch, feedparser и BeautifulSoup. MinHash, Jaccard, TF-IDF, разбор RSS и HTML написаны в репозитории
- Node.js 22 для фронтенда. npm-пакетов и CDN нет

## Данные

- CSV кейса и PDF Центра макроэкономических исследований читаются с диска по путям из окружения и в git не входят
- `agent/data/synthetic/` — клиенты, платежи, кластеры и фрагменты банка для сквозного запуска без выгрузки кейса
- `parser/fixtures/` и `parser/data/documents.json` — офлайн-лента
- `dedup/data/` — синтетический корпус и разметка пар, на которых считаются precision и recall
- `frontend/data/demo.json` — учебный экран, если шлюз не подключён

## Смоделированные интеграции

- Ленты РБК, Коммерсанта, ТАСС, Интерфакса и Банка России запрашиваются только при `PARSER_OFFLINE=0`, по allowlist и с проверкой адреса
- Публичной RSS аналитики Альфа-Банка нет. Материалы банка — локальные PDF и фикстуры
- Интернет-банк не вызывается. Интерфейс повторяет сценарий Альфа-Бизнеса локально
- Обратная связь пользователя пишется в SQLite агента и не уходит во внешнее обучение
