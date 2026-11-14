"""Opt-in integration test for the isolated NiyamLens PostgreSQL + pgvector database.

Skipped unless NIYAMLENS_TEST_DATABASE_URL is set. It never falls back to
DATABASE_URL or any other database. If the variable is set but does not point
to 127.0.0.1/localhost:5433 and a `niyamlens` / `niyamlens_*` database, the test
fails before any connection is attempted.

The checks are read-only (SELECT queries only).
"""

import os

import pytest
from sqlalchemy.engine import URL

from app.database.session import (
    build_database_url,
    check_database_connection,
    check_pgvector_extension,
    create_db_engine,
    safe_url,
)

ENV_VAR = "NIYAMLENS_TEST_DATABASE_URL"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
REQUIRED_PORT = 5433

pytestmark = pytest.mark.integration


def _validated_test_url() -> str:
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        pytest.skip(f"{ENV_VAR} is not set; database integration test is opt-in.")

    url: URL = build_database_url(raw)
    problems = []
    if url.host not in ALLOWED_HOSTS:
        problems.append(f"host must be one of {sorted(ALLOWED_HOSTS)}, got {url.host!r}")
    if url.port != REQUIRED_PORT:
        problems.append(f"port must be {REQUIRED_PORT}, got {url.port!r}")
    db = url.database or ""
    if not (db == "niyamlens" or db.startswith("niyamlens_")):
        problems.append(f"database must be 'niyamlens' or 'niyamlens_*', got {db!r}")
    if problems:
        pytest.fail(
            f"Refusing to run against {safe_url(url)}: " + "; ".join(problems),
            pytrace=False,
        )
    return raw


def test_isolated_database_is_reachable_and_has_pgvector() -> None:
    engine = create_db_engine(_validated_test_url())
    try:
        version = check_database_connection(engine)
        assert version.startswith("PostgreSQL 16")
        assert check_pgvector_extension(engine)
    finally:
        engine.dispose()
