import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from parser.api import app
from parser.classify import classify, load_taxonomy
from parser.config import load_settings
from parser.feeds import FeedParseError, canonicalize_url, html_to_text, parse_feed, to_utc_z
from parser.fetch import FetchDenied, HttpFetcher, check_url
from parser.pipeline import _remember, collect
from parser.store import Store

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = frozenset({"rbc.ru"})


def test_url_strips_tracking_and_www() -> None:
    assert canonicalize_url("https://www.rbc.ru/news/1/?utm_source=mail&b=2") == "https://rbc.ru/news/1?b=2"
    assert canonicalize_url("http://rbc.ru/news/1?b=2") == "https://rbc.ru/news/1?b=2"


def test_html_drops_script_and_keeps_text() -> None:
    text, title = html_to_text("<html><title>Заголовок</title><p>Видно</p><script>secret()</script></html>")
    assert title == "Заголовок"
    assert "Видно" in text
    assert "secret" not in text


def test_atom_and_broken_xml() -> None:
    items = parse_feed(
        """
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Атом</title>
            <id>atom-1</id>
            <updated>2026-09-11T08:00:00Z</updated>
            <link rel="alternate" href="https://example.com/a"/>
            <summary>Текст записи достаточно длинный.</summary>
          </entry>
        </feed>
        """
    )
    assert items[0].title == "Атом"
    assert items[0].link == "https://example.com/a"
    assert to_utc_z(items[0].published_raw) == "2026-09-11T08:00:00Z"
    with pytest.raises(FeedParseError):
        parse_feed("<nope>")


def test_cp1251_feed_decodes() -> None:
    from parser.feeds import decode_bytes

    xml = """<?xml version="1.0" encoding="windows-1251"?><rss version="2.0"><channel><item>
      <title>Ставка</title><link>https://cbr.ru/a</link><description>Текст решения Банка России.</description>
    </item></channel></rss>"""
    items = parse_feed(decode_bytes(xml.encode("cp1251")))
    assert items[0].title == "Ставка"


def test_taxonomy_marks_rate_fx_and_quote() -> None:
    taxonomy = load_taxonomy(ROOT / "config" / "taxonomy.yaml")
    found = classify(
        "Банк России сохранил ключевую ставку",
        "«Совет директоров» сохранил ставку на уровне 14%. Курс юаня тоже вырос.",
        taxonomy,
    )
    assert "key_rate" in found["tags"]
    assert "fx" in found["tags"]
    assert "Банк России" in found["entities"]
    assert "Совет директоров" in found["entities"]
    assert any("14" in fact for fact in found["key_facts"])


def test_private_and_foreign_urls_are_denied() -> None:
    public = lambda host: ["1.1.1.1"]
    with pytest.raises(FetchDenied):
        check_url("http://rbc.ru/a", ALLOWED, public)
    with pytest.raises(FetchDenied):
        check_url("https://user:pass@rbc.ru/a", ALLOWED, public)
    with pytest.raises(FetchDenied):
        check_url("https://127.0.0.1/latest", ALLOWED, public)
    with pytest.raises(FetchDenied):
        check_url("https://169.254.169.254/latest", ALLOWED, public)
    with pytest.raises(FetchDenied):
        check_url("https://evil.example/a", ALLOWED, public)
    with pytest.raises(FetchDenied):
        check_url("https://rbc.ru/a", ALLOWED, lambda host: ["10.1.1.1"])
    with pytest.raises(FetchDenied):
        check_url("https://rbc.ru/a", ALLOWED, lambda host: ["::ffff:10.1.1.1"])
    assert check_url("https://www.rbc.ru/a", ALLOWED, public) == "https://www.rbc.ru/a"


def test_redirect_off_allowlist_is_denied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "rbc.ru":
            raise AssertionError(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetcher = HttpFetcher(client, resolver=lambda host: ["1.1.1.1"])
        with pytest.raises(FetchDenied):
            fetcher.get("https://rbc.ru/go", ALLOWED)


def test_private_dns_does_not_send_request() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetcher = HttpFetcher(client, resolver=lambda host: ["10.0.0.8"])
        with pytest.raises(FetchDenied):
            fetcher.get("https://rbc.ru/a", ALLOWED)
    assert called is False


def test_response_over_limit_is_denied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"123456789")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetcher = HttpFetcher(client, resolver=lambda host: ["1.1.1.1"], max_bytes=4)
        with pytest.raises(FetchDenied):
            fetcher.get("https://rbc.ru/a", ALLOWED)


