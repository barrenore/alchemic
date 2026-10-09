"""``on_commit`` receivers: when they run, what they are told, and under async."""

import asyncio
import functools
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import ForeignKey, MetaData, String, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession as PlainAsyncSession
from sqlalchemy.orm import Session as PlainSession

from alchemic import (
    AsyncSession,
    Change,
    Deleted,
    Inserted,
    Mapped,
    Model,
    Related,
    Session,
    Updated,
    mapped_column,
    on_commit,
    relationship,
)


class Base(Model):
    __abstract__ = True
    metadata = MetaData()


class Audited:
    """A mixin: a receiver on it sees every model that inherits it."""


class Team(Base, Audited):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))
    heroes: Related[list["Hero"]] = relationship()


class Hero(Base):
    __tablename__ = "heroes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), default=None)
    team: Related[Team | None] = relationship()


class Unwatched(Base):
    __tablename__ = "unwatched"
    id: Mapped[int] = mapped_column(primary_key=True)


seen: list[tuple[str, Any, Change]] = []


@on_commit(Team)
def team_changed(team: Team, change: Change) -> None:
    seen.append(("team", team.id, change))


@on_commit(Hero, Deleted)
def hero_deleted(hero: Hero, change: Deleted) -> None:
    seen.append(("hero", hero.id, change))


@on_commit(Audited)
def audited(obj: Audited, change: Change) -> None:
    seen.append(("audited", type(obj).__name__, change))


@pytest.fixture(autouse=True)
def _clear() -> None:
    seen.clear()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _team_events() -> list[tuple[Any, Change]]:
    return [(row_id, change) for who, row_id, change in seen if who == "team"]


def test_receivers_run_after_the_commit_not_the_flush(engine: Engine):
    with Session(engine) as session:
        session.add(Team(id=1, name="red"))
        session.flush()
        assert seen == []
        session.commit()
    assert _team_events() == [(1, Inserted())]
    assert ("audited", "Team", Inserted()) in seen


def test_an_update_reports_the_fields_it_changed(engine: Engine):
    with Session(engine, expire_on_commit=False) as session:
        team = Team(id=1, name="red")
        session.add(team)
        session.commit()
        seen.clear()
        team.name = "blue"
        session.commit()
        team.name = "blue"  # the value it holds: not a change
        session.commit()
    assert _team_events() == [(1, Updated(frozenset({"name"})))]
    change = _team_events()[0][1]
    assert isinstance(change, Updated)
    assert change.has_changed(Team.name)
    assert not change.has_changed(Team.heroes)


def test_a_relationship_change_is_an_update(engine: Engine):
    with Session(engine, expire_on_commit=False) as session:
        team = Team(id=1, name="red")
        session.add(team)
        session.commit()
        seen.clear()
        team.heroes.append(Hero(id=1, name="ann"))
        session.commit()
    assert _team_events() == [(1, Updated(frozenset({"heroes"})))]


def test_kinds_select_what_a_receiver_hears(engine: Engine):
    with Session(engine) as session:
        hero = Hero(id=1, name="ann")
        session.add(hero)
        session.commit()
        hero.name = "bea"
        session.commit()
        session.delete(hero)
        session.commit()
    assert [session for session in seen if session[0] == "hero"] == [("hero", 1, Deleted())]


def test_a_rollback_drops_everything(engine: Engine):
    with Session(engine) as session:
        session.add(Team(id=1, name="red"))
        session.flush()
        session.rollback()
        session.commit()
    assert seen == []


def test_changes_in_one_transaction_are_merged(engine: Engine):
    with Session(engine, expire_on_commit=False) as session:
        red = Team(id=1, name="red")
        gone = Team(id=2, name="gone")
        session.add_all([red, gone])
        session.flush()
        red.name = "crimson"
        session.delete(gone)
        session.commit()
        assert _team_events() == [(1, Inserted())]

        seen.clear()
        red.name = "scarlet"
        session.flush()
        red.heroes.append(Hero(id=1, name="ann"))
        session.commit()
        assert _team_events() == [(1, Updated(frozenset({"name", "heroes"})))]

        seen.clear()
        red.name = "ruby"
        session.flush()
        session.delete(red)
        session.commit()
    assert _team_events() == [(1, Deleted())]


def test_a_rolled_back_savepoint_is_dropped_and_a_released_one_kept(engine: Engine):
    with Session(engine) as session:
        session.add(Team(id=1, name="outer"))
        savepoint = session.begin_nested()
        session.add(Team(id=2, name="dropped"))
        savepoint.rollback()
        with session.begin_nested():
            session.add(Team(id=3, name="kept"))
        session.commit()
    assert [row_id for row_id, _ in _team_events()] == [1, 3]


