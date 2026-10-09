"""Integration tests against a real Postgres.

Everything here exercises something SQLite cannot: native UUID and INTERVAL
columns, JSONB, real ENUM types, server-side defaults, and constraints a
database enforces rather than ignores. Skipped when Postgres is unreachable --
unless ALCHEMIC_REQUIRE_POSTGRES=1, which makes the absence an error (see
conftest).
"""

import datetime as dte
import enum
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session, declared_attr, selectinload
from sqlalchemy.orm.exc import StaleDataError

from alchemic import Mapped, create_model_base, mapped_column, relationship

pytestmark = pytest.mark.postgres


class Status(enum.StrEnum):
    DRAFT = "draft"
    LIVE = "live"


PgBase = create_model_base(name="PgBase")


class PgBaseModel(PgBase):  # type: ignore[misc,valid-type]
    __abstract__ = True
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(default=1)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": cls.__table__.c.version}


class Shot(PgBaseModel):
    __tablename__ = "pg_shots"
    scene_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("pg_scenes.id"), default=None)
    label: Mapped[str] = mapped_column(sa.String(40))


class Scene(PgBaseModel):
    __tablename__ = "pg_scenes"
    identifier: Mapped[str] = mapped_column(sa.String(80))
    status: Mapped[Status] = mapped_column(default=Status.DRAFT)
    duration: Mapped[dte.timedelta | None] = mapped_column(default=None)
    budget: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB, default=dict)
    created: Mapped[dte.datetime | None] = mapped_column(server_default=sa.func.now(), default=None)
    shots: Mapped[list[Shot]] = relationship()


@pytest.fixture
def pg(pg_engine):
    PgBase.metadata.create_all(pg_engine)
    try:
        yield pg_engine
    finally:
        PgBase.metadata.drop_all(pg_engine)


@pytest.fixture
async def pg_async(pg_async_engine):
    """Async counterpart to ``pg``: schema created before the test, dropped after."""
    async with pg_async_engine.begin() as conn:
        await conn.run_sync(PgBase.metadata.create_all)
    try:
        yield pg_async_engine
    finally:
        async with pg_async_engine.begin() as conn:
            await conn.run_sync(PgBase.metadata.drop_all)


def test_native_column_types(pg):
    """The DDL Postgres actually received, not what SQLite would have accepted."""
    insp = sa.inspect(pg)
    cols = {column["name"]: column["type"] for column in insp.get_columns("pg_scenes", schema=None)}
    assert isinstance(cols["id"], postgresql.UUID)
    assert isinstance(cols["duration"], postgresql.INTERVAL)
    assert isinstance(cols["payload"], postgresql.JSONB)
    assert isinstance(cols["budget"], sa.Numeric)
    assert isinstance(cols["status"], postgresql.ENUM)  # a real ENUM type, not a VARCHAR


def test_round_trip_native_types(pg):
    with Session(pg) as session:
        session.add(
            Scene(
                identifier="12A",
                status=Status.LIVE,
                duration=dte.timedelta(hours=1, minutes=30),
                budget=Decimal("1234.56"),
                payload={"k": [1, 2, 3]},
            )
        )
        session.commit()
    with Session(pg) as session:
        got = session.execute(sa.select(Scene)).scalar_one()
        assert got.status is Status.LIVE
        assert got.duration == dte.timedelta(hours=1, minutes=30)
        assert got.budget == Decimal("1234.56")
        assert got.payload == {"k": [1, 2, 3]}


def test_server_default_is_populated_and_not_required(pg):
    """A column with server_default is optional during model construction."""
    assert Scene.model_fields["created"].is_required() is False
    with Session(pg) as session:
        scene = Scene(identifier="1")
        assert scene.created is None  # not filled until the DB does it
        session.add(scene)
        session.commit()
        session.refresh(scene)
        assert isinstance(scene.created, dte.datetime)


def test_jsonb_is_queryable(pg):
    with Session(pg) as session:
        session.add_all(
            [Scene(identifier="a", payload={"kind": "x"}), Scene(identifier="b", payload={"kind": "y"})]
        )
        session.commit()
        found = session.execute(sa.select(Scene).where(Scene.payload["kind"].astext == "y")).scalar_one()
        assert found.identifier == "b"


def test_not_null_is_enforced_by_the_database(pg):
    """Bypass the ORM so Postgres, not Pydantic, is the thing rejecting it."""
    with Session(pg) as session, pytest.raises(IntegrityError):
        session.execute(
            sa.text(
                "insert into pg_scenes (id, version, identifier, status, payload) "
                "values (:i, 1, NULL, 'DRAFT', '{}')"
            ),
            {"i": str(uuid.uuid4())},
        )
        session.commit()


def test_optimistic_locking_raises_on_real_concurrent_update(pg):
    with Session(pg) as session:
        session.add(Scene(identifier="race"))
        session.commit()
    with Session(pg) as s1, Session(pg) as s2:
        first_scene = s1.execute(sa.select(Scene)).scalar_one()
        second_scene = s2.execute(sa.select(Scene)).scalar_one()
        first_scene.identifier = "first"
        s1.commit()
        second_scene.identifier = "second"
        with pytest.raises(StaleDataError):
            s2.commit()


def test_enum_is_stored_by_member_name_not_value(pg):
    """SQLAlchemy Enum stores enum member names in PostgreSQL rather than values."""
    with Session(pg) as session:
        session.add(Scene(identifier="e", status=Status.LIVE))
        session.commit()
        raw = session.execute(sa.text("select status::text from pg_scenes")).scalar_one()
    assert raw == "LIVE"
    assert Status.LIVE.value == "live"
    with Session(pg) as session:
        assert session.execute(sa.select(Scene)).scalar_one().status is Status.LIVE


def test_relationship_and_cascade(pg):
    with Session(pg) as session:
        scene = Scene(identifier="s")
        scene.shots.append(Shot(label="wide"))
        session.add(scene)
        session.commit()
    with Session(pg) as session:
        got = session.execute(sa.select(Scene).options(selectinload(Scene.shots))).scalar_one()
        assert [shot.label for shot in got.shots] == ["wide"]


def test_alembic_autogenerate_is_in_sync(pg):
    """Model metadata must match the DDL Postgres actually has."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with pg.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), PgBase.metadata)
    assert diff == [], f"metadata drifted from the database: {diff}"


async def test_async_round_trip_and_mutation(pg_async):
    """The async path, against Postgres rather than aiosqlite."""
    maker = async_sessionmaker(pg_async, expire_on_commit=False)
    async with maker() as session:
        session.add(Scene(identifier="async", duration=dte.timedelta(minutes=5)))
        await session.commit()
    async with maker() as session:
        got = (await session.execute(sa.select(Scene))).scalar_one()
        assert got.duration == dte.timedelta(minutes=5)
        got.identifier = "async-edited"  # mutate a LOADED instance
        await session.commit()
    async with maker() as session:
        assert (await session.execute(sa.select(Scene.identifier))).scalar_one() == "async-edited"
