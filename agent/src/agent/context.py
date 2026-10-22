"""Сессия запроса и контекст, который видят инструменты."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.bank_index import BankIndex
from agent.clients import ClientDirectory
from agent.llm_client import Budget
from agent.news_store import NewsStore
from agent.ranking import SensitivityRules


@dataclass
class Session:
    client_id: str
    role: str = "ceo"
    trace_id: str = ""

    def __post_init__(self) -> None:
        if self.role not in {"ceo", "cfo", "treasurer"}:
            self.role = "ceo"


@dataclass
class ToolContext:
    session: Session
    directory: ClientDirectory
    news: NewsStore
    bank: BankIndex
    rules: SensitivityRules
    budget: Budget
    steps: list[dict] = field(default_factory=list)
