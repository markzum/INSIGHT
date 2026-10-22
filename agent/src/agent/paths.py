"""Пути пакета и чтение YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def find_root() -> Path:
    override = os.environ.get("AGENT_HOME")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "okved_sectors.yaml").is_file():
            return parent
    return here.parents[2]


PACKAGE_ROOT = find_root()
CONFIG_DIR = PACKAGE_ROOT / "config"
SYNTHETIC_DIR = PACKAGE_ROOT / "data" / "synthetic"


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался словарь YAML в {path}")
    return data
