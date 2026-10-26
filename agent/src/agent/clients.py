"""Загрузка обезличенных CSV и графа связей клиента."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from agent.errors import ClientNotFound
from agent.paths import CONFIG_DIR, load_yaml


def _clean(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


def _money(value: str) -> float | None:
    text = value.replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    org_type: str
    ownership: str
    age: str
    eco_stage: str
    okved_code: str
    okved_name: str
    negative_mark: str
    workers_fns: str
    workers_spark: str
    client_type: str
    rko: str
    guarantee: str
    loan: str
    leasing: str
    deposit: str
    payroll: str
    fts_flag: str
    fts_country: str
    factoring: str
    revenue_raw: str
    revenue: float | None
    group_id: str
    city: str
    region: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "ClientRecord":
        revenue_raw = _clean(row.get("REVENUE_COMPANY"))
        return cls(
            client_id=_clean(row.get("SYNTHETIC_U")),
            org_type=_clean(row.get("ORG_TYPE")),
            ownership=_clean(row.get("OKFS_NAME")),
            age=_clean(row.get("COMPANY_AGE_YEARS_GRADATION")),
            eco_stage=_clean(row.get("ECO_MARK_LABEL")),
            okved_code=_clean(row.get("MAINOKVED_CODE")),
            okved_name=_clean(row.get("MAINOKVED_NAME")),
            negative_mark=_clean(row.get("NEGATIVEMARK")),
            workers_fns=_clean(row.get("AMOUNT_WORKERS_FNS_GRADATION")),
            workers_spark=_clean(row.get("AMOUNT_WORKERS_SPARK_GRADATION")),
            client_type=_clean(row.get("CL_TYPE")),
            rko=_clean(row.get("CL_PRODUCT_RKO_MARK")),
            guarantee=_clean(row.get("CL_PRODUCT_GAR_MARK")),
            loan=_clean(row.get("CL_PRODUCT_LOA_MARK")),
            leasing=_clean(row.get("CL_PRODUCT_LEA_MARK")),
            deposit=_clean(row.get("CL_PRODUCT_DEP_MARK")),
            payroll=_clean(row.get("CL_PRODUCT_ZPP_MARK")),
            fts_flag=_clean(row.get("FTS_FLAG")),
            fts_country=_clean(row.get("FTS_COUNTRY_NAME")),
            factoring=_clean(row.get("FACTORING_MARK")),
            revenue_raw=revenue_raw,
            revenue=_money(revenue_raw),
            group_id=_clean(row.get("GROUP_ID")),
            city=_clean(row.get("CITY")),
            region=_clean(row.get("REGION")),
        )


@dataclass
class Counterparty:
    counterparty_id: str
    relation: str
    direction: str
    amount: float
    count: int
    share: float | None
    okved_code: str | None
    okved_name: str | None
    sector_id: str | None
    sector_label: str | None
    region: str | None
    city: str | None


@dataclass
class GraphView:
    client_id: str
    payments: list[Counterparty] = field(default_factory=list)
    group_members: list[Counterparty] = field(default_factory=list)
    person_links: list[Counterparty] = field(default_factory=list)

    @property
    def top_share(self) -> float | None:
        if not self.payments:
            return None
        shares = [item.share for item in self.payments if item.share is not None]
        return max(shares) if shares else None


@dataclass
class OkvedCatalog:
    pairs: list[tuple[str, str, str]]
    commodity_sectors: set[str]
    demand_sectors: set[str]

    @classmethod
    def load(cls, path: Path | None = None) -> "OkvedCatalog":
        data = load_yaml(path or CONFIG_DIR / "okved_sectors.yaml")
        pairs: list[tuple[str, str, str]] = []
        for sector in data.get("sectors", []):
            for prefix in sector.get("prefixes", []):
                pairs.append((str(prefix), str(sector["id"]), str(sector["label"])))
        pairs.sort(key=lambda item: len(item[0]), reverse=True)
        return cls(
            pairs=pairs,
            commodity_sectors=set(data.get("commodity_sectors", [])),
            demand_sectors=set(data.get("demand_sectors", [])),
        )

    def resolve(self, code: str) -> tuple[str, str]:
        normalized = (code or "").strip()
        if not normalized:
            return "unknown", "Отрасль не определена"
        for prefix, sector_id, label in self.pairs:
            if normalized == prefix or normalized.startswith(prefix + "."):
                return sector_id, label
        return "unknown", "Отрасль не определена"


def _read_table(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    frame = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False)
    return frame.to_dict(orient="records")


class ClientDirectory:
    """Индексы юрлиц, платежей и связей через физлица. Только чтение."""

    def __init__(
        self,
        clients_csv: Path,
        payments_csv: Path,
        persons_csv: Path,
        okved: OkvedCatalog | None = None,
    ) -> None:
        self.okved = okved or OkvedCatalog.load()
        self.clients: dict[str, ClientRecord] = {}
        self.by_group: dict[str, list[str]] = {}
        self.payment_edges: dict[str, list[tuple[str, str, float, int]]] = {}
        self.persons_of: dict[str, list[str]] = {}
        self.companies_of_person: dict[str, list[str]] = {}
        self._load_clients(clients_csv)
        self._load_payments(payments_csv)
        self._load_persons(persons_csv)
        self._index_market()

    def _load_clients(self, path: Path) -> None:
        for row in _read_table(path):
            record = ClientRecord.from_row(row)
            if not record.client_id:
                continue
            self.clients[record.client_id] = record
            if record.group_id:
                self.by_group.setdefault(record.group_id, []).append(record.client_id)

    def _load_payments(self, path: Path) -> None:
        for row in _read_table(path):
            beneficiary = _clean(row.get("BENEFICIARY"))
            payer = _clean(row.get("PAYER"))
            amount = _money(_clean(row.get("PAY_AMT"))) or 0.0
            count_raw = _money(_clean(row.get("PAY_CNT"))) or 0.0
            count = int(count_raw)
            if not beneficiary or not payer:
                continue
            self.payment_edges.setdefault(beneficiary, []).append(("in", payer, amount, count))
            self.payment_edges.setdefault(payer, []).append(("out", beneficiary, amount, count))

    def _load_persons(self, path: Path) -> None:
        for row in _read_table(path):
            person = _clean(row.get("SYNTHETIC_F"))
            company = _clean(row.get("SYNTHETIC_U"))
            if not person or not company:
                continue
            self.persons_of.setdefault(company, []).append(person)
            self.companies_of_person.setdefault(person, []).append(company)

    def _index_market(self) -> None:
        buckets: dict[str, list[tuple[str, float]]] = {}
        labels: dict[str, str] = {}
        for record in self.clients.values():
            sector_id, label = self.okved.resolve(record.okved_code)
            if sector_id == "unknown" or record.revenue is None:
                continue
            buckets.setdefault(sector_id, []).append((record.client_id, record.revenue))
            labels[sector_id] = label
        for items in buckets.values():
            items.sort(key=lambda item: (-item[1], item[0]))
        self.sector_revenue = buckets
        self.sector_labels = labels

    def market_snapshot(self, client_id: str) -> dict:
        """Место и доля внутри выборки клиентов банка. Объёма всего рынка в данных нет."""
        record = self.require(client_id)
        sector_id, label = self.okved.resolve(record.okved_code)
        peers = self.sector_revenue.get(sector_id, []) if sector_id != "unknown" else []
        rank = None
        share = None
        if record.revenue is not None and peers:
            total = sum(amount for _, amount in peers)
            share = record.revenue / total if total else None
            for index, (peer_id, _) in enumerate(peers, start=1):
                if peer_id == client_id:
                    rank = index
                    break
        return {
            "sector_id": sector_id,
            "sector_label": label if sector_id != "unknown" else "",
            "rank": rank,
            "peers": len(peers),
            "share": share,
            "group": self._group_snapshot(record),
        }

    def _group_snapshot(self, record: ClientRecord) -> dict:
        empty = {"id": "", "members": 0, "revenue": None, "sectors": [], "sector_share": None}
        if not record.group_id:
            return empty
        members = [self.clients[item] for item in self.by_group.get(record.group_id, []) if item in self.clients]
        revenues = [item.revenue for item in members if item.revenue is not None]
        sectors: list[str] = []
        sector_revenue = 0.0
        client_sector, _ = self.okved.resolve(record.okved_code)
        for item in members:
            sector_id, label = self.okved.resolve(item.okved_code)
            if sector_id != "unknown" and label not in sectors:
                sectors.append(label)
            if sector_id == client_sector and item.revenue is not None:
                sector_revenue += item.revenue
        peers = self.sector_revenue.get(client_sector, []) if client_sector != "unknown" else []
        total = sum(amount for _, amount in peers)
        return {
            "id": record.group_id,
            "members": len(members),
            "revenue": sum(revenues) if revenues else None,
            "sectors": sectors,
            "sector_share": sector_revenue / total if total and sector_revenue else None,
        }

    def get(self, client_id: str) -> ClientRecord | None:
        return self.clients.get(client_id)

    def require(self, client_id: str) -> ClientRecord:
        record = self.get(client_id)
        if record is None:
            raise ClientNotFound(client_id)
        return record

    def graph(self, client_id: str, depth: int = 1, limit: int = 20) -> GraphView:
        self.require(client_id)
        depth = max(1, min(int(depth), 2))
        view = GraphView(client_id=client_id)
        view.payments = self._payment_counterparties(client_id, limit)
        if depth >= 2:
            view.payments.extend(self._second_hop(client_id, view.payments))
        view.group_members = self._group_members(client_id, limit)
        view.person_links = self._person_links(client_id, limit)
        return view

    def _describe(self, counterparty_id: str) -> tuple[str | None, str | None, str | None, str | None, str | None]:
        record = self.clients.get(counterparty_id)
        if record is None:
            return None, None, None, None, None
        sector_id, sector_label = self.okved.resolve(record.okved_code)
        if sector_id == "unknown":
            sector_id, sector_label = None, None
        return record.okved_code or None, record.okved_name or None, sector_id, sector_label, record.region or None

    def _payment_counterparties(self, client_id: str, limit: int) -> list[Counterparty]:
        totals: dict[str, dict[str, float]] = {}
        directions: dict[str, set[str]] = {}
        for direction, other, amount, count in self.payment_edges.get(client_id, []):
            bucket = totals.setdefault(other, {"amount": 0.0, "count": 0.0})
            bucket["amount"] += amount
            bucket["count"] += count
            directions.setdefault(other, set()).add(direction)
        total_amount = sum(item["amount"] for item in totals.values())
        ranked = sorted(totals.items(), key=lambda item: item[1]["amount"], reverse=True)[:limit]
        result: list[Counterparty] = []
        for other, bucket in ranked:
            dirs = directions.get(other, set())
            if dirs == {"in", "out"}:
                direction = "both"
            else:
                direction = next(iter(dirs), "both")
            share = bucket["amount"] / total_amount if total_amount else None
            okved_code, okved_name, sector_id, sector_label, region = self._describe(other)
            record = self.clients.get(other)
            result.append(
                Counterparty(
                    counterparty_id=other,
                    relation="payment",
                    direction=direction,
                    amount=bucket["amount"],
                    count=int(bucket["count"]),
                    share=share,
                    okved_code=okved_code,
                    okved_name=okved_name,
                    sector_id=sector_id,
                    sector_label=sector_label,
                    region=region,
                    city=record.city if record else None,
                )
            )
        return result

    def _second_hop(self, client_id: str, direct: list[Counterparty]) -> list[Counterparty]:
        seen = {client_id} | {item.counterparty_id for item in direct}
        extra: list[Counterparty] = []
        for item in direct[:5]:
            if item.relation != "payment":
                continue
            for hop in self._payment_counterparties(item.counterparty_id, limit=3):
                if hop.counterparty_id in seen:
                    continue
                seen.add(hop.counterparty_id)
                hop.relation = "payment_depth2"
                hop.share = None
                extra.append(hop)
                if len(extra) >= 10:
                    return extra
        return extra

    def _group_members(self, client_id: str, limit: int) -> list[Counterparty]:
        record = self.require(client_id)
        if not record.group_id:
            return []
        members = []
        for other_id in self.by_group.get(record.group_id, []):
            if other_id == client_id:
                continue
            other = self.clients.get(other_id)
            if other is None:
                continue
            okved_code, okved_name, sector_id, sector_label, region = self._describe(other_id)
            members.append(
                (
                    other.revenue or 0.0,
                    Counterparty(
                        counterparty_id=other_id,
                        relation="group",
                        direction="group",
                        amount=0.0,
                        count=0,
                        share=None,
                        okved_code=okved_code,
                        okved_name=okved_name,
                        sector_id=sector_id,
                        sector_label=sector_label,
                        region=region,
                        city=other.city or None,
                    ),
                )
            )
        members.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in members[:limit]]

    def _person_links(self, client_id: str, limit: int) -> list[Counterparty]:
        linked: list[Counterparty] = []
        seen: set[str] = set()
        for person in self.persons_of.get(client_id, []):
            for other_id in self.companies_of_person.get(person, []):
                if other_id == client_id or other_id in seen:
                    continue
                seen.add(other_id)
                other = self.clients.get(other_id)
                okved_code, okved_name, sector_id, sector_label, region = self._describe(other_id)
                linked.append(
                    Counterparty(
                        counterparty_id=other_id,
                        relation="person",
                        direction="person",
                        amount=0.0,
                        count=0,
                        share=None,
                        okved_code=okved_code,
                        okved_name=okved_name,
                        sector_id=sector_id,
                        sector_label=sector_label,
                        region=region,
                        city=other.city if other else None,
                    )
                )
                if len(linked) >= limit:
                    return linked
        return linked