def test_offline_collect_is_stable_and_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSER_OFFLINE", "1")
    monkeypatch.setenv("PARSER_DB", str(tmp_path / "parser.sqlite"))
    monkeypatch.delenv("PARSER_FEED_IDS", raising=False)
    monkeypatch.delenv("PARSER_BANK_DIR", raising=False)
    settings = load_settings(ROOT)
    store = Store(tmp_path / "parser.sqlite")
    first = collect(settings, _Boom(), store)
    assert first.warnings == []
    assert len(first.documents) == 5
    by_title = {item.title: item for item in first.documents}
    rate = by_title["Банк России сохранил ключевую ставку"]
    assert rate.url == "https://rbc.ru/economics/11/09/2026/rate"
    assert rate.published_at == "2026-09-11T08:00:00Z"
    assert rate.language == "ru"
    assert rate.content_kind == "external_news"
    assert "key_rate" in rate.raw_meta["tags"]
    assert "Банк России" in rate.raw_meta["entities"]
    assert "Совет директоров" in rate.raw_meta["entities"]
    assert any("14" in fact for fact in rate.raw_meta["key_facts"])
    yuan = by_title["Курс юаня вырос"]
    assert "fx" in yuan.raw_meta["tags"]
    assert "юанях" in yuan.text
    assert "secret" not in yuan.text
    assert by_title["Коротко"].raw_meta["warnings"]
    assert by_title["Инфляция остаётся высокой"].content_kind == "bank_analysis"
    assert "inflation" in by_title["Инфляция остаётся высокой"].raw_meta["tags"]
    assert by_title["Сохранение ключевой ставки"].content_kind == "bank_analysis"
    second = collect(settings, _Boom(), store)
    assert second.documents == []
    assert second.skipped == 5
    assert {item.id for item in store.documents()} == {item.id for item in first.documents}
    store.close()


def test_live_body_replaces_short_rss_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSER_OFFLINE", "0")
    monkeypatch.setenv("PARSER_FEED_IDS", "rbc")
    monkeypatch.delenv("PARSER_BANK_DIR", raising=False)
    settings = load_settings(ROOT)
    store = Store(tmp_path / "live.sqlite")
    result = collect(settings, _Pages(), store)
    yuan = next(item for item in result.documents if item.title == "Курс юаня вырос")
    assert "ПОЛНЫЙ-ТЕКСТ" in yuan.text
    assert "secret" not in yuan.text
    assert any("полный текст не загружен" in note for note in result.warnings)
    store.close()


def test_changed_text_creates_revision(tmp_path: Path) -> None:
    store = Store(tmp_path / "rev.sqlite")
    taxonomy = load_taxonomy(ROOT / "config" / "taxonomy.yaml")
    text = "Банк России сохранил ключевую ставку на уровне 14% годовых после заседания."
    first, status = _remember(
        store,
        feed_id="rbc",
        source="РБК",
        kind="external_news",
        title="Ставка",
        text=text,
        link="https://rbc.ru/rate",
        guid="g",
        published_raw="2026-09-11T08:00:00Z",
        taxonomy=taxonomy,
    )
    changed, second = _remember(
        store,
        feed_id="rbc",
        source="РБК",
        kind="external_news",
        title="Ставка",
        text=text.replace("14%", "15%"),
        link="https://www.rbc.ru/rate?utm_source=tg",
        guid="g",
        published_raw="2026-09-11T08:00:00Z",
        taxonomy=taxonomy,
    )
    assert status == "new" and second == "new"
    assert first is not None and changed is not None
    assert changed.id == f"{first.id}-r2"
    assert changed.raw_meta["supersedes"] == first.id
    again, third = _remember(
        store,
        feed_id="rbc",
        source="РБК",
        kind="external_news",
        title="Ставка",
        text=text.replace("14%", "15%"),
        link="https://rbc.ru/rate",
        guid="g",
        published_raw="2026-09-11T08:00:00Z",
        taxonomy=taxonomy,
    )
    assert third == "skip" and again is None
    assert [item.id for item in store.documents()] == [changed.id]
    store.close()


def test_api_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSER_OFFLINE", "1")
    monkeypatch.setenv("PARSER_DB", str(tmp_path / "api.sqlite"))
    monkeypatch.setenv("PARSER_API_KEYS", '{"secret":"demo"}')
    monkeypatch.setenv("PARSER_HOME", str(ROOT))
    monkeypatch.delenv("PARSER_FEED_IDS", raising=False)
    monkeypatch.delenv("PARSER_BANK_DIR", raising=False)
    from parser import api as api_module

    api_module._stores.clear()
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/collect").status_code == 401
    headers = {"Authorization": "Bearer secret"}
    created = client.post("/collect", headers=headers)
    assert created.status_code == 200
    body = created.json()
    assert len(body["documents"]) == 5
    assert set(body["documents"][0]) >= {"id", "source", "title", "text", "published_at", "language", "url", "raw_meta", "content_kind"}
    listed = client.get("/documents", headers=headers)
    assert len(listed.json()) == 5
    feeds = client.get("/feeds", headers=headers)
    assert {item["id"] for item in feeds.json()} == {"rbc", "alfa"}


def test_checked_in_schema_matches_model() -> None:
    schema = json.loads((ROOT / "schemas" / "document.schema.json").read_text(encoding="utf-8"))
    from parser.models import ParsedDocument

    assert set(schema["properties"]) == set(ParsedDocument.model_json_schema()["properties"])


class _Boom:
    def get(self, url: str, allowed_hosts: frozenset[str]) -> tuple[bytes, str]:
        raise AssertionError(url)


class _Pages:
    def get(self, url: str, allowed_hosts: frozenset[str]) -> tuple[bytes, str]:
        if url.endswith("full.rss"):
            return (ROOT / "fixtures" / "rbc.xml").read_bytes(), url
        if "yuan" in url:
            page = (
                "<p>ПОЛНЫЙ-ТЕКСТ. Курс китайской валюты вырос на 3% за неделю. "
                "Компании следят за расчетами в юанях и за валютой расчётов.</p><script>secret()</script>"
            )
            return page.encode(), url
        raise FetchDenied("нет страницы")
