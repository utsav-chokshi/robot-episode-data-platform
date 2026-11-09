"""Each integration test owns a uniquely named temporary database."""
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture
def fresh_database(monkeypatch):
    configured = os.environ.get("RDP_TEST_DATABASE_URL")
    if not configured:
        pytest.skip("Dedicated RDP_TEST_DATABASE_URL is not configured.")
    admin_url = make_url(configured)
    name = "rdp_test_" + uuid.uuid4().hex
    assert re.fullmatch(r"rdp_test_[0-9a-f]{32}", name)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    test_url = admin_url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("RDP_DATABASE_URL", test_url)
    try:
        yield test_url
    finally:
        # Only this fixture's exact generated database name is eligible for cleanup.
        assert re.fullmatch(r"rdp_test_[0-9a-f]{32}", name)
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
