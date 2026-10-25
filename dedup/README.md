# Дедупликация ИНСАЙТ

Модуль собирает перепечатки одной новости в кластер и оставляет отдельным материалом аналитику Альфа-Банка о том же событии. Сравнение идёт по каноническому URL, SHA-256 нормализованного текста, MinHash и точному Jaccard. Заголовки сравниваются косинусом TF-IDF после стемминга. Модель и эмбеддинги не вызываются.

Индекс живёт в SQLite. Новый документ дописывает свои бакеты LSH и не пересчитывает подписи уже сохранённых документов.

```
парсер
 → нормализация URL и токенов
 → точное совпадение URL или SHA-256
 → кандидаты MinHash LSH
 → Jaccard и косинус заголовка, проверка чисел, дат, сущностей и полярности
 → complete-link: документ должен совпасть со всеми участниками кластера
 → выбор канонического документа
 → provenance
 → JSONL для агента
```

## Пороги

Версия алгоритма `1.0.0`. Пороги стартовые, на живой ленте их нужно калибровать отдельно.

| Проверка | Условие |
| --- | --- |
| Текстовый хэш | SHA-256 токенов, если их не меньше 12 |
| Near-duplicate | оба текста от 30 токенов, один и тот же указанный язык |
| Шинглы | тройки слов |
| MinHash | 64 компоненты, 16 полос по 4 |
| Решение о near | Jaccard ≥ 0,72 и косинус заголовка ≥ 0,65 |
| Окно near | не больше 7 суток между `published_at` |
| Разные события | разные числа, разные даты, пустое пересечение сущностей или разная полярность (снижение, повышение, сохранение) |
| Канонический документ | домен из белого списка, затем более ранняя дата, более длинный текст, больше сущностей, меньший id |

Белый список доменов: `rbc.ru`, `kommersant.ru`, `tass.ru`, `interfax.ru`, `cbr.ru`, `alfabank.ru`.

Конфликт чисел, дат, сущностей или полярности запрещает объединение даже при одинаковом URL или хэше. `bank_analysis` не сливается с `external_news`. Пустой и короткий текст не получают общий хэш и не участвуют в near. Редиректы разрешаются только локальным словарём алиасов в конструкторе `Deduplicator`: модуль не открывает URL.

`cluster_id` не меняется, когда в кластер приходит более ранний источник. Меняются `canonical_id` и `cluster_version`. Рёбра provenance только дописываются.

Повтор того же `id` с тем же содержимым идемпотентен. Другой текст под тем же `id` — ошибка `IdentityConflict` и HTTP 409.

## Запуск

Из каталога `dedup/`, Python 3.12.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m dedup.synthetic
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m dedup.evaluate
```

На Linux и macOS venv активируется через `.venv/bin/python`.

API слушает порт 8001, чтобы не пересечься с агентом на 8000. Процесс один: индекс SQLite не рассчитан на несколько воркеров.

```powershell
$env:DEDUP_DB = "dedup.sqlite"
$env:DEDUP_API_KEYS = '{"demo-token":"demo"}'
.venv\Scripts\python -m uvicorn dedup.api:app --host 127.0.0.1 --port 8001 --workers 1
```

`demo-token` годится только для локальной проверки. Свой токен задаётся так: JSON-объект `{"токен":"tenant"}`. Клиент в данных не хранится, арендатор берётся только из этого объекта.

```powershell
curl.exe --fail -X POST http://127.0.0.1:8001/batch -H "Authorization: Bearer demo-token" -H "Content-Type: application/json" --data-binary "@data/documents.json"
curl.exe --fail http://127.0.0.1:8001/clusters -H "Authorization: Bearer demo-token"
curl.exe --fail http://127.0.0.1:8001/health
```

`GET /health` без токена. Остальные маршруты требуют `Authorization: Bearer`. В журнал пишутся арендатор, id документа, кластер и метод, без текста новости.

Выгрузка для агента:

```bash
.venv\Scripts\python -m dedup.export
```

Файл `reports/clusters.jsonl` — одна строка на кластер: `cluster_id`, `title`, `summary` (до 1200 символов), `published_at`, `articles`, `entities`, `tags`. Агенту этот путь передаётся как `AGENT_NEWS_PATH`. Банковские кластеры в этой выгрузке остаются отдельными строками и не заменяют PDF-индекс агента.

Docker из корня репозитория:

```bash
docker compose up --build dedup
```

## Синтетика и метрики

`data/documents.json` — 100 документов: 12 сюжетов по 4 документа, 8 сюжетов по 3, 28 одиночных. Одиночные включают ловушки с другим решением и другой датой, копии сюжетов с `content_kind=bank_analysis`, пустые тексты, короткие заметки и отдельные сюжеты, в том числе один английский.

На этом наборе отчёт `reports/metrics.json`: 48 кластеров, precision/recall/F1 = 1, точные пары 36/36, near-пары 60/60, ложных объединений нет. Это проверка генератора, а не оценка качества на реальной ленте.

## Состав

```
dedup/
  src/dedup/          модели, признаки, индекс, API, синтетика, метрики, экспорт
  tests/test_dedup.py
  data/               documents.json и labels.json
  schemas/            JSON Schema, OpenAPI, пример результата
  reports/            metrics.json и quality.md
  Dockerfile
  requirements.lock
```

Контракт результата: `canonical_id`, `cluster_id`, `cluster_version`, `canonical_document`, `duplicates`, `method`, `similarity_score`, `provenance`, `warnings`. `confirmed_facts` и `model_inferences` пустые: модуль не подтверждает новость и не пишет вывод. `bank_analysis` заполняется только у кластера банковского материала.
