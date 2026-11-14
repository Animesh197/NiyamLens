"""Offline tests for database configuration handling. No connection is opened."""

import pytest
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.database import session as db_session
from app.database.session import (
    DatabaseNotConfiguredError,
    DatabaseUnavailableError,
    build_database_url,
    check_database_connection,
    create_db_engine,
    safe_url,
)


def test_importing_module_does_not_create_an_engine() -> None:
    assert db_session._engine is None


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u:pw@127.0.0.1:5433/niyamlens",
        "postgres://u:pw@127.0.0.1:5433/niyamlens",
        "postgresql+psycopg://u:pw@127.0.0.1:5433/niyamlens",
    ],
)
def test_build_database_url_forces_psycopg3(raw: str) -> None:
    url = build_database_url(raw)
    assert url.drivername == "postgresql+psycopg"
    assert url.port == 5433
    assert url.database == "niyamlens"


@pytest.mark.parametrize(
    "raw",
    [
        "mysql://u:pw@127.0.0.1:3306/niyamlens",
        "postgresql+psycopg://u:pw@127.0.0.1:5433",
        "not a url",
    ],
)
def test_build_database_url_rejects_invalid_urls(raw: str) -> None:
    with pytest.raises(DatabaseNotConfiguredError):
        build_database_url(raw)


def test_create_engine_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db_session, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(DatabaseNotConfiguredError, match="DATABASE_URL is not set"):
        create_db_engine()


def test_safe_url_hides_password() -> None:
    engine = create_db_engine("postgresql://u:super-secret-pw@127.0.0.1:5433/niyamlens")
    try:
        rendered = safe_url(engine.url)
        assert "super-secret-pw" not in rendered
        assert "127.0.0.1:5433/niyamlens" in rendered
    finally:
        engine.dispose()


def test_connection_failure_is_actionable_and_hides_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_db_engine("postgresql://u:super-secret-pw@127.0.0.1:5433/niyamlens")

    def refuse(*args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT version()", {}, Exception("connection refused"))

    monkeypatch.setattr(engine, "connect", refuse)
    try:
        with pytest.raises(DatabaseUnavailableError) as excinfo:
            check_database_connection(engine)
    finally:
        engine.dispose()

    message = str(excinfo.value)
    assert "connection refused" in message
    assert "5433" in message
    assert "super-secret-pw" not in message
