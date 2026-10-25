"""Попарные precision/recall на размеченном наборе."""

from __future__ import annotations

import json
from pathlib import Path

from dedup.core import Deduplicator

ROOT = Path(__file__).resolve().parents[2]


def pairwise(groups: dict[str, list[str]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for members in groups.values():
        ordered = sorted(members)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                pairs.add((left, right))
    return pairs


def score(documents: list[dict], labels: dict[str, dict], db_path: Path) -> dict:
    if db_path.exists():
        db_path.unlink()
    engine = Deduplicator(db_path, tenant="eval")
    try:
        engine.batch(documents)
        predicted: dict[str, list[str]] = {}
        for cluster in engine.clusters():
            members = [cluster.canonical_id, *[item.id for item in cluster.duplicates]]
            predicted[cluster.cluster_id] = members
    finally:
        engine.close()

    gold: dict[str, list[str]] = {}
    for doc_id, meta in labels.items():
        gold.setdefault(meta["gold_cluster"], []).append(doc_id)
    gold_pairs = pairwise(gold)
    predicted_pairs = pairwise(predicted)
    true_positive = gold_pairs & predicted_pairs
    false_positive = predicted_pairs - gold_pairs
    false_negative = gold_pairs - predicted_pairs
    precision = len(true_positive) / len(predicted_pairs) if predicted_pairs else 1.0
    recall = len(true_positive) / len(gold_pairs) if gold_pairs else 1.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    def kind(pair: tuple[str, str]) -> str:
        relations = {labels[pair[0]]["relation"], labels[pair[1]]["relation"]}
        if "near" in relations:
            return "near"
        return "exact"

    exact_gold = {pair for pair in gold_pairs if kind(pair) == "exact"}
    near_gold = {pair for pair in gold_pairs if kind(pair) == "near"}
    sizes: dict[str, int] = {}
    for members in predicted.values():
        sizes[str(len(members))] = sizes.get(str(len(members)), 0) + 1
    hidden = sum(len(members) - 1 for members in predicted.values())
    return {
        "documents": len(documents),
        "clusters": len(predicted),
        "true_positive": len(true_positive),
        "false_positive": len(false_positive),
        "false_negative": len(false_negative),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "exact_pairs": len(exact_gold),
        "exact_recalled": len(exact_gold & predicted_pairs),
        "near_pairs": len(near_gold),
        "near_recalled": len(near_gold & predicted_pairs),
        "hidden_share": round(hidden / len(documents), 4) if documents else 0.0,
        "cluster_sizes": sizes,
        "false_positive_examples": sorted(f"{left} + {right}" for left, right in list(false_positive)[:10]),
        "false_negative_examples": sorted(f"{left} + {right}" for left, right in list(false_negative)[:10]),
    }


def write_report(metrics: dict, directory: Path | None = None) -> Path:
    target = directory or (ROOT / "reports")
    target.mkdir(parents=True, exist_ok=True)
    (target / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Качество дедупликации",
        "",
        f"Документов: {metrics['documents']}. Кластеров: {metrics['clusters']}.",
        f"Precision {metrics['precision']}, recall {metrics['recall']}, F1 {metrics['f1']}.",
        f"Точные пары: {metrics['exact_recalled']} из {metrics['exact_pairs']}.",
        f"Near-пары: {metrics['near_recalled']} из {metrics['near_pairs']}.",
        f"Доля документов, которые не стали каноническими: {metrics['hidden_share']}.",
        f"Размеры кластеров: {metrics['cluster_sizes']}.",
        "",
        "Ложноположительные: " + (", ".join(metrics["false_positive_examples"]) or "нет."),
        "Ложноотрицательные: " + (", ".join(metrics["false_negative_examples"]) or "нет."),
        "",
        "Пороги стартовые: Jaccard 0,72 и косинус заголовка 0,65, окно 7 суток, complete-link.",
        "Набор синтетический. На живой ленте пороги нужно калибровать отдельно.",
    ]
    (target / "quality.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> None:
    data = ROOT / "data"
    documents = json.loads((data / "documents.json").read_text(encoding="utf-8"))
    labels = json.loads((data / "labels.json").read_text(encoding="utf-8"))
    metrics = score(documents, labels, ROOT / "reports" / "eval.sqlite")
    write_report(metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
