"""PostgreSQL feature tests: init=False, cascade deletes, column limits, and stream_exec."""

import datetime as dte
from decimal import Decimal

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import Numeric, String, func
from sqlalchemy.exc import DataError

from alchemic import (
    AsyncSession,
    Mapped,
    Related,
    Session,
    create_model_base,
    link,
    mapped_column,
    path,
    references,
    relationship,
    select,
)

pytestmark = pytest.mark.postgres

FeatureBase = create_model_base(name="FeatureBase")


class Guild(FeatureBase):  # type: ignore[valid-type,misc]
    __tablename__ = "pgf_guilds"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    created: Mapped[dte.datetime] = mapped_column(server_default=func.now(), init=False)
    name: Mapped[str] = mapped_column(String(5))
    fee: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal(0))
    members: Related[list["Member"]] = relationship()


class Member(FeatureBase):  # type: ignore[valid-type,misc]
    __tablename__ = "pgf_members"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    # Usually set through ``guild``, whose id is unknown until it is inserted.
    guild_id: Mapped[int] = mapped_column(references(Guild.id, ondelete="CASCADE"), default=None)
    guild: Related[Guild] = relationship()
    skills: Related[list["Skill"]] = relationship()


class Skill(FeatureBase):  # type: ignore[valid-type,misc]
    __tablename__ = "pgf_skills"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    members: Related[list[Member]] = relationship()


member_skill = link(Member.skills, Skill.members)


@pytest.fixture
def pg(pg_engine):
    FeatureBase.metadata.create_all(pg_engine)
    try:
        yield pg_engine
    finally:
        FeatureBase.metadata.drop_all(pg_engine)


@pytest.fixture
async def pg_async(pg_async_engine):
    async with pg_async_engine.begin() as conn:
        await conn.run_sync(FeatureBase.metadata.create_all)
    try:
        yield pg_async_engine
    finally:
        async with pg_async_engine.begin() as conn:
            await conn.run_sync(FeatureBase.metadata.drop_all)


def _seed(session: Session | AsyncSession) -> Guild:
    guild = Guild(name="red")
    guild.members = [Member(guild=guild, skills=[Skill()]), Member(guild=guild)]
    session.add(guild)
    return guild


def _count(session: Session, table: sa.Table) -> int:
    return session.execute(sa.select(func.count()).select_from(table)).scalar_one()


def test_init_false_columns_are_generated_by_postgres(pg):
    with Session(pg) as session:
        guild = _seed(session)
        session.commit()
        assert isinstance(guild.id, int)
        assert isinstance(guild.created, dte.datetime)


def test_link_and_references_cascade_in_the_database(pg):
    """Cascade deletion via database foreign key constraints."""
    with Session(pg) as session:
        guild = _seed(session)
        session.commit()
        assert _count(session, member_skill) == 1
        session.execute(sa.delete(Guild.__table__).where(Guild.__table__.c.id == guild.id))  # type: ignore[attr-defined]
        session.commit()
        assert _count(session, Member.__table__) == 0  # type: ignore[arg-type]
        assert _count(session, member_skill) == 0


def test_length_limit_counts_characters_as_postgres_does(pg):
    with Session(pg) as session:
        session.add(Guild(name="ééééé"))  # five characters, ten bytes: within VARCHAR(5)
        session.commit()
    with pytest.raises(ValidationError, match="at most 5 characters"):
        Guild(name="éééééé")
    with Session(pg) as session, pytest.raises(DataError):
        session.execute(sa.insert(Guild.__table__).values(name="éééééé"))  # type: ignore[arg-type]


def test_numeric_limit_matches_the_column(pg):
    with Session(pg) as session:
        session.add(Guild(name="rich", fee=Decimal("999.99")))
        session.commit()
    with pytest.raises(ValidationError):
        Guild(name="x", fee=Decimal("1000.00"))
    with Session(pg) as session, pytest.raises(DataError):
        session.execute(sa.insert(Guild.__table__).values(name="x", fee=Decimal("1000.00")))  # type: ignore[arg-type]


async def test_stream_exec_and_prefetch_on_the_async_driver(pg_async):
    async with AsyncSession(pg_async) as session:
        _seed(session)
        await session.commit()
    async with AsyncSession(pg_async) as session:
        stream = await session.stream_exec(
            Guild.where(prefetch=[path(Guild.members, Member.skills)]).execution_options(yield_per=1)
        )
        guilds = [guild async for guild in stream]
        assert [len(member.skills) for member in guilds[0].members] == [1, 0]
        names = await session.stream_exec(select(Guild.name, Guild.fee))
        assert [tuple(row) async for row in names] == [("red", Decimal("0.00"))]
