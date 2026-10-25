# Сторонние компоненты

Модуль дедупликации не вызывает LLM и не считает эмбеддинги. MinHash, LSH, Jaccard, TF-IDF и стемминг написаны в `src/dedup/features.py`.

## Библиотеки

- Python 3.12
- fastapi, uvicorn — HTTP API
- pydantic — документ и результат
- httpx — зависимость тестового клиента Starlette, в рантайм-образ не входит
- pytest — тесты, в рантайм-образ не входит

datasketch, scikit-learn, faiss, qdrant, rapidfuzz и агентные фреймворки не подключаются.

## Данные

`data/documents.json` и `data/labels.json` — синтетические новости и разметка пар. Выгрузки кейса и ключи API в этот модуль не входят.
