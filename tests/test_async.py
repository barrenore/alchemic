import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine

from alchemic import Mapped, Model, mapped_column, relationship

aiosqlite = pytest.importorskip("aiosqlite")


class AsyncParent(AsyncAttrs, Model):
    __tablename__ = "async_parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    children: Mapped[list["AsyncChild"]] = relationship(back_populates="parent")


class AsyncChild(AsyncAttrs, Model):
    __tablename__ = "async_children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(sa.ForeignKey("async_parents.id"), default=None)
    data: Mapped[str]
    parent: Mapped[AsyncParent | None] = relationship(back_populates="children")


@pytest.mark.asyncio
async def test_async_attrs_and_async_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.create_all)

    async_session = async_sessionmaker(engine)

    async with async_session() as session:
        parent = AsyncParent(id=1, name="Parent")
        child = AsyncChild(id=1, parent=parent, data="Child Data")
        session.add_all([parent, child])
        await session.commit()

    async with async_session() as session:
        stmt = sa.select(AsyncParent).where(AsyncParent.id == 1)
        res = await session.execute(stmt)
        loaded_parent = res.scalar_one()

        # Load relation via awaitable_attrs
        kids = await loaded_parent.awaitable_attrs.children
        assert len(kids) == 1
        assert kids[0].data == "Child Data"


@pytest.fixture
async def async_engine():
    """Fresh in-memory async database per test."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_mutating_a_loaded_row_works_under_async(async_engine):
    """Mutating a loaded row marks it dirty under an async session."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add(AsyncParent(id=1, name="before"))
        await session.commit()
    async with maker() as session:
        loaded = (await session.execute(sa.select(AsyncParent))).scalar_one()
        loaded.name = "after"
        assert loaded in session.dirty
        await session.commit()
    async with maker() as session:
        assert (await session.execute(sa.select(AsyncParent.name))).scalar_one() == "after"


async def test_lazy_load_in_async_raises_rather_than_hanging(async_engine):
    """Accessing an unloaded relationship without await raises MissingGreenlet."""
    from sqlalchemy.exc import MissingGreenlet

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add(AsyncParent(id=1, name="p"))
        await session.commit()
    async with maker() as session:
        parent = (await session.execute(sa.select(AsyncParent))).scalar_one()
        with pytest.raises(MissingGreenlet):
            _ = parent.children


async def test_model_dump_does_not_trigger_io(async_engine):
    """model_dump() excludes relationships and does not trigger lazy loading."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add(AsyncParent(id=1, name="p"))
        await session.commit()
    async with maker() as session:
        parent = (await session.execute(sa.select(AsyncParent))).scalar_one()
        dumped = parent.model_dump()
    assert dumped == {"id": 1, "name": "p"}
    assert "children" not in dumped


async def test_selectinload_and_awaitable_attrs(async_engine):
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        parent = AsyncParent(id=1, name="p")
        parent.children.append(AsyncChild(id=1, data="c1"))
        session.add(parent)
        await session.commit()
    async with maker() as session:
        from sqlalchemy.orm import selectinload

        eager = (
            await session.execute(sa.select(AsyncParent).options(selectinload(AsyncParent.children)))
        ).scalar_one()
        assert [child.data for child in eager.children] == ["c1"]
    async with maker() as session:
        lazy = (await session.execute(sa.select(AsyncParent))).scalar_one()
        assert [child.data for child in await lazy.awaitable_attrs.children] == ["c1"]


async def test_concurrent_tasks_each_with_their_own_session(async_engine):
    """Concurrent tasks mutating distinct loaded rows across separate sessions."""
    import asyncio

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all([AsyncParent(id=index, name=f"n{index}") for index in range(20)])
        await session.commit()

    async def bump(index: int) -> None:
        async with maker() as session:
            row = (await session.execute(sa.select(AsyncParent).where(AsyncParent.id == index))).scalar_one()
            row.name = f"{row.name}-done"
            await session.commit()

    await asyncio.gather(*(bump(index) for index in range(20)))
    async with maker() as session:
        names = (await session.execute(sa.select(AsyncParent.name).order_by(AsyncParent.id))).scalars().all()
    assert names == [f"n{index}-done" for index in range(20)]


# ``Model.load`` needs no ``AsyncAttrs``: these models have none.
class LoadTeam(Model):
    __tablename__ = "load_teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    members: Mapped[list["LoadMember"]] = relationship(back_populates="team")


class LoadMember(Model):
    __tablename__ = "load_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int | None] = mapped_column(sa.ForeignKey("load_teams.id"), default=None)
    team: Mapped[LoadTeam | None] = relationship(back_populates="members")


async def test_load_fetches_lazy_attributes_under_async(async_engine):
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add(LoadMember(id=1, team=LoadTeam(id=1, name="red")))
        await session.commit()
    async with maker() as session:
        member = (await session.execute(sa.select(LoadMember))).scalar_one()
        team = await member.load(LoadMember.team)
        assert team is not None and team.name == "red"
        assert [team_member.id for team_member in await team.load(LoadTeam.members)] == [1]
        # Already loaded now: returned as is.
        assert await member.load(LoadMember.team) is team


async def test_load_refuses_another_models_attribute(async_engine):
    member = LoadMember(id=1)
    with pytest.raises(TypeError, match="LoadTeam.name is not an attribute of LoadMember"):
        await member.load(LoadTeam.name)
