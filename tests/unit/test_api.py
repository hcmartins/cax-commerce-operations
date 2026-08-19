from unittest.mock import Mock

from sqlalchemy.exc import OperationalError


def test_health_is_live_without_database(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Correlation-ID"]


def test_ready_checks_database(client, monkeypatch) -> None:
    monkeypatch.setattr("commerce_operations.api.health.database_is_ready", lambda: True)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_returns_problem_when_database_is_unavailable(client, monkeypatch) -> None:
    error = OperationalError("SELECT 1", {}, Mock())

    def unavailable() -> None:
        raise error

    monkeypatch.setattr("commerce_operations.api.health.database_is_ready", unavailable)

    response = client.get("/ready", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == 503
    assert body["detail"] == "Database is unavailable"
    assert body["correlation_id"] == response.headers["X-Correlation-ID"]


def test_v1_api_information(client) -> None:
    response = client.get("/api/v1/")

    assert response.status_code == 200
    assert response.json() == {"name": "Commerce Operations API", "version": "0.1.0"}


def test_unknown_route_uses_problem_details(client) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["status"] == 404
