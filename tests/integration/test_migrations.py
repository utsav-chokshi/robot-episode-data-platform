import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from robotics_data_platform.api.factory import create_app
from robotics_data_platform.settings import Settings

pytestmark = pytest.mark.integration


def test_migrations_enable_readiness_and_are_repeatable(fresh_database):
    settings = Settings(database_url=fresh_database)
    with TestClient(create_app(settings)) as client:
        before = client.get("/readyz")
        assert before.status_code == 503
        assert before.json()["schema"] == "migration_required"
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        assert client.get("/readyz").status_code == 200
        engine = create_engine(fresh_database)
        with engine.connect() as connection:
            original = connection.execute(text("SELECT singleton_id, contract_schema_version, created_at_utc FROM platform_metadata")).one()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT singleton_id, contract_schema_version, created_at_utc FROM platform_metadata")).one() == original
            assert connection.scalar(text("SELECT count(*) FROM platform_metadata")) == 1
        engine.dispose()


def test_readiness_blocks_incompatible_contract_version(fresh_database):
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(fresh_database)
    with engine.begin() as connection:
        connection.execute(text("UPDATE platform_metadata SET contract_schema_version = '99.0.0' WHERE singleton_id = 1"))
    engine.dispose()
    with TestClient(create_app(Settings(database_url=fresh_database))) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["schema"] == "contract_mismatch"
