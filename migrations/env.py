"""Migration connections use environment settings, never checked-in credentials."""
from alembic import context
from sqlalchemy import create_engine, pool

from robotics_data_platform.settings import Settings

settings = Settings()
url = settings.database_url.get_secret_value()

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
