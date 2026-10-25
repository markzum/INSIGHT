"""Синтетический набор: точные копии, перепечатки и ловушки разных событий."""

from __future__ import annotations

import json
import random
from pathlib import Path

LEXICON = [
    "рынок", "компания", "поставка", "контракт", "оборот", "маржа", "склад", "логистика",
    "покупатель", "производитель", "регион", "отрасль", "выручка", "закупка", "партия",
    "договор", "отсрочка", "предел", "спрос", "предложение", "канал", "опт", "розница",
    "импорт", "экспорт", "валюта", "расчет", "банк", "кредит", "депозит", "гарантия",
    "лизинг", "факторинг", "бюджет", "инфляция", "ожидание", "комментарий", "обзор",
    "аналитика", "прогноз", "квартал", "месяц", "неделя", "динамика", "уровень", "риск",
    "возможность", "действие", "источник", "цитата", "фрагмент", "таблица", "график",
    "сценарий", "условие", "оговорка", "подробность", "контекст", "сравнение", "оценка",
]

ROOT = Path(__file__).resolve().parents[2]


def build_corpus() -> tuple[list[dict], dict[str, dict]]:
    documents: list[dict] = []
    labels: dict[str, dict] = {}

    def add(document: dict, gold: str, relation: str) -> None:
        documents.append(document)
        labels[document["id"]] = {"gold_cluster": gold, "relation": relation}

    for number in range(12):
        fact = (
            f"Совет директоров 11 сентября 2026 года сохранил показатель номер {number} "
            f"на уровне {20 + number} процента."
        )
        base = filler(1000 + number)
        text = _text(fact, base)
        title = f"компания {number} сохранила показатель на уровне {20 + number} процента"
        add(_doc(f"s{number}-anchor", title, text, f"https://rbc.ru/news/{number}", "2026-09-11T08:00:00Z", number), f"gold-{number}", "anchor")
        add(
            _doc(
                f"s{number}-exact",
                title,
                text,
                f"https://www.rbc.ru/news/{number}?utm_source=mail&utm_medium=email",
                "2026-09-11T09:00:00Z",
                number,
            ),
            f"gold-{number}",
            "exact",
        )
        add(
            _doc(
                f"s{number}-near1",
                f"показатель компании {number} сохранен на уровне {20 + number} процента",
                _text(fact, _edited(base, "альфаобзор", (20,))),
                f"https://kommersant.ru/doc/{number}-a",
                "2026-09-11T11:00:00Z",
                number,
            ),
            f"gold-{number}",
            "near",
        )
        add(
            _doc(
                f"s{number}-near2",
                f"на уровне {20 + number} процента компания {number} сохранила показатель",
                _text(fact, _edited(base, "бетаобзор", (80,))),
                f"https://kommersant.ru/doc/{number}-b",
                "2026-09-11T12:00:00Z",
                number,
            ),
            f"gold-{number}",
            "near",
        )

    for number in range(12, 20):
        fact = (
            f"Совет директоров 11 сентября 2026 года сохранил показатель номер {number} "
            f"на уровне {20 + number} процента."
        )
        text = _text(fact, filler(1000 + number))
        title = f"компания {number} сохранила показатель на уровне {20 + number} процента"
        add(_doc(f"s{number}-anchor", title, text, f"https://rbc.ru/news/{number}", "2026-09-11T08:00:00Z", number), f"gold-{number}", "anchor")
        add(
            _doc(
                f"s{number}-exact1",
                title,
                text,
                f"https://rbc.ru/news/{number}/?utm_campaign=digest",
                "2026-09-11T09:30:00Z",
                number,
            ),
            f"gold-{number}",
            "exact",
        )
        add(
            _doc(f"s{number}-exact2", title, text, f"https://tass.ru/ekonomika/{number}", "2026-09-12T07:00:00Z", number),
            f"gold-{number}",
            "exact",
        )

    shared = filler(1000)
    traps = (
        ("компания 0 снизила показатель на уровне 20 процента", "Совет директоров 6 июня 2026 года снизил показатель номер 0 до 16 процента.", "2026-09-10T08:00:00Z", shared),
        ("компания 0 повысила показатель на уровне 20 процента", "Совет директоров 1 августа 2026 года повысил показатель номер 0 до 19 процента.", "2026-09-12T08:00:00Z", shared),
        ("компания 0 повысила показатель на уровне 20 процента", "Совет директоров 1 марта 2026 года повысил показатель номер 0 до 18 процента.", "2026-09-13T08:00:00Z", shared),
        ("свежий комментарий без общего сюжета 77", "Самостоятельный комментарий номер 77 опубликован 20 сентября 2026 года.", "2026-09-20T08:00:00Z", filler(3077)),
    )
    for index, (title, fact, when, words) in enumerate(traps):
        add(
            _doc(
                f"trap-{index}",
                title,
                _text(fact, words),
                f"https://example.com/trap/{index}",
                when,
                0 if index < 3 else 900 + index,
            ),
            f"trap-{index}",
            "singleton",
        )

    for number in range(6):
        source = next(item for item in documents if item["id"] == f"s{number}-anchor")
        add(
            _doc(
                f"bank-{number}",
                source["title"],
                source["text"],
                f"https://alfabank.ru/research/{number}",
                source["published_at"],
                number,
                source_name="Альфа-Банк",
                kind="bank_analysis",
            ),
            f"bank-{number}",
            "singleton",
        )

    for number in range(8):
        add(
            {
                "id": f"empty-{number}",
                "source": "Неизвестный источник",
                "title": "",
                "text": "",
                "published_at": "2026-09-11T00:00:00Z",
                "language": "ru",
                "url": None,
                "raw_meta": {},
                "content_kind": "external_news",
            },
            f"empty-{number}",
            "singleton",
        )

    for number in range(4):
        add(
            _doc(f"short-{number}", "Заметка", "Мало слов тут", None, "2026-09-11T00:00:00Z", 700 + number),
            f"short-{number}",
            "singleton",
        )

    for number in range(5):
        add(
            _doc(
                f"solo-{number}",
                f"отдельный сюжет {number} про поставку оборудования",
                _text(f"Поставка оборудования номер {100 + number} согласована 2 октября 2026 года.", filler(5000 + number)),
                f"https://example.com/solo/{number}",
                "2026-10-02T08:00:00Z",
                800 + number,
            ),
            f"solo-{number}",
            "singleton",
        )
    add(
        {
            "id": "solo-en",
            "source": "Wire",
            "title": "Equipment delivery number 200 agreed on 2 October 2026",
            "text": "The company confirmed an equipment delivery. This English note is not a translation match.",
            "published_at": "2026-10-02T08:00:00Z",
            "language": "en",
            "url": "https://example.com/solo/en",
            "raw_meta": {"entities": ["equipment"], "tags": ["trade"]},
            "content_kind": "external_news",
        },
        "solo-en",
        "singleton",
    )
    return documents, labels


