from fastapi.testclient import TestClient

from robotics_data_platform.api.factory import create_app
from robotics_data_platform.settings import Settings


def unreachable_settings():
    return Settings(database_url="postgresql+psycopg://test_user:do-not-return-this@127.0.0.1:1/unused")


def test_liveness_does_not_depend_on_database():
    with TestClient(create_app(unreachable_settings())) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_readiness_fails_without_exposing_connection_details():
    with TestClient(create_app(unreachable_settings())) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "database": "unavailable", "schema": "unknown"}
        assert "do-not-return-this" not in response.text
        assert "test_user" not in response.text
