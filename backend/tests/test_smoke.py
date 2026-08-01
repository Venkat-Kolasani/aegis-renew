"""Smoke tests for the Phase 0 API shell."""

from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_check_returns_ok() -> None:
    """The application factory exposes a healthy API."""
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
