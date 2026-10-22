"""Детерминированные признаки профиля. Без LLM."""

from __future__ import annotations

from dataclasses import dataclass

from agent.clients import ClientDirectory, ClientRecord, GraphView

HEADCOUNT_LARGE = {"101-250", "более 251", "251+", "более 250"}
ROLE_TREASURER = {
    "sector",
    "okved_name",
    "region",
    "city",
    "scale_revenue",
    "rate_sensitivity",
    "fx_sensitivity",
    "deposit_benefit",
    "factoring_sensitivity",
    "top_counterparties",
    "counterparty_concentration",
    "group",
    "commodity_exposure",
    "rko",
    "guarantee_product",
}
ROLE_CFO_HIDDEN = {"negative_mark"}


def flag(value: str) -> bool | None:
    text = (value or "").strip().lower()
    if text in {"", "nan", "none"}:
        return None
    if text in {"1", "1.0", "true", "yes", "да"}:
        return True
    if text in {"0", "0.0", "false", "no", "нет"}:
        return False
    return None


def _yes_no(value: bool | None) -> str:
    if value is True:
        return "да"
    if value is False:
        return "нет"
    return "unknown"


@dataclass(frozen=True)
class Feature:
    feature_id: str
    label: str
    value: str
    csv_field: str
    active: bool = False
    code: str = ""

    def public(self) -> dict[str, str]:
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "value": self.value,
            "csv_field": self.csv_field,
        }


@dataclass
class Profile:
    client_id: str
    features: list[Feature]
    record: ClientRecord
    graph: GraphView

    def by_id(self) -> dict[str, Feature]:
        return {item.feature_id: item for item in self.features}

    def active(self, feature_id: str) -> bool:
        feature = self.by_id().get(feature_id)
        return bool(feature and feature.active)

    def for_role(self, role: str | None) -> list[Feature]:
        normalized = (role or "ceo").lower()
        if normalized == "treasurer":
            return [item for item in self.features if item.feature_id in ROLE_TREASURER]
        if normalized == "cfo":
            return [item for item in self.features if item.feature_id not in ROLE_CFO_HIDDEN]
        return list(self.features)

    def source_text(self, role: str | None = None) -> str:
        lines = [f"{item.label}: {item.value}" for item in self.for_role(role)]
        return "\n".join(lines)


def revenue_band(revenue: float | None) -> tuple[str, str, bool]:
    if revenue is None:
        return "unknown", "выручка не указана", False
    if revenue >= 15_000_000_000:
        return "large", "крупный бизнес, выручка от 15 млрд руб.", True
    if revenue >= 1_000_000_000:
        return "upper_mid", "выручка от 1 млрд руб.", True
    if revenue >= 100_000_000:
        return "mid", "выручка от 100 млн руб.", True
    return "small", "выручка ниже 100 млн руб.", True


