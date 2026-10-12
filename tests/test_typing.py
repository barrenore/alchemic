import os
import re
import subprocess
import sys
import tempfile

import pytest
from sqlalchemy.orm import InstrumentedAttribute

from alchemic import Mapped, Model, mapped_column

# Ensures type checkers (pyright, mypy) are present and executed when
# ALCHEMIC_REQUIRE_TYPECHECK=1 is set.
REQUIRE_TYPECHECK = os.environ.get("ALCHEMIC_REQUIRE_TYPECHECK") == "1"

VALID_CODE = """
from alchemic import Model, Mapped, mapped_column
from sqlalchemy import select

class User(Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()

stmt = select(User).where(User.name == "alice")
user = User(id=1, name="alice")
val: str = user.name
"""

INVALID_CODE = """
from alchemic import Model, Mapped, mapped_column
from sqlalchemy import select

class User(Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()

stmt = select(User).where(User.name in ["alice", "bob"])
"""

COL_CODE = """
from alchemic import Model, Mapped, col

class Hero(Model):
    __tablename__ = "heroes_col_typing"
    id: Mapped[int] = col(primary_key=True, init=False)
    name: Mapped[str] = col(default="anon")
    level: Mapped[int]

Hero(level=1)
Hero(name="ada", level=2)
"""

COL_INVALID_CODE = """
from alchemic import Model, Mapped, col

class Hero(Model):
    __tablename__ = "heroes_col_invalid"
    id: Mapped[int] = col(primary_key=True, init=False)
    level: Mapped[int]

Hero(level="high")
"""


# Overload resolution types checked with assert_type.
EXEC_CODE = """
from collections.abc import Sequence
from typing import Any, assert_type

from pydantic import BaseModel
from sqlalchemy import CursorResult, ForeignKey, Result, ScalarResult, func
from sqlalchemy.engine import TupleResult
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncScalarResult, AsyncTupleResult

from alchemic import (
    AsyncSession, Mapped, Model, Session, async_sessionmaker, delete, insert, mapped_column, select,
    update,
)

class Team(Model):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]

class Hero(Model):
    __tablename__ = "heroes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    age: Mapped[int | None]
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))

class HeroPatch(BaseModel):
    name: str | None = None

def sync(session: Session, hero: Hero) -> None:
    assert_type(session.exec(select(Hero)), ScalarResult[Hero])
    assert_type(session.exec(select(Hero).where(Hero.name == "x")).all(), Sequence[Hero])
    assert_type(session.exec(Hero.where(Hero.age > 3)).first(), Hero | None)
    assert_type(session.exec(select(Hero.name)).one(), str)
    assert_type(session.exec(select(Hero.age)).one(), int | None)
    assert_type(session.exec(select(func.count()).select_from(Hero)).one(), int)
    assert_type(session.exec(select(Hero, Team)), TupleResult[tuple[Hero, Team]])
    assert_type(session.exec(select(Hero, Team.name, Team.id)).all(), Sequence[tuple[Hero, str, int]])
    assert_type(session.exec(update(Hero).where(Hero.id == 1).values(name="x")), CursorResult[Any])
    assert_type(session.exec(delete(Hero)), CursorResult[Any])
    assert_type(session.exec(update(Hero).values(name="x").returning(Hero)).all(), Sequence[Hero])
    assert_type(session.exec(insert(Hero).returning(Hero.id, Hero.name)).all(), Sequence[tuple[int, str]])
    assert_type(session.exec(insert(Hero), [{"name": "a"}, {"name": "b"}]), Result[Any])
    assert_type(session.exec(update(Hero), [{"id": 1, "name": "b"}]), Result[Any])
    assert_type(session.exec(update(Hero).values(name="x"), {"x": 1}), CursorResult[Any])
    assert_type(hero.update(HeroPatch(name="y")), Hero)
    assert_type(hero.update({"name": "y"}), Hero)

async def asynchronous(session: AsyncSession) -> None:
    assert_type(await session.exec(select(Hero)), ScalarResult[Hero])
    assert_type((await session.exec(select(Hero, Team))).one(), tuple[Hero, Team])
    assert_type(await session.exec(delete(Hero)), CursorResult[Any])
    assert_type(await session.exec(insert(Hero), [{"name": "a"}]), Result[Any])
    assert_type(await session.stream_exec(select(Hero)), AsyncScalarResult[Hero])
    assert_type(await session.stream_exec(select(Hero, Team.name)), AsyncTupleResult[tuple[Hero, str]])
    async for hero in await session.stream_exec(select(Hero)):
        assert_type(hero, Hero)

def maker(engine: AsyncEngine) -> None:
    assert_type(async_sessionmaker(engine)(), AsyncSession)
"""

