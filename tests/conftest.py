from __future__ import annotations

from pathlib import Path

import pytest

from pi_guard.config.settings import Settings
from pi_guard.storage.db import Database

TOKEN = "123456789:AAH" + "x" * 32


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("PI_GUARD_"):
            monkeypatch.delenv(key)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token=TOKEN,
        telegram_admin_user_ids=[1001],
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path / "run",
        config_file=tmp_path / "config.yaml",
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "state.db")
    yield database
    database.close()
