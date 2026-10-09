"""``Session.exec`` / ``AsyncSession.exec``: results shaped by the statement's arity.

The static half of this contract (which overload a statement picks) is checked by
pyright and mypy in ``test_typing.py``. These check the runtime half agrees.
"""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import CursorResult, ForeignKey, ScalarResult, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from alchemic import (
    AsyncSession,
    Mapped,
    Model,
    Session,
    delete,
    insert,
    mapped_column,
    relationship,
    select,
    update,
)
from alchemic import async_sessionmaker as alchemic_async_sessionmaker


class Guild(Model):
    __tablename__ = "exec_guilds"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    members: Mapped[list["Member"]] = relationship(back_populates="guild")


class Member(Model):
    __tablename__ = "exec_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    level: Mapped[int]
    guild_id: Mapped[int | None] = mapped_column(ForeignKey("exec_guilds.id"), default=None)
    guild: Mapped[Guild | None] = relationship(back_populates="members")


def _seed() -> list[Model]:
    red = Guild(id=1, name="red")
    return [
        red,
        Member(id=1, name="ann", level=3, guild=red),
        Member(id=2, name="bea", level=7, guild=red),
        Member(id=3, name="cid", level=5),
    ]


@pytest.fixture
def db(sqlite_engine: sa.Engine) -> Iterator[Session]:
    with Session(sqlite_engine) as session:
        session.add_all(_seed())
        session.commit()
        yield session


def test_single_entity_is_scalars(db: Session):
    result = db.exec(select(Member).where(Member.level > 4).order_by(Member.id))
    assert isinstance(result, ScalarResult)
    assert [member.name for member in result.all()] == ["bea", "cid"]


def test_single_column_is_scalars(db: Session):
    assert db.exec(select(Member.name).order_by(Member.name)).all() == ["ann", "bea", "cid"]


def test_aggregate_is_a_scalar(db: Session):
    assert db.exec(select(func.count()).select_from(Member)).one() == 3


def test_several_entities_are_tuples(db: Session):
    rows = db.exec(select(Member, Guild).join(Member.guild).order_by(Member.id)).all()
    assert [(member.name, guild.name) for member, guild in rows] == [("ann", "red"), ("bea", "red")]


def test_mixed_columns_are_tuples(db: Session):
    rows = db.exec(select(Member.name, Member.level).order_by(Member.id)).all()
    assert [tuple(row) for row in rows] == [("ann", 3), ("bea", 7), ("cid", 5)]


def test_model_where_helper(db: Session):
    assert db.exec(Member.where(Member.name == "cid")).one().level == 5


def test_dml_without_returning_is_a_cursor_result(db: Session):
    result = db.exec(update(Member).where(Member.level < 6).values(level=6))
    assert isinstance(result, CursorResult)
    assert result.rowcount == 2
    assert db.exec(delete(Member).where(Member.name == "cid")).rowcount == 1


def test_bulk_writes_are_returned_as_they_are(db: Session):
    """A list of parameter sets is a bulk write returning a row-less ``Result``."""
    inserted = db.exec(
        insert(Member), [{"id": 4, "name": "dee", "level": 1}, {"id": 5, "name": "eve", "level": 2}]
    )
    assert not isinstance(inserted, CursorResult)
    updated = db.exec(update(Member), [{"id": 4, "level": 8}])
    assert not isinstance(updated, CursorResult)
    assert db.exec(select(Member.level).where(Member.id.in_([4, 5])).order_by(Member.id)).all() == [8, 2]


def test_bulk_insert_returning_is_still_shaped(db: Session):
    ids = db.exec(insert(Member).returning(Member.id), [{"id": 6, "name": "fay", "level": 1}]).all()
    assert ids == [6]


def test_dml_returning_an_entity_is_scalars(db: Session):
    bumped = db.exec(update(Member).where(Member.id == 1).values(level=9).returning(Member)).all()
    assert [(member.name, member.level) for member in bumped] == [("ann", 9)]


def test_dml_returning_columns_is_tuples(db: Session):
    rows = db.exec(insert(Member).values(id=4, name="dee", level=1).returning(Member.id, Member.name)).all()
    assert [tuple(row) for row in rows] == [(4, "dee")]


def test_params_and_options_pass_through(db: Session):
    stmt = select(Member.name).where(Member.level > sa.bindparam("floor"))
    names = db.exec(stmt, {"floor": 4}, execution_options={"populate_existing": True}).all()
    assert sorted(names) == ["bea", "cid"]


def test_sessionmaker_builds_the_subclass(sqlite_engine: sa.Engine):
    with sessionmaker(sqlite_engine, class_=Session)() as session:
        assert isinstance(session, Session)
        assert session.exec(select(func.count()).select_from(Member)).one() == 0


async def test_async_exec():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.create_all)

    async with async_sessionmaker(engine, class_=AsyncSession)() as session:
        session.add_all(_seed())
        await session.commit()

        assert [member.name for member in (await session.exec(select(Member).order_by(Member.id))).all()] == [
            "ann",
            "bea",
            "cid",
        ]
        pairs = (await session.exec(select(Member.name, Guild.name).join(Member.guild))).all()
        assert sorted(tuple(pair) for pair in pairs) == [("ann", "red"), ("bea", "red")]
        assert (await session.exec(delete(Member).where(Member.guild_id.is_(None)))).rowcount == 1

    await engine.dispose()


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.create_all)
    async with alchemic_async_sessionmaker(engine)() as session:
        session.add_all(_seed())
        await session.commit()
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_async_stream_exec_shapes_by_arity(async_engine):
    async with AsyncSession(async_engine) as session:
        stream = await session.stream_exec(select(Member).order_by(Member.id))
        assert [member.name async for member in stream] == ["ann", "bea", "cid"]
        pairs = await session.stream_exec(select(Member.name, Member.level).order_by(Member.id))
        assert [tuple(pair) async for pair in pairs] == [("ann", 3), ("bea", 7), ("cid", 5)]


async def test_async_bulk_write_is_returned_as_it_is(async_engine):
    async with AsyncSession(async_engine) as session:
        await session.exec(insert(Member), [{"id": 4, "name": "dee", "level": 1}])
        assert (await session.exec(select(Member.name).where(Member.id == 4))).one() == "dee"


async def test_async_session_does_not_expire_on_commit(async_engine):
    """Reading a loaded attribute after commit must not need a refresh query."""
    async with AsyncSession(async_engine) as session:
        member = (await session.exec(select(Member).where(Member.id == 1))).one()
        member.level = 9
        await session.commit()
        assert member.level == 9


async def test_async_sessionmaker_builds_the_subclass_without_expiry(async_engine):
    maker = alchemic_async_sessionmaker(async_engine)
    async with maker() as session:
        assert isinstance(session, AsyncSession)
        assert session.sync_session.expire_on_commit is False
    async with alchemic_async_sessionmaker(async_engine, expire_on_commit=True)() as session:
        assert session.sync_session.expire_on_commit is True


async def test_repr_after_an_expiring_commit_does_no_io(async_engine):
    async with AsyncSession(async_engine, expire_on_commit=True) as session:
        member = (await session.exec(select(Member).where(Member.id == 1))).one()
        await session.commit()
        assert repr(member) == "<Member(id=1)>"
