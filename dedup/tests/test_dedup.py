import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dedup.api import app
from dedup.core import Deduplicator, IdentityConflict
from dedup.evaluate import score
from dedup.export import export_engine
from dedup.features import canonicalize_url, prepare, title_cosine
from dedup.models import DedupResult, Document
from dedup.synthetic import build_corpus


def test_url_strips_tracking_and_www() -> None:
    left = canonicalize_url("https://www.rbc.ru/news/1/?utm_source=mail&b=2")
    right = canonicalize_url("http://rbc.ru/news/1?b=2")
    assert left == right == "https://rbc.ru/news/1?b=2"


def test_exact_url_utm_and_text_hash_collapse(tmp_path: Path) -> None:
    engine = Deduplicator(tmp_path / "dedup.sqlite", tenant="demo")
    text = "Совет директоров 11 сентября 2026 года сохранил показатель номер 5 на уровне 25 процента. " + " ".join(["рынок"] * 40)
    first = engine.add(_sample("a", text, "https://rbc.ru/news/5", "2026-09-11T08:00:00Z"))
    second = engine.add(_sample("b", text, "https://www.rbc.ru/news/5?utm_source=tg", "2026-09-11T09:00:00Z"))
    third = engine.add(_sample("c", text, "https://tass.ru/ekonomika/5", "2026-09-11T10:00:00Z"))
    assert first.cluster_id == second.cluster_id == third.cluster_id
    assert second.method == "url"
    assert third.method == "text_hash"
    assert {item.id for item in second.duplicates} == {"b"}
    assert second.provenance[-1].matched_document_id == "a"
    assert second.canonical_id == "a"
    engine.close()


def test_near_reprint_collapses_and_trap_does_not(tmp_path: Path) -> None:
    documents, labels = build_corpus()
    by_id = {item["id"]: item for item in documents}
    engine = Deduplicator(tmp_path / "dedup.sqlite")
    anchor = engine.add(by_id["s0-anchor"])
    near = engine.add(by_id["s0-near1"])
    trap = engine.add(by_id["trap-0"])
    assert near.cluster_id == anchor.cluster_id
    assert near.method == "near_duplicate"
    assert near.similarity_score >= 0.65
    assert trap.cluster_id != anchor.cluster_id
    assert any("не объединён" in note for note in trap.warnings)
    engine.close()
    assert labels["trap-0"]["gold_cluster"] != labels["s0-anchor"]["gold_cluster"]


def test_bank_analysis_stays_separate(tmp_path: Path) -> None:
    documents, _labels = build_corpus()
    by_id = {item["id"]: item for item in documents}
    engine = Deduplicator(tmp_path / "dedup.sqlite")
    news = engine.add(by_id["s0-anchor"])
    bank = engine.add(by_id["bank-0"])
    assert bank.cluster_id != news.cluster_id
    assert bank.bank_analysis
    assert news.bank_analysis == []
    engine.close()


def test_empty_documents_do_not_collapse(tmp_path: Path) -> None:
    engine = Deduplicator(tmp_path / "dedup.sqlite")
    first = engine.add({"id": "e1", "source": "x", "title": "", "text": "", "published_at": None, "language": "ru", "url": None, "raw_meta": {}})
    second = engine.add({"id": "e2", "source": "x", "title": "", "text": "", "published_at": None, "language": "ru", "url": None, "raw_meta": {}})
    assert first.cluster_id != second.cluster_id
    assert first.warnings
    engine.close()


def test_same_id_is_idempotent_until_content_changes(tmp_path: Path) -> None:
    engine = Deduplicator(tmp_path / "dedup.sqlite")
    body = _sample("same", "текст " * 20, "https://rbc.ru/a", "2026-09-11T08:00:00Z")
    first = engine.add(body)
    again = engine.add(body)
    assert again.cluster_id == first.cluster_id
    assert again.warnings == ["повтор того же документа"]
    changed = dict(body)
    changed["text"] = "другой текст " * 20
    with pytest.raises(IdentityConflict):
        engine.add(changed)
    engine.close()


def test_tenants_do_not_see_each_other(tmp_path: Path) -> None:
    path = tmp_path / "dedup.sqlite"
    left = Deduplicator(path, tenant="one")
    right = Deduplicator(path, tenant="two")
    body = _sample("doc", "общий текст " * 20, "https://rbc.ru/shared", "2026-09-11T08:00:00Z")
    left.add(body)
    isolated = right.add(body)
    assert isolated.duplicates == []
    left.close()
    right.close()


def test_index_survives_restart_and_canonical_can_change(tmp_path: Path) -> None:
    path = tmp_path / "dedup.sqlite"
    engine = Deduplicator(path, aliases={"https://old.example/item": "https://rbc.ru/item"})
    late = engine.add(_sample("late", "текст решения " * 30, "https://example.com/item", "2026-09-11T12:00:00Z"))
    engine.close()
    reopened = Deduplicator(path, aliases={"https://old.example/item": "https://rbc.ru/item"})
    early = reopened.add(_sample("early", "текст решения " * 30, "https://old.example/item", "2026-09-11T08:00:00Z"))
    assert early.cluster_id == late.cluster_id
    assert early.canonical_id == "early"
    assert early.cluster_version == 2
    reopened.close()


