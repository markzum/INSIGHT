"""Настройки запуска. Секреты и имена моделей только из окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent.paths import PACKAGE_ROOT, SYNTHETIC_DIR


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw) if raw else default


@dataclass
class Settings:
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model_light: str = "light"
    llm_model_reasoning: str = "reasoning"
    llm_model_default: str = "default"
    mock_llm: bool = True
    top_n: int = 10
    max_iters: int = 2
    max_steps: int = 12
    max_tokens: int = 40_000
    max_seconds: int = 120
    max_llm_calls: int = 12
    concurrency: int = 2
    min_relevance: float = 0.05
    temperature: float = 0.1
    db_path: Path = PACKAGE_ROOT / "data" / "cache" / "agent.sqlite"
    cache_dir: Path = PACKAGE_ROOT / "data" / "cache" / "llm"
    index_cache: Path = PACKAGE_ROOT / "data" / "index"
    clients_csv: Path = SYNTHETIC_DIR / "clients.csv"
    payments_csv: Path = SYNTHETIC_DIR / "payments.csv"
    persons_csv: Path = SYNTHETIC_DIR / "persons.csv"
    news_path: Path = SYNTHETIC_DIR / "clusters.jsonl"
    bank_docs_dir: Path = SYNTHETIC_DIR / "bank"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_base_url=os.environ.get("LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model_light=os.environ.get("LLM_MODEL_LIGHT", "light"),
            llm_model_reasoning=os.environ.get("LLM_MODEL_REASONING", "reasoning"),
            llm_model_default=os.environ.get("LLM_MODEL_DEFAULT", "default"),
            mock_llm=_flag("MOCK_LLM", default=True),
            top_n=int(os.environ.get("AGENT_TOP_N", "10")),
            max_iters=int(os.environ.get("AGENT_MAX_ITERS", "2")),
            max_steps=int(os.environ.get("AGENT_MAX_STEPS", "12")),
            max_tokens=int(os.environ.get("AGENT_MAX_TOKENS", "40000")),
            max_seconds=int(os.environ.get("AGENT_MAX_SECONDS", "120")),
            max_llm_calls=int(os.environ.get("AGENT_MAX_LLM_CALLS", "12")),
            concurrency=int(os.environ.get("AGENT_CONCURRENCY", "2")),
            min_relevance=float(os.environ.get("AGENT_MIN_RELEVANCE", "0.05")),
            temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.1")),
            db_path=_path("AGENT_DB_PATH", PACKAGE_ROOT / "data" / "cache" / "agent.sqlite"),
            cache_dir=_path("AGENT_CACHE_DIR", PACKAGE_ROOT / "data" / "cache" / "llm"),
            index_cache=_path("AGENT_INDEX_CACHE", PACKAGE_ROOT / "data" / "index"),
            clients_csv=_path("AGENT_CLIENTS_CSV", SYNTHETIC_DIR / "clients.csv"),
            payments_csv=_path("AGENT_PAYMENTS_CSV", SYNTHETIC_DIR / "payments.csv"),
            persons_csv=_path("AGENT_PERSONS_CSV", SYNTHETIC_DIR / "persons.csv"),
            news_path=_path("AGENT_NEWS_PATH", SYNTHETIC_DIR / "clusters.jsonl"),
            bank_docs_dir=_path("AGENT_BANK_DOCS_DIR", SYNTHETIC_DIR / "bank"),
        )

    def model_for(self, role: str) -> str:
        return {
            "light": self.llm_model_light,
            "reasoning": self.llm_model_reasoning,
            "default": self.llm_model_default,
        }[role]
