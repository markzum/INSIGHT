from pathlib import Path

import pytest

from agent.clients import ClientDirectory
from agent.paths import SYNTHETIC_DIR


@pytest.fixture
def directory() -> ClientDirectory:
    return ClientDirectory(
        SYNTHETIC_DIR / "clients.csv",
        SYNTHETIC_DIR / "payments.csv",
        SYNTHETIC_DIR / "persons.csv",
    )
