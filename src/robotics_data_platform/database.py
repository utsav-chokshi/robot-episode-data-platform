"""PostgreSQL connection and a fail-closed database readiness probe."""
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from robotics_data_platform.settings import Settings

CONTRACT_SCHEMA_VERSION = "1.0.0"


def expected_revision(settings: Settings) -> str:
    revision = ScriptDirectory.from_config(Config(str(settings.alembic_config))).get_current_head()
    if revision is None:
        raise RuntimeError("Migration head is not defined.")
    return revision


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
    )


def probe_database(engine: Engine, revision: str) -> dict[str, str]:
    try:
        with engine.connect() as connection:
            exists = connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
            if exists is None:
                return {"status": "not_ready", "database": "reachable", "schema": "migration_required"}
            revisions = set(connection.scalars(text("SELECT version_num FROM alembic_version")))
            if revisions != {revision}:
                return {"status": "not_ready", "database": "reachable", "schema": "revision_mismatch"}
            version = connection.scalar(text("SELECT contract_schema_version FROM platform_metadata WHERE singleton_id = 1"))
            if version != CONTRACT_SCHEMA_VERSION:
                return {"status": "not_ready", "database": "reachable", "schema": "contract_mismatch"}
            return {"status": "ready", "database": "reachable", "schema": "current", "contract_schema_version": version}
    except SQLAlchemyError:
        # Do not return exception strings: connection errors may contain host/user details.
        return {"status": "not_ready", "database": "unavailable", "schema": "unknown"}
