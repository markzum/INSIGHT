# Парсер ИНСАЙТ

Модуль забирает новости из настроенных RSS/Atom-лент и локальные заметки Альфа-Банка, приводит их к одному JSON и ставит метки. Этот JSON принимает `dedup/` как документ: `id`, `source`, `title`, `text`, `published_at`, `language`, `url`, `raw_meta`, `content_kind`.

Модель не вызывается. Метки считаются по словарю `config/taxonomy.yaml` — тем же темам, что у агента: ставка, валюта, инфляция, сырьё и отрасли. В `raw_meta.key_facts` попадают предложения из текста, в которых есть число. В `raw_meta.entities` — названия из списка и цитаты в «ёлочках».

```
RSS / локальный файл
 → разбор ленты и снятие HTML
 → канонический URL
 → язык, дата в UTC, метки и факты
 → SQLite
 → POST /batch модуля dedup
```

## Откуда берутся материалы

`config/feeds.yaml`:

- РБК, Коммерсантъ, ТАСС, Интерфакс, Банк России — внешние ленты, `content_kind=external_news`
- `fixtures/alfa.xml` и каталог `fixtures/bank/` — материалы банка, `content_kind=bank_analysis`

Новый источник добавляется строкой в yaml: адрес ленты и список имён хостов, с которых можно скачать и ленту, и полный текст. Поддомен, которого нет в списке, не запрашивается.

По умолчанию `PARSER_OFFLINE=1`: читаются только `fixture` из yaml и локальные файлы, сеть не открывается. Живой сбор — `PARSER_OFFLINE=0`. Пустой `url` в сетевом режиме пропускается, поэтому банковская фикстура не уходит на выдуманный адрес.

Полный текст статьи запрашивается только по https, только у хоста из списка ленты, не больше двух редиректов и только если каждый редирект снова проходит ту же проверку. Числовой адрес, адрес с логином, частная и локальная сеть, ответ больше `PARSER_MAX_BYTES` отсекаются. Имя хоста разрешается до запроса; если хотя бы один адрес не публичный, запрос не отправляется.

Повтор того же URL с тем же текстом ничего не добавляет. Если текст по этому URL изменился, появляется новый id с суффиксом `-r2`, а в `raw_meta.supersedes` остаётся прежний. Для `dedup` это другой документ: при совпадении фактов он сольётся по URL, при разных числах или датах останется отдельным.

## Запуск

Из каталога `parser/`, Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m parser.cli
```

Команда пишет `data/documents.json`. API слушает порт 8002. Процесс один: индекс в SQLite.

```powershell
$env:PARSER_OFFLINE = "1"
$env:PARSER_DB = "data\parser.sqlite"
$env:PARSER_API_KEYS = '{"demo-token":"demo"}'
.venv\Scripts\python -m uvicorn parser.api:app --host 127.0.0.1 --port 8002 --workers 1
```

`demo-token` только для своей машины. Свой токен — JSON `{"токен":"имя"}`.

```powershell
curl.exe --fail -X POST http://127.0.0.1:8002/collect -H "Authorization: Bearer demo-token"
curl.exe --fail http://127.0.0.1:8002/documents -H "Authorization: Bearer demo-token"
curl.exe --fail http://127.0.0.1:8002/health
```

`GET /health` без токена. В журнал пишутся лента, число новых документов и имя из токена, без текста новости.

Дальше тот же файл отдаётся дедупликации, а её JSONL — агенту:

```powershell
curl.exe --fail -X POST http://127.0.0.1:8001/batch -H "Authorization: Bearer demo-token" -H "Content-Type: application/json" --data-binary "@data/documents.json"
```

Каталог `dedup/` после этого: `python -m dedup.export`. Путь `reports/clusters.jsonl` записывается в `AGENT_NEWS_PATH`.

Свои заметки банка: `.txt` или `.html` в каталог из `local_dirs`, либо путь в `PARSER_BANK_DIR`. У `.txt` первая строка `# заголовок`, вторая — `published_at: 2026-09-11T08:00:00Z`. PDF кейса этот модуль не читает: их индекс живёт в `agent/`.

Docker из корня репозитория, без сети:

```bash
docker compose up --build parser
```

## Проверка

Офлайн-набор: три материала РБК (ставка, юань, пустая заметка), одна лента банка про инфляцию и одна локальная заметка про ставку. Пустой текст остаётся документом с предупреждением и не роняет сбор. Повторный `POST /collect` возвращает пустой список новых документов.

Живые ленты в тестах не открываются. Если лента недоступна или XML битый, сбор остальных источников продолжается, причина попадает в `warnings`. Адреса РБК, Коммерсанта, ТАСС, Интерфакса и пресс-ленты Банка России разбираются текущим кодом: у каждой ленты читаются заголовок, ссылка и текст.