def test_every_receiver_runs_and_their_errors_are_raised_together(engine: Engine):
    class Fragile(Base):
        __tablename__ = "fragile"
        id: Mapped[int] = mapped_column(primary_key=True)

    ran: list[str] = []

    @on_commit(Fragile)
    def first(obj: Fragile, change: Change) -> None:
        ran.append("first")
        raise ValueError("first")

    @on_commit(Fragile)
    def second(obj: Fragile, change: Change) -> None:
        ran.append("second")
        raise KeyError("second")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Fragile(id=1))
        with pytest.raises(ExceptionGroup) as raised:
            session.commit()
        assert ran == ["first", "second"]
        assert [type(error) for error in raised.value.exceptions] == [ValueError, KeyError]
        assert session.get(Fragile, 1) is not None, "committed, and the session is usable"


def test_an_async_receiver_needs_an_async_session(engine: Engine):
    class Pinged(Base):
        __tablename__ = "pinged"
        id: Mapped[int] = mapped_column(primary_key=True)

    @on_commit(Pinged)
    async def ping(obj: Pinged, change: Change) -> None: ...

    Base.metadata.create_all(engine)
    with Session(engine) as session, warnings.catch_warnings():
        warnings.simplefilter("error")  # the coroutine is closed, not left unawaited
        session.add(Pinged(id=1))
        with pytest.raises(ExceptionGroup) as raised:
            session.commit()
    assert "commit through an AsyncSession" in str(raised.value.exceptions[0])


def test_an_async_partial_receiver_is_named_in_the_error(engine: Engine):
    class Partial(Base):
        __tablename__ = "partial"
        id: Mapped[int] = mapped_column(primary_key=True)

    async def ping(tag: str, obj: Partial, change: Change) -> None: ...

    on_commit(Partial)(functools.partial(ping, "tag"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Partial(id=1))
        with pytest.raises(ExceptionGroup) as raised:
            session.commit()
    [error] = raised.value.exceptions
    assert isinstance(error, TypeError)
    assert "functools.partial" in str(error)


def test_any_sqlalchemy_session_runs_receivers(engine: Engine):
    with PlainSession(engine) as session:
        session.add(Team(id=1, name="red"))
        session.commit()
    assert _team_events() == [(1, Inserted())]


def test_a_model_without_receivers_records_nothing(engine: Engine):
    with Session(engine) as session:
        session.add(Unwatched(id=1))
        session.flush()
        assert "alchemic.pending_changes" not in session.info


def test_kinds_are_checked():
    with pytest.raises(TypeError, match="Inserted, Updated or Deleted"):
        on_commit(Team, str)  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType]


# ---- AsyncSession ----


class Broadcast(Base):
    __tablename__ = "broadcast"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))


broadcasts: list[tuple[int, str]] = []


@on_commit(Broadcast)
async def broadcast(obj: Broadcast, change: Change) -> None:
    await asyncio.sleep(0)
    broadcasts.append((obj.id, type(change).__name__))


@pytest.fixture
async def async_engine(tmp_path: Path) -> AsyncEngine:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def test_an_async_session_awaits_async_receivers(async_engine: AsyncEngine):
    broadcasts.clear()
    async with AsyncSession(async_engine) as session:
        item = Broadcast(id=1, name="a")
        session.add(item)
        await session.commit()
        assert broadcasts == [(1, "Inserted")]
        item.name = "b"
        await session.commit()
        assert broadcasts == [(1, "Inserted"), (1, "Updated")]
        await session.delete(item)
        await session.commit()
    assert broadcasts[-1] == (1, "Deleted")
    await async_engine.dispose()


async def test_a_begin_block_and_a_plain_async_session_run_receivers_too(async_engine: AsyncEngine):
    broadcasts.clear()
    async with AsyncSession(async_engine) as session, session.begin():
        session.add(Broadcast(id=1, name="a"))
        session.add(Team(id=1, name="red"))
    # An async receiver cannot reload an expired attribute.
    async with PlainAsyncSession(async_engine, expire_on_commit=False) as plain:
        plain.add(Broadcast(id=2, name="b"))
        await plain.commit()
    assert broadcasts == [(1, "Inserted"), (2, "Inserted")]
    assert _team_events() == [(1, Inserted())]
    await async_engine.dispose()


async def test_async_receivers_on_postgres(pg_async_engine: AsyncEngine):
    async with pg_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    broadcasts.clear()
    async with AsyncSession(pg_async_engine) as session:
        session.add(Broadcast(id=1, name="a"))
        await session.commit()
        savepoint = await session.begin_nested()
        session.add(Broadcast(id=2, name="dropped"))
        await savepoint.rollback()
        await session.commit()
    assert broadcasts == [(1, "Inserted")]
