from __future__ import annotations

import pytest

from agent.errors import ClientNotFound
from agent.news_model import NewsCluster
from agent.profile_features import build_profile
from agent.ranking import SensitivityRules, priority_score, score_relevance, select_top
from agent.schemas.models import parse_insight, schema_errors


@pytest.fixture
def rules() -> SensitivityRules:
    return SensitivityRules.load()


def cluster(cluster_id: str, title: str, summary: str = "", tags: list[str] | None = None) -> NewsCluster:
    return NewsCluster(
        cluster_id=cluster_id,
        title=title,
        summary=summary,
        published_at="2026-09-11T12:00:00Z",
        tags=tags or [],
    )


def test_loan_profile_features(directory: ClientDirectory) -> None:
    profile = build_profile(directory, "C_LOAN")
    features = profile.by_id()
    assert features["sector"].code == "wholesale"
    assert features["sector"].csv_field == "MAINOKVED_CODE"
    assert features["scale_revenue"].code == "large"
    assert features["rate_sensitivity"].active is True
    assert features["deposit_benefit"].active is False
    assert features["fx_sensitivity"].active is False
    assert features["labor_sensitivity"].active is True
    assert features["demand_sensitivity"].active is True
    assert "C_SUPPLIER" in features["top_counterparties"].value
    assert features["counterparty_concentration"].value.startswith("доля крупнейшего контрагента 100%")
    assert features["market_position"].code == "4/4"
    assert "не доля всего рынка" in features["sample_share"].value
    assert features["group"].code == "group_loan"
    assert "C_GROUPMATE" in features["related_via_persons"].value
    assert profile.graph.group_members[0].counterparty_id == "C_GROUPMATE"
    assert profile.graph.person_links[0].counterparty_id == "C_GROUPMATE"


def test_sparse_profile_does_not_fail(directory: ClientDirectory) -> None:
    profile = build_profile(directory, "C_SPARSE")
    features = profile.by_id()
    assert features["sector"].code == "unknown"
    assert features["sector"].active is False
    assert features["scale_revenue"].code == "unknown"
    assert features["top_counterparties"].value == "unknown"
    assert features["fx_sensitivity"].code == "no"
    assert features["market_position"].active is False
    assert features["sample_share"].value == "unknown"


def test_unknown_client(directory: ClientDirectory) -> None:
    with pytest.raises(ClientNotFound):
        build_profile(directory, "NOBODY")


def test_role_filter_hides_fields(directory: ClientDirectory) -> None:
    profile = build_profile(directory, "C_LOAN")
    treasurer_ids = {item.feature_id for item in profile.for_role("treasurer")}
    assert "rate_sensitivity" in treasurer_ids
    assert "labor_sensitivity" not in treasurer_ids
    assert "negative_mark" not in treasurer_ids
    cfo_ids = {item.feature_id for item in profile.for_role("cfo")}
    assert "labor_sensitivity" in cfo_ids
    assert "negative_mark" not in cfo_ids


def test_graph_depth(directory: ClientDirectory) -> None:
    near = directory.graph("C_LOAN", depth=1)
    far = directory.graph("C_LOAN", depth=2)
    near_ids = {item.counterparty_id for item in near.payments}
    far_ids = {item.counterparty_id for item in far.payments}
    assert near_ids == {"C_SUPPLIER"}
    assert "C_DEPOSIT" in far_ids


def test_rate_news_ranks_borrower_above_depositor(directory: ClientDirectory, rules: SensitivityRules) -> None:
    news = cluster("clu_rate", "ЦБ сохранил ключевую ставку", "Банк России сохранил ключевую ставку на уровне 14%.")
    loan = score_relevance(build_profile(directory, "C_LOAN"), news, rules)
    deposit = score_relevance(build_profile(directory, "C_DEPOSIT"), news, rules)
    assert loan.score > deposit.score
    assert any(reason.feature_id == "rate_sensitivity" for reason in loan.reasons)
    assert any(reason.feature_id == "deposit_benefit" for reason in deposit.reasons)


def test_fx_news_ranks_importer_above_domestic(directory: ClientDirectory, rules: SensitivityRules) -> None:
    news = cluster("clu_fx", "Курс юаня вырос", "Оптовые импортёры пересматривают контракты из-за курса юаня.")
    importer = score_relevance(build_profile(directory, "C_IMPORT"), news, rules)
    domestic = score_relevance(build_profile(directory, "C_DOMESTIC"), news, rules)
    assert importer.score > domestic.score
    assert any(reason.feature_id == "fx_sensitivity" for reason in importer.reasons)
    assert not any(reason.feature_id == "fx_sensitivity" for reason in domestic.reasons)


def test_supply_word_is_not_a_rate_hit(directory: ClientDirectory, rules: SensitivityRules) -> None:
    news = cluster("clu_supply", "Срыв поставки оборудования", "Поставка задержана на складе.")
    score = score_relevance(build_profile(directory, "C_LOAN"), news, rules)
    assert all(reason.feature_id != "rate_sensitivity" for reason in score.reasons)