def test_complete_link_blocks_transitive_merge(tmp_path: Path) -> None:
    engine = Deduplicator(tmp_path / "dedup.sqlite")
    base = "Совет директоров 11 сентября 2026 года сохранил показатель номер 3 на уровне 23 процента. " + " ".join(["контракт"] * 80)
    left = base.replace("контракт", "договор", 1)
    middle = base
    right = "Совет директоров 2 февраля 2026 года снизил показатель номер 3 до 11 процента. " + " ".join(["контракт"] * 80)
    a = engine.add(_sample("A", left, "https://rbc.ru/A", "2026-09-11T08:00:00Z", entities=["сюжет-3"]))
    b = engine.add(_sample("B", middle, "https://kommersant.ru/B", "2026-09-11T09:00:00Z", entities=["сюжет-3"]))
    c = engine.add(_sample("C", right, "https://kommersant.ru/C", "2026-09-11T10:00:00Z", entities=["сюжет-3"]))
    assert b.cluster_id == a.cluster_id
    assert c.cluster_id != a.cluster_id
    engine.close()


def test_synthetic_metrics_are_perfect(tmp_path: Path) -> None:
    documents, labels = build_corpus()
    assert len(documents) == 100
    metrics = score(documents, labels, tmp_path / "eval.sqlite")
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["precision"] == 1
    assert metrics["recall"] == 1
    assert metrics["exact_recalled"] == metrics["exact_pairs"] == 36
    assert metrics["near_recalled"] == metrics["near_pairs"] == 60


def test_export_matches_agent_cluster_shape(tmp_path: Path) -> None:
    documents, _labels = build_corpus()
    engine = Deduplicator(tmp_path / "dedup.sqlite")
    engine.batch(documents[:4])
    destination = tmp_path / "clusters.jsonl"
    count = export_engine(engine, destination)
    engine.close()
    assert count == 1
    record = json.loads(destination.read_text(encoding="utf-8").splitlines()[0])
    assert set(record) >= {"cluster_id", "title", "summary", "published_at", "articles", "entities", "tags"}
    assert len(record["articles"]) == 4
    assert record["articles"][0]["text"]


def test_api_requires_token_and_returns_clusters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEDUP_DB", str(tmp_path / "api.sqlite"))
    monkeypatch.setenv("DEDUP_API_KEYS", '{"secret":"demo"}')
    from dedup import api as api_module

    api_module._engines.clear()
    client = TestClient(app)
    denied = client.post("/documents", json=_sample("n1", "текст " * 20, "https://rbc.ru/n", "2026-09-11T08:00:00Z"))
    assert denied.status_code == 401
    headers = {"Authorization": "Bearer secret"}
    created = client.post("/documents", headers=headers, json=_sample("n1", "текст " * 20, "https://rbc.ru/n", "2026-09-11T08:00:00Z"))
    assert created.status_code == 200
    listed = client.get("/clusters", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["canonical_id"] == "n1"
    exported = client.get("/export/jsonl", headers=headers)
    assert exported.status_code == 200
    assert "Заголовок решения" in exported.text
    health = client.get("/health")
    assert health.json()["status"] == "ok"


def test_checked_in_schemas_match_models() -> None:
    root = Path(__file__).resolve().parents[1]
    document = json.loads((root / "schemas" / "document.schema.json").read_text(encoding="utf-8"))
    result = json.loads((root / "schemas" / "dedup-result.schema.json").read_text(encoding="utf-8"))
    assert set(document["properties"]) == set(Document.model_json_schema()["properties"])
    assert set(result["properties"]) == set(DedupResult.model_json_schema()["properties"])


def test_title_similarity_of_reorder_stays_high() -> None:
    left = prepare(Document.model_validate(_sample("l", "текст " * 40, None, "2026-09-11T08:00:00Z", title="компания 1 сохранила показатель на уровне 21 процента")))
    right = prepare(Document.model_validate(_sample("r", "текст " * 40, None, "2026-09-11T08:00:00Z", title="показатель компании 1 сохранен на уровне 21 процента")))
    assert title_cosine(list(left.title_stems), list(right.title_stems), {}, 0) >= 0.65


def _sample(doc_id: str, text: str, url: str | None, published: str, entities: list[str] | None = None, title: str = "Заголовок решения") -> dict:
    return {
        "id": doc_id,
        "source": "Издание",
        "title": title,
        "text": text,
        "published_at": published,
        "language": "ru",
        "url": url,
        "raw_meta": {"entities": entities or ["сюжет"]},
        "content_kind": "external_news",
    }
