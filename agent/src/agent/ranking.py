"""Предотбор кластеров без LLM и итоговый priority_score."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agent.news_model import NewsCluster
from agent.paths import CONFIG_DIR, load_yaml
from agent.profile_features import Profile

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_REGION_NOISE = ("область", "край", "республика", "округ", "автономный", "город", "г")

LEVEL_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def tokens(text: str) -> set[str]:
    return {_norm(token) for token in _TOKEN.findall(text or "")}


def keyword_hit(text: str, token_set: set[str], keywords: list[str]) -> bool:
    folded = _norm(text)
    for keyword in keywords:
        probe = _norm(keyword).strip()
        if not probe:
            continue
        if " " in probe:
            if probe in folded:
                return True
        elif len(probe) <= 3:
            if probe in token_set:
                return True
        elif any(token.startswith(probe) for token in token_set):
            return True
    return False


@dataclass
class SensitivityRules:
    sector_client: float
    sector_counterparty: float
    region: float
    sensitivity_cap: float
    min_relevance: float
    counterparty_limit: int
    factors: list[dict]
    sector_keywords: dict[str, list[str]]

    @classmethod
    def load(cls, path: Path | None = None) -> "SensitivityRules":
        data = load_yaml(path or CONFIG_DIR / "sensitivity_rules.yaml")
        weights = data.get("weights", {})
        return cls(
            sector_client=float(weights.get("sector_client", 0.35)),
            sector_counterparty=float(weights.get("sector_counterparty", 0.15)),
            region=float(weights.get("region", 0.15)),
            sensitivity_cap=float(data.get("sensitivity_cap", 0.35)),
            min_relevance=float(data.get("min_relevance", 0.05)),
            counterparty_limit=int(data.get("counterparty_limit", 5)),
            factors=list(data.get("factors", {}).values()) if isinstance(data.get("factors"), dict) else list(data.get("factors") or []),
            sector_keywords={str(key): list(value) for key, value in (data.get("sector_keywords") or {}).items()},
        )


@dataclass
class Reason:
    code: str
    feature_id: str | None
    weight: float
    detail: str


@dataclass
class RelevanceScore:
    client_id: str
    cluster_id: str
    score: float
    reasons: list[Reason] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "cluster_id": self.cluster_id,
            "score": self.score,
            "reasons": [reason.__dict__ for reason in self.reasons],
        }


def _sector_match(sector_id: str, rules: SensitivityRules, text: str, token_set: set[str], tags: set[str]) -> bool:
    if not sector_id or sector_id == "unknown":
        return False
    if sector_id in tags or f"sector:{sector_id}" in tags:
        return True
    keywords = rules.sector_keywords.get(sector_id, [])
    return keyword_hit(text, token_set, keywords)


def _region_match(profile: Profile, text: str, token_set: set[str]) -> bool:
    record = profile.record
    probes: list[str] = []
    for raw in (record.region, record.city):
        for token in tokens(raw):
            if token in _REGION_NOISE or len(token) < 5:
                continue
            probes.append(token)
            if token.endswith("ская"):
                probes.append(token[:-4])
            elif token.endswith("ский"):
                probes.append(token[:-4])
    return any(probe and (probe in token_set or any(token.startswith(probe) for token in token_set)) for probe in probes)


def score_relevance(profile: Profile, cluster: NewsCluster, rules: SensitivityRules) -> RelevanceScore:
    text = cluster.searchable_text()
    token_set = tokens(text)
    tags = {_norm(tag) for tag in cluster.tags}
    reasons: list[Reason] = []
    sector = profile.by_id().get("sector")
    sector_id = sector.code if sector else "unknown"

    if _sector_match(sector_id, rules, text, token_set, tags):
        reasons.append(
            Reason("sector_client", "sector", rules.sector_client, f"событие совпадает с отраслью «{sector.value if sector else ''}»")
        )

    matched_counterparty = None
    for item in profile.graph.payments[: rules.counterparty_limit]:
        if item.relation != "payment" or not item.sector_id:
            continue
        if _sector_match(item.sector_id, rules, text, token_set, tags):
            matched_counterparty = item
            break
    if matched_counterparty is not None:
        reasons.append(
            Reason(
                "sector_counterparty",
                "top_counterparties",
                rules.sector_counterparty,
                f"событие близко к контрагенту {matched_counterparty.counterparty_id} ({matched_counterparty.sector_label})",
            )
        )

    if profile.active("region") and _region_match(profile, text, token_set):
        reasons.append(Reason("region", "region", rules.region, f"в тексте есть регион или город клиента ({profile.record.region})"))

    sensitivity = 0.0
    for factor in rules.factors:
        factor_tags = {_norm(tag) for tag in factor.get("tags") or []}
        keywords = list(factor.get("keywords") or [])
        if not (tags & factor_tags or keyword_hit(text, token_set, keywords)):
            continue
        for feature_id, weight in (factor.get("effects") or {}).items():
            if not profile.active(feature_id):
                continue
            sensitivity += float(weight)
            reasons.append(
                Reason(
                    "sensitivity",
                    feature_id,
                    float(weight),
                    f"фактор пересекается с признаком {feature_id}",
                )
            )
    if sensitivity > rules.sensitivity_cap:
        overflow = sensitivity - rules.sensitivity_cap
        sensitivity = rules.sensitivity_cap
        reasons.append(Reason("sensitivity_cap", None, -overflow, "вклад чувствительности ограничен потолком"))

    raw = sum(reason.weight for reason in reasons)
    score = max(0.0, min(1.0, raw))
    return RelevanceScore(profile.client_id, cluster.cluster_id, round(score, 4), reasons)


def select_top(
    scores: list[RelevanceScore],
    top_n: int,
    min_relevance: float,
) -> list[RelevanceScore]:
    passed = [item for item in scores if item.score >= min_relevance]
    passed.sort(key=lambda item: item.score, reverse=True)
    return passed[:top_n]


def priority_score(relevance: float, impact: str, urgency: str, confidence: str) -> float:
    """relevance * w(impact) * w(urgency) * w(confidence), веса high/medium/low = 1.0/0.6/0.3."""
    value = float(relevance) * LEVEL_WEIGHT[impact] * LEVEL_WEIGHT[urgency] * LEVEL_WEIGHT[confidence]
    return round(max(0.0, min(1.0, value)), 4)
