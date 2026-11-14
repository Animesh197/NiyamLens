import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.database import session as db_session
from app.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "niyamlens-backend",
        "version": __version__,
    }


def test_health_does_not_touch_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("health check must not create a database engine")

    monkeypatch.setattr(db_session, "create_engine", fail_if_called)
    monkeypatch.setattr(db_session, "_engine", None)

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200


def test_unknown_route_returns_404() -> None:
    assert TestClient(app).get("/api/v1/does-not-exist").status_code == 404