def test_counterparty_sector_and_region(directory: ClientDirectory, rules: SensitivityRules) -> None:
    metals = cluster("clu_metal", "Цены на металл выросли", "Биржевой металл подорожал.")
    score = score_relevance(build_profile(directory, "C_LOAN"), metals, rules)
    assert any(reason.code == "sector_counterparty" for reason in score.reasons)

    local = cluster("clu_local", "Оптовый спрос в Иркутской области", "Оптовая торговля в Иркутске ожила.")
    local_score = score_relevance(build_profile(directory, "C_LOAN"), local, rules)
    assert any(reason.code == "sector_client" for reason in local_score.reasons)
    assert any(reason.code == "region" for reason in local_score.reasons)


def test_group_sector_and_role_shift(directory: ClientDirectory, rules: SensitivityRules) -> None:
    profile = build_profile(directory, "C_LOAN")
    construction = cluster("clu_build", "Строительство жилья замедлилось", "Девелоперы сокращают запуски.")
    score = score_relevance(profile, construction, rules)
    assert any(reason.code == "group_sector" and reason.feature_id == "group" for reason in score.reasons)
    rate = cluster("clu_rate", "ЦБ сохранил ключевую ставку", "Банк России сохранил ключевую ставку на уровне 14%.")
    base = score_relevance(profile, rate, rules)
    treasurer = score_relevance(profile, rate, rules, role="treasurer")
    assert treasurer.score > base.score
    labor = cluster("clu_labor", "Рынок труда остаётся напряжённым", "Зарплаты и спрос на персонал растут.")
    labor_base = score_relevance(profile, labor, rules)
    labor_treasury = score_relevance(profile, labor, rules, role="treasurer")
    assert labor_treasury.score < labor_base.score


def test_select_top_drops_noise(directory: ClientDirectory, rules: SensitivityRules) -> None:
    profile = build_profile(directory, "C_LOAN")
    rate = score_relevance(profile, cluster("clu_rate", "Ключевая ставка сохранена"), rules)
    sport = score_relevance(profile, cluster("clu_sport", "Футбольный клуб выиграл матч"), rules)
    chosen = select_top([sport, rate], top_n=10, min_relevance=rules.min_relevance)
    assert [item.cluster_id for item in chosen] == ["clu_rate"]


def test_priority_formula() -> None:
    assert priority_score(0.5, "high", "medium", "low") == round(0.5 * 1.0 * 0.6 * 0.3, 4)


def test_fuel_prefix_beats_wholesale() -> None:
    from agent.clients import OkvedCatalog

    catalog = OkvedCatalog.load()
    assert catalog.resolve("46.71.2")[0] == "fuel"
    assert catalog.resolve("46.90")[0] == "wholesale"
    assert catalog.resolve("")[0] == "unknown"


def test_insight_schema_roundtrip() -> None:
    payload = {
        "insight_id": "ins_1",
        "client_id": "C_LOAN",
        "cluster_id": "clu_rate",
        "generated_at": "2026-09-11T12:00:00Z",
        "headline": "Ставка 14% сохраняет стоимость кредитов",
        "type": "risk",
        "urgency": "high",
        "impact": "high",
        "confidence": "medium",
        "priority_score": 0.25,
        "what_happened": {
            "facts": [
                {"text": "Банк России сохранил ключевую ставку на уровне 14%.", "source_ids": ["news:clu_rate"], "status": "confirmed"}
            ],
            "assumptions": [],
        },
        "why_it_matters": [
            {
                "text": "У компании подключены кредитные продукты, поэтому пауза в снижении ставки держит стоимость долга.",
                "client_features": ["rate_sensitivity", "sector"],
                "source_ids": ["profile:C_LOAN", "news:clu_rate"],
            }
        ],
        "consequences": {
            "risks": [
                {
                    "text": "Финансовая нагрузка по плавающей ставке не снизится.",
                    "source_ids": ["news:clu_rate"],
                    "origin": "ai",
                }
            ],
            "opportunities": [],
            "factors_to_watch": ["следующее решение по ставке"],
        },
        "actions": [
            {
                "text": "Сверить долю кредитов с плавающей ставкой и дату ближайшего пересмотра.",
                "rationale": "От этого зависит, сохранится ли текущая нагрузка.",
                "origin": "ai",
                "product_hint": "loan",
            }
        ],
        "bank_view": [],
        "sources": [
            {"source_id": "news:clu_rate", "kind": "news", "title": "Ставка", "url": None, "published_at": "2026-09-11T12:00:00Z"},
            {"source_id": "profile:C_LOAN", "kind": "client_profile", "title": "Профиль", "url": None, "published_at": None},
        ],
        "client_features_used": [
            {
                "feature_id": "rate_sensitivity",
                "label": "Чувствительность к ключевой ставке",
                "value": "кредит: да",
                "csv_field": "CL_PRODUCT_LOA_MARK,CL_PRODUCT_LEA_MARK,CL_PRODUCT_GAR_MARK",
            }
        ],
        "trace_id": "tr_1",
        "gaps": [],
    }
    assert schema_errors(payload, "insight.schema.json") == []
    parsed = parse_insight(payload)
    assert parsed.client_id == "C_LOAN"
    payload["type"] = "banner"
    assert schema_errors(payload, "insight.schema.json")
