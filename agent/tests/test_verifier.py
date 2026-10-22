from agent.news_model import NewsCluster
from agent.verifier import check_insight, numbers_of


def _draft() -> dict:
    return {
        "insight_id": "ins_1",
        "client_id": "C_LOAN",
        "cluster_id": "clu_rate",
        "generated_at": "2026-09-11T12:00:00+00:00",
        "headline": "ЦБ сохранил ключевую ставку 14%",
        "type": "risk",
        "urgency": "high",
        "impact": "high",
        "confidence": "medium",
        "priority_score": 0.2,
        "what_happened": {
            "facts": [
                {
                    "text": "11 сентября 2026 года Банк России сохранил ключевую ставку на уровне 14%.",
                    "source_ids": ["news:clu_rate"],
                    "status": "confirmed",
                }
            ],
            "assumptions": [],
        },
        "why_it_matters": [
            {
                "text": "У компании есть кредиты, поэтому пауза в ставке не снижает долговую нагрузку.",
                "client_features": ["rate_sensitivity"],
                "source_ids": ["profile:C_LOAN", "news:clu_rate"],
            },
            {
                "text": "Событие относится к оптовой торговле, в которой работает клиент.",
                "client_features": ["sector"],
                "source_ids": ["profile:C_LOAN"],
            },
        ],
        "consequences": {
            "risks": [
                {"text": "Стоимость долга не снижается, пока ставка на паузе.", "source_ids": ["news:clu_rate"], "origin": "ai"}
            ],
            "opportunities": [],
            "factors_to_watch": ["следующее решение по ставке"],
        },
        "actions": [
            {
                "text": "Сверить долю кредитов с плавающей ставкой и дату ближайшего пересмотра.",
                "rationale": "От этого зависит, сохранится ли нагрузка.",
                "origin": "ai",
                "product_hint": "loan",
            }
        ],
        "bank_view": [
            {
                "text": "Ключевая ставка сохранена на уровне 14%.",
                "doc_id": "policy_rate",
                "doc_title": "Ключевая ставка ЦБ РФ",
                "fragment_id": "policy_rate-0",
            }
        ],
        "sources": [
            {
                "source_id": "news:clu_rate",
                "kind": "news",
                "title": "ЦБ сохранил ключевую ставку 14%",
                "url": "https://example.test/rate",
                "published_at": "2026-09-11T12:00:00Z",
            },
            {
                "source_id": "profile:C_LOAN",
                "kind": "client_profile",
                "title": "Профиль C_LOAN",
                "url": None,
                "published_at": None,
            },
            {
                "source_id": "bank:policy_rate:policy_rate-0",
                "kind": "bank_analytics",
                "title": "Ключевая ставка ЦБ РФ",
                "url": None,
                "published_at": None,
            },
        ],
        "client_features_used": [
            {
                "feature_id": "rate_sensitivity",
                "label": "Чувствительность к ключевой ставке",
                "value": "кредит: да; лизинг: да; гарантии: да",
                "csv_field": "CL_PRODUCT_LOA_MARK,CL_PRODUCT_LEA_MARK,CL_PRODUCT_GAR_MARK",
            }
        ],
        "trace_id": "tr_1",
        "gaps": [],
    }


def _sources() -> dict[str, str]:
    return {
        "news:clu_rate": "ЦБ сохранил ключевую ставку 14%. 11 сентября 2026 года Банк России сохранил ключевую ставку на уровне 14%.",
        "profile:C_LOAN": "кредит: да; лизинг: да; гарантии: да",
        "bank:policy_rate:policy_rate-0": "Ключевая ставка сохранена на уровне 14%.",
    }


def _features() -> dict[str, str]:
    return {"rate_sensitivity": "кредит: да; лизинг: да; гарантии: да", "sector": "Оптовая торговля"}


def _fragments() -> list[dict]:
    return [
        {
            "fragment_id": "policy_rate-0",
            "doc_id": "policy_rate",
            "doc_title": "Ключевая ставка ЦБ РФ",
            "text": "Ключевая ставка сохранена на уровне 14%. Риторика стала жёстче.",
        }
    ]


def test_valid_draft_has_no_issues() -> None:
    assert check_insight(_draft(), allowed_sources=_sources(), feature_values=_features(), bank_fragments=_fragments()) == []


def test_rejects_unknown_source_invented_number_and_paraphrase() -> None:
    draft = _draft()
    draft["what_happened"]["facts"][0]["source_ids"] = ["news:missing"]
    draft["what_happened"]["facts"][0]["text"] += " Эффект оценивается в 99%."
    draft["bank_view"][0]["text"] = "Банк считает, что ставка ещё долго будет высокой."
    draft["why_it_matters"][1]["client_features"] = ["not_a_feature"]
    issues = check_insight(draft, allowed_sources=_sources(), feature_values=_features(), bank_fragments=_fragments())
    codes = {item["code"] for item in issues}
    assert {"source_id", "number", "bank_view", "feature"} <= codes


def test_invalid_json_shape_is_schema_error() -> None:
    issues = check_insight({"headline": "только заголовок"}, allowed_sources={}, feature_values={}, bank_fragments=[])
    assert issues[0]["code"] == "schema"


def test_numbers_normalize_comma_and_percent() -> None:
    assert numbers_of("ставка 14,0%") == {"14%"}


def test_cluster_period() -> None:
    cluster = NewsCluster("c", "t", "s", "2026-09-11T00:00:00Z")
    assert cluster.in_period("2026-09-01", "2026-09-30")
    assert not cluster.in_period("2026-10-01", "2026-10-02")
