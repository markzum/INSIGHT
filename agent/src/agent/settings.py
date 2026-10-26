"""Настройки запуска. Секрет читается из окружения или agent/.env и в код не записывается."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent.paths import PACKAGE_ROOT, SYNTHETIC_DIR


_MODEL_LIGHT = "google/gemma-4-26b-a4b-it"
_MODEL_REASONING = "z-ai/glm-5.3-flash"
_MODEL_DEFAULT = "qwen/qwen3.6-35b-a3b"
_BASE_URL = "https://changellenge.up.railway.app/v1"


def _load_dotenv() -> None:
    path = PACKAGE_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


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
    llm_base_url: str = _BASE_URL
    llm_api_key: str = ""
    llm_model_light: str = _MODEL_LIGHT
    llm_model_reasoning: str = _MODEL_REASONING
    llm_model_default: str = _MODEL_DEFAULT
    mock_llm: bool = False
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
        _load_dotenv()
        return cls(
            llm_base_url=os.environ.get("LLM_BASE_URL", _BASE_URL).rstrip("/"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model_light=os.environ.get("LLM_MODEL_LIGHT", _MODEL_LIGHT),
            llm_model_reasoning=os.environ.get("LLM_MODEL_REASONING", _MODEL_REASONING),
            llm_model_default=os.environ.get("LLM_MODEL_DEFAULT", _MODEL_DEFAULT),
            mock_llm=_flag("MOCK_LLM", default=False),
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