def build_features(record: ClientRecord, directory: ClientDirectory, graph: GraphView) -> list[Feature]:
    catalog = directory.okved
    sector_id, sector_label = catalog.resolve(record.okved_code)
    features: list[Feature] = []

    features.append(
        Feature(
            feature_id="sector",
            label="Отрасль",
            value=sector_label if sector_id != "unknown" else "не определена",
            csv_field="MAINOKVED_CODE",
            active=sector_id != "unknown",
            code=sector_id,
        )
    )
    features.append(
        Feature(
            feature_id="okved_name",
            label="Вид деятельности",
            value=record.okved_name or "не указан",
            csv_field="MAINOKVED_NAME",
            active=bool(record.okved_name),
            code=record.okved_code,
        )
    )

    scale_code, scale_label, scale_active = revenue_band(record.revenue)
    revenue_text = scale_label if record.revenue is None else f"{record.revenue_raw} руб., {scale_label}"
    features.append(
        Feature(
            feature_id="scale_revenue",
            label="Масштаб по выручке",
            value=revenue_text,
            csv_field="REVENUE_COMPANY",
            active=scale_active,
            code=scale_code,
        )
    )

    workers = record.workers_fns or record.workers_spark
    features.append(
        Feature(
            feature_id="scale_headcount",
            label="Численность",
            value=workers or "unknown",
            csv_field="AMOUNT_WORKERS_FNS_GRADATION",
            active=bool(workers),
            code=workers.lower() if workers else "unknown",
        )
    )

    loan = flag(record.loan)
    leasing = flag(record.leasing)
    guarantee = flag(record.guarantee)
    rate_active = any(item is True for item in (loan, leasing, guarantee))
    features.append(
        Feature(
            feature_id="rate_sensitivity",
            label="Чувствительность к ключевой ставке",
            value=f"кредит: {_yes_no(loan)}; лизинг: {_yes_no(leasing)}; гарантии: {_yes_no(guarantee)}",
            csv_field="CL_PRODUCT_LOA_MARK,CL_PRODUCT_LEA_MARK,CL_PRODUCT_GAR_MARK",
            active=rate_active,
            code="yes" if rate_active else "no" if all(item is False for item in (loan, leasing, guarantee)) else "unknown",
        )
    )

    fts = flag(record.fts_flag)
    country = record.fts_country or "страна не указана"
    features.append(
        Feature(
            feature_id="fx_sensitivity",
            label="Внешнеэкономическая деятельность",
            value=("нет" if fts is False else f"да, страна: {country}" if fts is True else "unknown"),
            csv_field="FTS_FLAG,FTS_COUNTRY_NAME",
            active=fts is True,
            code="yes" if fts is True else "no" if fts is False else "unknown",
        )
    )

    deposit = flag(record.deposit)
    features.append(
        Feature(
            feature_id="deposit_benefit",
            label="Депозиты",
            value=_yes_no(deposit),
            csv_field="CL_PRODUCT_DEP_MARK",
            active=deposit is True,
            code="yes" if deposit is True else "no" if deposit is False else "unknown",
        )
    )

    payroll = flag(record.payroll)
    large_staff = (workers or "").lower() in HEADCOUNT_LARGE
    labor_active = payroll is True or large_staff
    features.append(
        Feature(
            feature_id="labor_sensitivity",
            label="Чувствительность к рынку труда",
            value=f"зарплатный проект: {_yes_no(payroll)}; численность: {workers or 'unknown'}",
            csv_field="CL_PRODUCT_ZPP_MARK,AMOUNT_WORKERS_FNS_GRADATION",
            active=labor_active,
            code="yes" if labor_active else "no",
        )
    )

    factoring = flag(record.factoring)
    features.append(
        Feature(
            feature_id="factoring_sensitivity",
            label="Факторинг",
            value=_yes_no(factoring),
            csv_field="FACTORING_MARK",
            active=factoring is True,
            code="yes" if factoring is True else "no" if factoring is False else "unknown",
        )
    )

    features.append(
        Feature(
            feature_id="region",
            label="Регион",
            value=record.region or "unknown",
            csv_field="REGION",
            active=bool(record.region),
            code=record.region.lower(),
        )
    )
    features.append(
        Feature(
            feature_id="city",
            label="Город",
            value=record.city or "unknown",
            csv_field="CITY",
            active=bool(record.city),
            code=record.city.lower(),
        )
    )
    features.append(
        Feature(
            feature_id="group",
            label="Группа компаний",
            value=record.group_id or "unknown",
            csv_field="GROUP_ID",
            active=bool(record.group_id),
            code=record.group_id,
        )
    )

    features.append(_counterparties_feature(graph))
    features.append(_concentration_feature(graph))

    features.append(
        Feature(
            feature_id="commodity_exposure",
            label="Зависимость от сырья",
            value="да" if sector_id in catalog.commodity_sectors else "нет",
            csv_field="MAINOKVED_CODE",
            active=sector_id in catalog.commodity_sectors,
            code="yes" if sector_id in catalog.commodity_sectors else "no",
        )
    )
    features.append(
        Feature(
            feature_id="demand_sensitivity",
            label="Зависимость от спроса",
            value="да" if sector_id in catalog.demand_sectors else "нет",
            csv_field="MAINOKVED_CODE",
            active=sector_id in catalog.demand_sectors,
            code="yes" if sector_id in catalog.demand_sectors else "no",
        )
    )
    features.append(
        Feature(
            feature_id="rko",
            label="Расчётно-кассовое обслуживание",
            value=_yes_no(flag(record.rko)),
            csv_field="CL_PRODUCT_RKO_MARK",
            active=flag(record.rko) is True,
            code="yes" if flag(record.rko) is True else "no",
        )
    )
    features.append(
        Feature(
            feature_id="age",
            label="Возраст компании",
            value=record.age or "unknown",
            csv_field="COMPANY_AGE_YEARS_GRADATION",
            active=bool(record.age),
            code=record.age,
        )
    )
    features.append(
        Feature(
            feature_id="eco_stage",
            label="Стадия развития",
            value=record.eco_stage or "unknown",
            csv_field="ECO_MARK_LABEL",
            active=bool(record.eco_stage),
            code=record.eco_stage,
        )
    )
    features.append(
        Feature(
            feature_id="ownership",
            label="Форма собственности",
            value=record.ownership or "unknown",
            csv_field="OKFS_NAME",
            active=bool(record.ownership),
            code=record.ownership,
        )
    )
    features.append(
        Feature(
            feature_id="negative_mark",
            label="Негативная отметка",
            value=_yes_no(flag(record.negative_mark)),
            csv_field="NEGATIVEMARK",
            active=flag(record.negative_mark) is True,
            code="yes" if flag(record.negative_mark) is True else "no",
        )
    )
    features.append(
        Feature(
            feature_id="client_type",
            label="Тип клиента",
            value=record.client_type or "unknown",
            csv_field="CL_TYPE",
            active=bool(record.client_type),
            code=record.client_type,
        )
    )
    return features


def _counterparties_feature(graph: GraphView) -> Feature:
    if not graph.payments:
        return Feature(
            feature_id="top_counterparties",
            label="Крупнейшие контрагенты",
            value="unknown",
            csv_field="BENEFICIARY,PAYER",
            active=False,
            code="unknown",
        )
    parts = []
    for item in graph.payments[:5]:
        if item.relation != "payment":
            continue
        sector = item.sector_label or "отрасль неизвестна"
        region = item.region or "регион неизвестен"
        share = "доля неизвестна" if item.share is None else f"доля {item.share:.0%}"
        parts.append(f"{item.counterparty_id} ({sector}, {region}, {share})")
    return Feature(
        feature_id="top_counterparties",
        label="Крупнейшие контрагенты",
        value="; ".join(parts) if parts else "unknown",
        csv_field="BENEFICIARY,PAYER",
        active=bool(parts),
        code="present" if parts else "unknown",
    )


def _concentration_feature(graph: GraphView) -> Feature:
    share = graph.top_share
    if share is None:
        value = "unknown"
        active = False
        code = "unknown"
    else:
        value = f"доля крупнейшего контрагента {share:.0%}"
        active = True
        code = f"{share:.4f}"
    return Feature(
        feature_id="counterparty_concentration",
        label="Концентрация контрагентов",
        value=value,
        csv_field="PAY_AMT",
        active=active,
        code=code,
    )


def build_profile(directory: ClientDirectory, client_id: str) -> Profile:
    record = directory.require(client_id)
    graph = directory.graph(client_id, depth=1)
    return Profile(
        client_id=client_id,
        features=build_features(record, directory, graph),
        record=record,
        graph=graph,
    )
