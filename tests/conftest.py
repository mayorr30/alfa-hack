"""Общие настройки тестов.

По умолчанию бэкенд тестируется на временной SQLite (нужен пакет aiosqlite из dev-группы).
Чтобы прогнать те же тесты на PostgreSQL:
    TEST_DATABASE_URL=postgresql+asyncpg://alfa_user:alfa_password@localhost:5432/alfa_test pytest
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Адрес БД задаём ДО импорта приложения: движок создаётся при импорте
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.database import Base, engine
    from app.main import app

    async def reset_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    with TestClient(app) as c:
        c.portal.call(reset_db)
        yield c


@pytest.fixture(scope="session")
def demo(client):
    """Демо-компании по названию сценария: demo['approve']['inn'] и т. д."""
    return {c["scenario"]: c for c in client.get("/api/v1/demo/companies").json()}