# Raw text() statements are not accepted by typed exec.
EXEC_UNTYPED_CODE = """
from sqlalchemy import text
from alchemic import Session

def execute_text(session: Session) -> None:
    session.exec(text("select 1"))
"""


def _require(name: str) -> list[str]:
    """Return an argv running ``name`` under *this* interpreter, resolving imports against it.

    Deliberately not ``shutil.which``: that resolves against PATH, which may
    well be a different virtualenv that has the checker but not this package
    installed. Every failure then looks like "cannot find module alchemic",
    which is both a false failure here and -- worse -- a false *pass* in
    test_invalid_expressions_rejected, since a missing import is also a
    non-zero exit.
    """
    probe = subprocess.run(
        [sys.executable, "-m", name, "--version"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        if REQUIRE_TYPECHECK:
            pytest.fail(f"{name} not importable under {sys.executable} and ALCHEMIC_REQUIRE_TYPECHECK=1")
        pytest.skip(f"{name} not installed in this environment")
    if name == "pyright":
        # pyright resolves imports against the first ``python3`` on PATH unless told otherwise.
        return [sys.executable, "-m", name, "--pythonpath", sys.executable]
    return [sys.executable, "-m", name]


def _check(argv: list[str], code: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".py") as source_file:
        source_file.write(code)
        source_file.flush()
        return subprocess.run([*argv, source_file.name], capture_output=True, text=True, check=False)


class TypedModel(Model):
    __tablename__ = "typed_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()


def test_runtime_type_descriptors():
    # At class level, name is an InstrumentedAttribute
    assert isinstance(TypedModel.name, InstrumentedAttribute)

    # At instance level, name is str
    instance = TypedModel(id=1, name="Alice")
    assert isinstance(instance.name, str)


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_valid_expressions_accepted(checker: str):
    binary = _require(checker)
    res = _check(binary, VALID_CODE)
    assert res.returncode == 0, f"Valid code failed {checker}:\n{res.stdout}\n{res.stderr}"


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_invalid_expressions_rejected(checker: str):
    """Reject ``User.name in [...]`` -- and for the RIGHT reason.

    Asserting only on a non-zero exit is not enough: an unresolved import exits
    non-zero too, so the test would stay green while proving nothing. Pin the
    diagnostic to the bool-where-a-column-belongs error that is the entire point
    of the package.
    """
    argv = _require(checker)
    res = _check(argv, INVALID_CODE)
    output = res.stdout + res.stderr
    assert res.returncode != 0, f"Expected {checker} to reject User.name in [...]\n{output}"
    assert (
        "alchemic" not in output.lower() or "cannot find" not in output.lower()
    ), f"{checker} failed on an unresolved import, not on the expression under test:\n{output}"
    assert (
        '"bool"' in output or "incompatible type" in output.lower()
    ), f"{checker} rejected the code, but not with the expected bool/arg-type error:\n{output}"


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_col_alias_constructor_is_typed(checker: str):
    binary = _require(checker)
    res = _check(binary, COL_CODE)
    assert res.returncode == 0, f"col() constructor failed {checker}:\n{res.stdout}\n{res.stderr}"


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_col_alias_rejects_wrong_argument_type(checker: str):
    binary = _require(checker)
    res = _check(binary, COL_INVALID_CODE)
    output = res.stdout + res.stderr
    assert res.returncode != 0, f"Expected {checker} to reject level='high' through col()\n{output}"
    assert (
        "reportArgumentType" in output or "arg-type" in output
    ), f"{checker} rejected the code, but not with an argument-type error:\n{output}"


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_exec_and_update_types_resolve_exactly(checker: str):
    res = _check(_require(checker), EXEC_CODE)
    assert res.returncode == 0, f"exec/update types drifted under {checker}:\n{res.stdout}\n{res.stderr}"


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_exec_rejects_untyped_statements(checker: str):
    res = _check(_require(checker), EXEC_UNTYPED_CODE)
    output = res.stdout + res.stderr
    assert res.returncode != 0, f"Expected {checker} to reject exec(text(...))\n{output}"
    assert (
        'overloads for "exec"' in output or 'overload variant of "exec"' in output
    ), f"{checker} rejected exec(text(...)), but not with the expected overload error:\n{output}"


# Constructors and comparisons: valid lines pass cleanly; invalid lines
# marked with # E must report type errors.
_MODELS = """
import uuid
from typing import Annotated, assert_type
from pydantic import Field
from sqlalchemy import ForeignKey, Update, bindparam
from alchemic import Mapped, Model, Session, mapped_column, relationship, select

class Team(Model):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    heroes: Mapped[list["Hero"]] = relationship(back_populates="team")

class Hero(Model):
    __tablename__ = "heroes"
    id: Mapped[str] = mapped_column(primary_key=True)
    uid: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
    name: Mapped[Annotated[str, Field(max_length=8)]]
    age: Mapped[int | None] = mapped_column(default=None)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), default=None)
    team: Mapped[Team | None] = relationship(back_populates="heroes")
    active: Mapped[bool] = mapped_column(default=True)
"""

CHECKED_VALID_CODE = _MODELS + """
def valid(team: Team) -> None:
    Hero(id="h", name="x")
    Hero(id="h", name="x", age=3, team=team, uid=uuid.uuid4(), team_id=None)
    Hero.where(Hero.id == "proper-id", Hero.id != "x")
    Hero.where(Hero.age > 3, Hero.age == None, Hero.age.is_(None))  # noqa: E711
    Hero.where(Hero.team_id == Team.id, Team.id == Hero.team_id)
    Hero.where(Hero.name.in_(["a", "b"]), Hero.age.not_in([1]), Hero.id.in_(select(Hero.id)))
    Hero.where(Hero.age > bindparam("floor"), Hero.name.like("a%"))
    Hero.where(Hero.team.has(Team.name == "red"))
    Team.where(Team.heroes.any(Hero.age > 3))
    select(Hero).join(Team, Team.id == Hero.team_id).order_by(Hero.age.desc())
    hero = Hero(id="h", name="x")
    assert_type(hero.age, int | None)
    hero.age = None
    Hero.where(Hero.active == True, Hero.active != False)  # noqa: E712

async def valid_writes(team: Team, hero: Hero, session: Session) -> None:
    assert_type(hero.update(name="y", age=None, team=team), Hero)
    assert_type(hero.update({"name": "y"}), Hero)
    assert_type(Hero.assign(name="y", age=3), Update)
    session.exec(Hero.assign(age=None).where(Hero.id == "h"))
    assert_type(await hero.load(Hero.team), Team | None)
    assert_type(await team.load(Team.heroes), list[Hero])
"""

CHECKED_INVALID_CODE = _MODELS + """
def invalid(team: Team, hero: Hero) -> None:
    Hero.where(Hero.id == True)  # E
    Hero.where(Hero.age == "old")  # E
    Hero.where(Hero.uid == "not-a-uuid")  # E
    Hero.where(Hero.name == None)  # E  # noqa: E711
    Hero.where(Hero.team_id == Team.name)  # E
    Hero.where(Hero.name.in_([1, 2]))  # E
    Hero(name="x")  # E
    Hero(id="h")  # E
    Hero(id="h", name="x", age="old")  # E
    Hero(id="h", nme="x")  # E
    Hero(id="h", name="x", team="red")  # E
    Hero(id="h", name="x").age = "old"  # E
    Hero.where(Hero.age == True)  # E
    Hero.where(Hero.age != False)  # E
    hero.update(nme="y")  # E
    hero.update(age="old")  # E
    hero.update({"name": "y"}, age=3)  # E
    Hero.assign(nme="y")  # E
    Hero.assign(age="old")  # E
    hero.model_copy()  # E
    Hero.model_construct(id="h", name="x")  # E
"""

# Column types given to mapped_column are checked against the annotation.
# Valid definitions pass cleanly; mismatched types marked with # E fail.
COLUMN_TYPES_CODE = """
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, NewType
from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Numeric, String, Uuid, func
from alchemic import Mapped, Model, mapped_column, references

HeroId = NewType("HeroId", uuid.UUID)

class Status(enum.StrEnum):
    DRAFT = "draft"
    LIVE = "live"

class Priority(enum.IntEnum):
    LOW = 1

class Fine(Model):
    __tablename__ = "fine"
    doc: Mapped[dict[str, Any]] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    id: Mapped[HeroId] = mapped_column(Uuid(), primary_key=True, default=lambda: HeroId(uuid.uuid4()))
    alt_id: Mapped[HeroId | None] = mapped_column(Uuid, default=None)
    name: Mapped[str] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(String(80), default=None)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.DRAFT)
    kind: Mapped[Literal["a", "b"]] = mapped_column(String(1), default="a")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    rate: Mapped[float] = mapped_column(default=0)
    score: Mapped[int] = mapped_column(default=0)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), default=None)
    named: Mapped[str] = mapped_column("real_name", String(20), default="n")
    keyed: Mapped[str] = mapped_column(type_=String(20), default="k")
    ts: Mapped[datetime] = mapped_column(default=datetime.now)
    ts_sql: Mapped[datetime] = mapped_column(default=func.now())
    priority: Mapped[int] = mapped_column(default=Priority.LOW)
    label: Mapped[str] = mapped_column(default=Status.DRAFT)
    day: Mapped[date] = mapped_column(default=datetime.now)

class Wrong(Model):
    __tablename__ = "wrong"
    id: Mapped[int] = mapped_column(primary_key=True)
    int_as_string: Mapped[int] = mapped_column(String(80))  # E
    date_as_datetime: Mapped[date] = mapped_column(DateTime)  # E
    int_as_boolean: Mapped[int] = mapped_column(Boolean)  # E
    hero_id_as_string: Mapped[HeroId] = mapped_column(String(36))  # E
    int_with_string_default: Mapped[int] = mapped_column(default="zero")  # E[mypy]
    string_with_int_default: Mapped[str] = mapped_column(String(10), default=5)  # E
    named_int_as_string: Mapped[int] = mapped_column("col", String(20))  # E
    keyed_int_as_string: Mapped[int] = mapped_column(type_=String(20))  # E
    misspelled_keyword: Mapped[str] = mapped_column(String(10), primay_key=True)  # E
    datetime_with_date_default: Mapped[datetime] = mapped_column(default=date.today)  # E[mypy]

class Team(Model):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    code: Mapped[str] = mapped_column(String(3))
    uid: Mapped[HeroId] = mapped_column(Uuid, default=lambda: HeroId(uuid.uuid4()))

class Linked(Model):
    __tablename__ = "linked"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    team_id: Mapped[int | None] = mapped_column(references(Team.id, ondelete="CASCADE"), default=None)
    required_team_id: Mapped[int] = mapped_column(references(Team.id))
    code: Mapped[str] = mapped_column("code_col", references(Team.code))
    uid: Mapped[HeroId] = mapped_column(references(Team.uid))
    parent_id: Mapped[int | None] = mapped_column(references(id), default=None)

class WrongReferences(Model):
    __tablename__ = "wrong_references"
    id: Mapped[int] = mapped_column(primary_key=True)
    string_references_int: Mapped[str] = mapped_column(references(Team.id))  # E
    unknown_target: Mapped[int] = mapped_column(references(Team.idd))  # E
    hero_id_references_int: Mapped[HeroId] = mapped_column(references(Team.id))  # E
    string_references_own_id: Mapped[str] = mapped_column(references(id))  # E
    reference_with_string_default: Mapped[int] = mapped_column(references(Team.id), default="3")  # E
    ondelete_on_mapped_column: Mapped[int] = mapped_column(references(Team.id), ondelete="CASCADE")  # E

team = Team(code="abc")
Linked(required_team_id=1, code="a", uid=team.uid)
team.update(code="xyz")
Team(id=1, code="abc")  # E
Linked(id=1, required_team_id=1, code="a", uid=team.uid)  # E
team.update(id=2)  # E
Team.assign(id=2)  # E
"""


def _marked_lines(code: str, checker: str) -> set[int]:
    """Lines marked ``# E`` must be errors under both checkers, ``# E[mypy]`` under mypy only."""
    marks = ("# E", f"# E[{checker}]")
    return {
        lineno
        for lineno, line in enumerate(code.splitlines(), 1)
        if line.rstrip().endswith(marks) or "# E  #" in line
    }


def _error_lines(checker: str, output: str) -> set[int]:
    # "file.py:12:5 - error: ..." from pyright, "file.py:12: error: ..." from mypy
    pattern = r"\.py:(\d+):\d+ - error" if checker == "pyright" else r"\.py:(\d+): error"
    return {int(number) for number in re.findall(pattern, output)}


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_checked_constructors_and_comparisons_accept_valid_code(checker: str):
    res = _check(_require(checker), CHECKED_VALID_CODE)
    assert res.returncode == 0, f"valid code rejected by {checker}:\n{res.stdout}\n{res.stderr}"


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_checked_constructors_and_comparisons_reject_each_invalid_line(checker: str):
    res = _check(_require(checker), CHECKED_INVALID_CODE)
    output = res.stdout + res.stderr
    expected = _marked_lines(CHECKED_INVALID_CODE, checker)
    assert _error_lines(checker, output) == expected, f"{checker} errors on the wrong lines:\n{output}"


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_column_types_are_checked_against_the_annotation(checker: str):
    res = _check(_require(checker), COLUMN_TYPES_CODE)
    output = res.stdout + res.stderr
    expected = _marked_lines(COLUMN_TYPES_CODE, checker)
    assert _error_lines(checker, output) == expected, f"{checker} errors on the wrong lines:\n{output}"


# ``Related`` gives each relationship both ends, so a prefetch path, its root and
# a ``link`` are checked. Valid lines must pass, and each ``# E`` fail, under both.
RELATED_CODE = """
from typing import assert_type
from sqlalchemy import Table
from sqlalchemy.orm import selectinload
from alchemic import (
    Mapped, Model, Related, RelatedAttribute, Path, Session, link, mapped_column, path, prefetch,
    references, relationship, select,
)

class Team(Model):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    heroes: Related[list["Hero"]] = relationship()

class Hero(Model):
    __tablename__ = "heroes"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    team_id: Mapped[int | None] = mapped_column(references(Team.id), default=None)
    team: Related[Team | None] = relationship()
    powers: Related[list["Power"]] = relationship()
    tags: Related[set["Tag"]] = relationship()

class Power(Model):
    __tablename__ = "powers"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    heroes: Related[list[Hero]] = relationship()

class Tag(Model):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    heroes: Related[set[Hero]] = relationship()

def valid(session: Session, team: Team, hero: Hero) -> None:
    assert_type(team.heroes, list[Hero])
    assert_type(hero.team, Team | None)
    assert_type(Team.heroes, RelatedAttribute[Team, Hero, list[Hero]])
    assert_type(Hero.team, RelatedAttribute[Hero, Team, Team | None])
    assert_type(path(Team.heroes, Hero.powers, Power.heroes), Path[Team, Hero])
    assert_type(link(Hero.powers, Power.heroes), Table)
    assert_type(link(Hero.tags, Tag.heroes), Table)
    assert_type(hero.tags, set[Tag])
    assert_type(Hero.tags, RelatedAttribute[Hero, Tag, set[Tag]])
    assert_type(path(Team.heroes, Hero.tags, Tag.heroes), Path[Team, Hero])
    assert_type(session.exec(Team.where(prefetch=[Team.heroes, path(Team.heroes, Hero.team)])).one(), Team)
    session.get(Hero, 1, options=prefetch(Hero, Hero.team, path(Hero.powers, Power.heroes)))
    select(Team).join(Team.heroes).options(selectinload(Team.heroes)).where(Team.heroes.any(Hero.id == 1))
    select(Hero).where(Hero.team == team)
    Team(heroes=[hero])
    hero.update(team=None)
    assert_type({hero: team}, dict[Hero, Team])
    assert_type({hero, hero}, set[Hero])

def invalid(team: Team, hero: Hero) -> None:
    Team(heroes=[team])  # E
    hero.update(team=Power())  # E
    path(Team.heroes, Power.heroes)  # E
    Team.where(prefetch=[Hero.powers])  # E
    Team.where(prefetch=[path(Hero.team, Team.heroes)])  # E
    prefetch(Team, Hero.powers)  # E
    link(Hero.powers, Team.heroes)  # E
    link(Hero.team, Team.heroes)  # E
    link(Hero.tags, Power.heroes)  # E
    path(Hero.tags, Power.heroes)  # E
"""


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_relationships_prefetch_paths_and_links_are_checked(checker: str):
    res = _check(_require(checker), RELATED_CODE)
    output = res.stdout + res.stderr
    expected = _marked_lines(RELATED_CODE, checker)
    assert _error_lines(checker, output) == expected, f"{checker} errors on the wrong lines:\n{output}"


# ``onupdate=`` is checked as ``default=`` is, ``readonly`` and ``version`` are
# typed options, ``references`` takes a plain SQLAlchemy model's column, and a
# base class declaring its ``registry`` can be subclassed under mypy too.
WRITE_RULES_CODE = """
import datetime as dt
import time
from sqlalchemy import BigInteger, Date, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, registry
from sqlalchemy.orm import Mapped as SAMapped
from sqlalchemy.orm import mapped_column as sa_mapped_column
from alchemic import Mapped, Model, mapped_column, references

def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)

def unix() -> int:
    return int(time.time())

shared = registry()

class Base(Model):
    __abstract__ = True
    registry = shared

class Legacy(DeclarativeBase):
    pass

class LegacyTeam(Legacy):
    __tablename__ = "legacy_teams"
    id: SAMapped[int] = sa_mapped_column(primary_key=True)

class Doc(Base):
    __tablename__ = "docs"
    id: Mapped[int] = mapped_column(primary_key=True, readonly=True)
    day: Mapped[dt.date] = mapped_column(Date, default=dt.date.today, onupdate=dt.date.today)
    ts: Mapped[int] = mapped_column(BigInteger, default=unix, onupdate=unix)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    touched: Mapped[int] = mapped_column(default=0, onupdate=unix)
    stamped: Mapped[int | None] = mapped_column(default=None, onupdate=unix)
    version: Mapped[int] = mapped_column(version=True, init=False)
    team_id: Mapped[int | None] = mapped_column(references(LegacyTeam.id), default=None)

class Wrong(Base):
    __tablename__ = "wrong_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    typed_int_onupdate_date: Mapped[int] = mapped_column(Integer, default=0, onupdate=dt.date.today)  # E
    datetime_onupdate_int: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=unix)  # E
    inferred_int_onupdate_date: Mapped[int] = mapped_column(default=0, onupdate=dt.date.today)  # E[mypy]
    str_onupdate_int: Mapped[str] = mapped_column(onupdate=unix)  # E[mypy]
    str_references_int: Mapped[str] = mapped_column(references(LegacyTeam.id))  # E
    readonly_not_bool: Mapped[int] = mapped_column(readonly="yes")  # E

Doc(id=1)
Doc(id=1, version=2)  # E
"""


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_write_rules_are_checked(checker: str):
    res = _check(_require(checker), WRITE_RULES_CODE)
    output = res.stdout + res.stderr
    expected = _marked_lines(WRITE_RULES_CODE, checker)
    assert _error_lines(checker, output) == expected, f"{checker} errors on the wrong lines:\n{output}"


# A receiver must accept the model it is registered for, and a ``Change``. A
# decorator's error is reported on the decorator line.
RECEIVERS_CODE = """
from typing import assert_never

from alchemic import Change, Deleted, Inserted, Mapped, Model, Updated, mapped_column, on_commit


class Team(Model):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]

    def before_update(self) -> None:
        if self.has_changed(Team.name):
            pass
        self.has_changed(Team.nme)  # E


class Hero(Model):
    __tablename__ = "heroes"
    id: Mapped[int] = mapped_column(primary_key=True)


@on_commit(Team)
async def every_kind(team: Team, change: Change) -> None:
    match change:
        case Inserted():
            pass
        case Updated(fields=fields):
            print(fields, change.has_changed(Team.name))
        case Deleted():
            pass
        case _:
            assert_never(change)


@on_commit(Team, Deleted)
def deletes(team: Team, change: Deleted) -> None: ...


@on_commit(Team, Inserted, Deleted)
def two_kinds(team: Team, change: Inserted | Deleted) -> None: ...


@on_commit(Model)
def any_model(obj: Model, change: Change) -> None: ...


@on_commit(Team)  # E
def wrong_model(hero: Hero, change: Change) -> None: ...


@on_commit(Team, Deleted)  # E
def wrong_kind(team: Team, change: Updated) -> None: ...


@on_commit(Team)  # E
def wrong_return(team: Team, change: Change) -> int:
    return 1


@on_commit(Team)  # E
def missing_change(team: Team) -> None: ...


@on_commit(Team)
def a_case_missing(team: Team, change: Change) -> None:
    match change:
        case Inserted() | Updated():
            pass
        case _:
            assert_never(change)  # E


def fields_of_a_delete(change: Deleted) -> None:
    change.fields  # E


async def use() -> None:
    await every_kind(Team(id=1, name="x"), Inserted())
"""


@pytest.mark.parametrize("checker", ["pyright", "mypy"])
def test_receivers_and_has_changed_are_checked(checker: str):
    res = _check(_require(checker), RECEIVERS_CODE)
    output = res.stdout + res.stderr
    expected = _marked_lines(RECEIVERS_CODE, checker)
    assert _error_lines(checker, output) == expected, f"{checker} errors on the wrong lines:\n{output}"
