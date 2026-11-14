"""SQLAlchemy engine/session setup and database readiness checks.

Nothing here connects to the database at import time. Engines are created
lazily, and SQLAlchemy itself only opens a connection when one is first used.
Error messages render the URL with the password hidden.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

PSYCOPG_DRIVER = "postgresql+psycopg"
_ACCEPTED_DRIVERS = {"postgresql", "postgres", PSYCOPG_DRIVER}
CONNECT_TIMEOUT_SECONDS = 5


class DatabaseError(RuntimeError):
    """Base class for database readiness errors."""


class DatabaseNotConfiguredError(DatabaseError):
    """DATABASE_URL is missing or invalid."""


class DatabaseUnavailableError(DatabaseError):
    """The database could not be reached or queried."""


class PgvectorMissingError(DatabaseError):
    """The PostgreSQL `vector` extension is not enabled."""


def build_database_url(raw_url: str) -> URL:
    """Parse a PostgreSQL URL and force the Psycopg 3 driver."""
    try:
        url = make_url(raw_url)
    except ArgumentError as exc:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not a valid database URL. Expected the form "
            "postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME"
        ) from exc
    if url.drivername not in _ACCEPTED_DRIVERS:
        raise DatabaseNotConfiguredError(
            f"DATABASE_URL uses unsupported driver '{url.drivername}'. "
            f"NiyamLens requires PostgreSQL via '{PSYCOPG_DRIVER}://'."
        )
    if not url.database:
        raise DatabaseNotConfiguredError("DATABASE_URL must include a database name.")
    return url.set(drivername=PSYCOPG_DRIVER)


def safe_url(url: URL) -> str:
    """Render a URL for messages with the password masked."""
    return url.render_as_string(hide_password=True)


def create_db_engine(database_url: str | None = None) -> Engine:
    """Create an engine. Does not open a connection."""
    if database_url is None:
        configured = get_settings().database_url
        if configured is None:
            raise DatabaseNotConfiguredError(
                "DATABASE_URL is not set. Copy .env.example to .env in the repository "
                "root and set DATABASE_URL to the NiyamLens database on port 5433."
            )
        database_url = configured.get_secret_value()
    url = build_database_url(database_url)
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a session that commits on success and rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection(engine: Engine | None = None) -> str:
    """Run ``SELECT version()``; return the server version string.

    Raises DatabaseUnavailableError with an actionable message on failure.
    """
    engine = engine or get_engine()
    try:
        with engine.connect() as conn:
            return str(conn.execute(text("SELECT version()")).scalar_one())
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0]
        raise DatabaseUnavailableError(
            f"Could not connect to PostgreSQL at {safe_url(engine.url)}: {reason}. "
            "Check that the NiyamLens database is running (`docker compose up -d db`) "
            "and that DATABASE_URL host, port (5433), user and password are correct."
        ) from exc


def check_pgvector_extension(engine: Engine | None = None) -> str:
    """Verify the `vector` extension is enabled; return its version.

    Raises PgvectorMissingError if it is not enabled in this database, or
    DatabaseUnavailableError if the check itself cannot run.
    """
    engine = engine or get_engine()
    try:
        with engine.connect() as conn:
            installed = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
            if installed is not None:
                return str(installed)
            available = conn.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0]
        raise DatabaseUnavailableError(
            f"Could not check pgvector at {safe_url(engine.url)}: {reason}"
        ) from exc

    if available is None:
        raise PgvectorMissingError(
            f"pgvector is not installed on the server at {safe_url(engine.url)}. "
            "Use the pgvector/pgvector:pg16 image from docker-compose.yml."
        )
    raise PgvectorMissingError(
        f"pgvector is installed but not enabled in database '{engine.url.database}'. "
        "Run `CREATE EXTENSION IF NOT EXISTS vector;` in that database "
        "(docker/postgres/init/01-enable-vector.sql does this on first initialisation)."
    )