def write_corpus(directory: Path | None = None) -> Path:
    target = directory or (ROOT / "data")
    target.mkdir(parents=True, exist_ok=True)
    documents, labels = build_corpus()
    (target / "documents.json").write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def filler(seed: int, size: int = 110) -> list[str]:
    rng = random.Random(seed)
    return [rng.choice(LEXICON) for _ in range(size)]


def _edited(words: list[str], salt: str, slots: tuple[int, ...]) -> list[str]:
    copy = list(words)
    for slot in slots:
        copy[slot] = salt
    return copy


def _text(fact: str, words: list[str]) -> str:
    return fact + " " + " ".join(words)


def _doc(
    doc_id: str,
    title: str,
    text: str,
    url: str | None,
    published_at: str,
    story: int,
    source_name: str = "Издание",
    kind: str = "external_news",
) -> dict:
    return {
        "id": doc_id,
        "source": source_name,
        "title": title,
        "text": text,
        "published_at": published_at,
        "language": "ru",
        "url": url,
        "raw_meta": {"entities": [f"сюжет-{story}"], "tags": ["macro"]},
        "content_kind": kind,
    }


def main() -> None:
    path = write_corpus()
    documents, _labels = build_corpus()
    print(f"{len(documents)} documents -> {path}")


if __name__ == "__main__":
    main()
