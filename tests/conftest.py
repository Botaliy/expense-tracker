from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client(monkeypatch, tmp_path: Path):
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(uploads_dir))
    monkeypatch.setenv("AUTH_USERNAME", "testuser")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    from app.auth import hash_password
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("testpass"))

    import app.database as db_module
    import app.models  # noqa: F401 ensure models are registered on Base

    settings = get_settings()
    test_engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})
    test_session_local = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Point the shared database module at a fresh engine/session for this test.
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", test_session_local)
    db_module.Base.metadata.create_all(bind=test_engine)

    from app.main import app

    def override_get_db():
        session = test_session_local()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def logged_in_client(client: TestClient):
    resp = client.post(
        "/login", data={"username": "testuser", "password": "testpass"}
    )
    assert resp.status_code in (200, 303)
    return client
