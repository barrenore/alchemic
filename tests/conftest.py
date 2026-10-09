"""Shared fixtures, including the Postgres integration database.

SQLite proves the mapping logic. It does not prove the things this library is
actually deployed against: native ``UUID`` and ``INTERVAL`` columns, ``JSONB``,
real ``ENUM`` types, server-side defaults, and DDL a database can reject. Those
need Postgres, which ``docker-compose.yaml`` provides.

Tests marked ``@pytest.mark.postgres`` skip when the database is not reachable.
Set ``ALCHEMIC_REQUIRE_POSTGRES=1`` to fail rather than skip when Postgres is absent.
"""

import atexit
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

_REQUIRED = os.environ.get("ALCHEMIC_REQUIRE_POSTGRES") == "1"


def _isolated_database_url(admin_url: str) -> str:
    """Create a uniquely-named database on ``admin_url``'s server, return its URL.

    Creates an isolated database per test process, dropped at interpreter exit.
    """
    name = f"alchemic_ci_{uuid.uuid4().hex[:12]}"

    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()

    def _drop() -> None:
        engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            engine.dispose()

    atexit.register(_drop)
    return sa.engine.make_url(admin_url).set(database=name).render_as_string(hide_password=False)


_ADMIN_URL = os.environ.get("ALCHEMIC_POSTGRES_ADMIN_URL")
POSTGRES_URL = (
    _isolated_database_url(_ADMIN_URL)
    if _ADMIN_URL
    else os.environ.get(
        "ALCHEMIC_POSTGRES_URL",
        "postgresql+psycopg://alchemic:alchemic@localhost:5443/alchemic_test",
    )
)
# psycopg3 serves both sync and async through the same dialect.
ASYNC_POSTGRES_URL = POSTGRES_URL


def _postgres_available() -> tuple[bool, str]:
    try:
        engine = sa.create_engine(POSTGRES_URL, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(sa.text("select 1"))
        engine.dispose()
    except Exception as exc:  # noqa: BLE001 -- any failure means "not usable"
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


_AVAILABLE, _REASON = _postgres_available()


def require_postgres() -> None:
    """Skip -- or fail, under ALCHEMIC_REQUIRE_POSTGRES=1 -- if Postgres is absent."""
    if _AVAILABLE:
        return
    message = f"Postgres not reachable at {POSTGRES_URL} ({_REASON}). Start it with `docker compose up -d`."
    if _REQUIRED:
        pytest.fail(f"ALCHEMIC_REQUIRE_POSTGRES=1 but {message}", pytrace=False)
    pytest.skip(message)


@pytest.fixture(scope="session")
def postgres_url() -> str:
    require_postgres()
    return POSTGRES_URL


@pytest.fixture
def sqlite_engine():
    """A fresh in-memory SQLite engine with the model schema created."""
    engine = sa.create_engine("sqlite://")
    from alchemic import Model

    Model.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(sqlite_engine):
    """A session bound to a fresh in-memory SQLite database."""
    with Session(sqlite_engine) as session:
        yield session


def _reset_public_schema(url: str) -> None:
    """Drop and recreate the ``public`` schema."""
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def pg_engine(postgres_url: str):
    """A sync engine against a freshly emptied database."""
    _reset_public_schema(postgres_url)
    engine = sa.create_engine(postgres_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
async def pg_async_engine(postgres_url: str):
    """Async counterpart, same clean-slate discipline."""
    from sqlalchemy.ext.asyncio import create_async_engine

    _reset_public_schema(postgres_url)
    engine = create_async_engine(ASYNC_POSTGRES_URL)
    try:
        yield engine
    finally:
        await engine.dispose()
