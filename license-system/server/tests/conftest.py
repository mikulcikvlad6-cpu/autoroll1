"""Общие настройки тестов: отдельная SQLite-база и предсказуемые секреты."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

TEST_DB = SERVER_DIR / "tests" / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["CLIENT_SECRET"] = "test-client-secret"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["CLIENT_SIGN_REQUIRED"] = "0"
os.environ["KEY_PREFIX"] = "LARP"
os.environ["OWNER_IDS"] = "1"

import pytest  # noqa: E402

from app.database import Base, engine  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}
