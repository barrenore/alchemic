"""``onupdate=``, ``readonly=True``, ``version=True`` and the ``before_insert``/``before_update`` hooks."""

import datetime as dt
import itertools
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, MetaData, String, create_engine, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declared_attr, registry
from sqlalchemy.orm.exc import StaleDataError

from alchemic import AsyncSession, Inserted, Mapped, Model, Session, Updated, mapped_column, on_commit
from alchemic.model import _run_before_insert, _run_before_update


class Base(Model):
    __abstract__ = True
    metadata = MetaData()


class Clock:
    """A clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = dt.datetime(2026, 1, 1)  # noqa: DTZ001  # SQLite's DateTime reads back naive

    def __call__(self) -> dt.datetime:
        return self.now

    def tick(self) -> dt.datetime:
        self.now += dt.timedelta(seconds=1)
        return self.now


clock = Clock()
counter = itertools.count(1)


def next_number() -> int:
    return next(counter)


class Stamped(Base):
    __abstract__ = True
    id: Mapped[int] = mapped_column(primary_key=True, readonly=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=clock, readonly=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=clock, onupdate=clock)
    version: Mapped[int] = mapped_column(version=True, init=False)


class Doc(Stamped):
    __tablename__ = "docs"
    title: Mapped[str] = mapped_column(String(50))
    owner: Mapped[str | None] = mapped_column(String(50), default=None, readonly=True)


class Shapes(Base):
    __tablename__ = "shapes"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    day: Mapped[dt.date] = mapped_column(Date, default=dt.date.today, onupdate=dt.date.today)
    serial: Mapped[int] = mapped_column(BigInteger, default=next_number, onupdate=next_number)


class Hooked(Base):
    __tablename__ = "hooked"
    calls: ClassVar[list[tuple[str, int]]] = []
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), init=False)
    title: Mapped[str] = mapped_column(String(50))
    slug: Mapped[str | None] = mapped_column(String(50), default=None)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"polymorphic_on": "kind", "polymorphic_identity": "hooked"}

    def before_insert(self) -> None:
        self.slug = self.title.lower()
        Hooked.calls.append(("insert", self.id))

    def before_update(self) -> None:
        Hooked.calls.append(("update", self.id))


class SubHooked(Hooked):
    __tablename__ = "sub_hooked"
    id: Mapped[int] = mapped_column(ForeignKey("hooked.id"), primary_key=True)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"polymorphic_identity": "sub"}


class AppendOnly(Base):
    __tablename__ = "append_only"
    id: Mapped[int] = mapped_column(primary_key=True)
    message: Mapped[str] = mapped_column(String(50))

    def before_update(self) -> None:
        raise PermissionError("append-only")


class WithMapperArgs(Base):
    __abstract__ = True
    version: Mapped[int] = mapped_column(version=True, init=False)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"eager_defaults": True}


class ComposedArgs(WithMapperArgs):
    __tablename__ = "composed_args"
    id: Mapped[int] = mapped_column(primary_key=True)

    # A plain dict over a base's declared ``__mapper_args__`` (see test_inheritance).
    __mapper_args__: ClassVar[dict[str, Any]] = {"confirm_deleted_rows": False}  # type: ignore[misc]  # pyright: ignore[reportIncompatibleVariableOverride]


class VersionedParent(Base):
    __tablename__ = "versioned_parent"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), init=False)
    version: Mapped[int] = mapped_column(version=True, init=False)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"polymorphic_on": "kind", "polymorphic_identity": "parent"}


class VersionedChild(VersionedParent):
    __tablename__ = "versioned_child"
    id: Mapped[int] = mapped_column(ForeignKey("versioned_parent.id"), primary_key=True)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"polymorphic_identity": "child"}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    # Shared file-backed SQLite database across sessions.
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def doc(engine: Engine) -> Iterator[tuple[Session, Doc]]:
    with Session(engine) as session:
        document = Doc(id=1, title="a")
        session.add(document)
        session.commit()
        yield session, document


# ---- onupdate ----


def test_timestamps_are_set_on_insert(doc: tuple[Session, Doc]):
    _, document = doc
    assert document.created_at == document.updated_at == clock.now
    assert document.version == 1


def test_updated_at_moves_only_when_a_flush_changes_the_row(doc: tuple[Session, Doc]):
    session, document = doc
    created = document.created_at
    clock.tick()
    document.title = "a"
    session.commit()
    assert document.updated_at == created

    later = clock.tick()
    document.title = "b"
    session.flush()
    assert "updated_at" in vars(document), "a Python onupdate is on the instance after the flush"
    assert document.updated_at == later
    assert document.created_at == created
    assert document.version == 2


def test_update_moves_updated_at(doc: tuple[Session, Doc]):
    session, document = doc
    later = clock.tick()
    document.update(title="b")
    session.commit()
    assert document.updated_at == later


def test_a_bulk_update_moves_updated_at(doc: tuple[Session, Doc]):
    session, document = doc
    later = clock.tick()
    session.exec(Doc.assign(title="c").where(Doc.id == 1))
    session.commit()
    session.refresh(document)
    assert document.updated_at == later


def test_onupdate_takes_any_function(engine: Engine):
    with Session(engine) as session:
        shapes = Shapes(id=1, title="a")
        session.add(shapes)
        session.commit()
        assert shapes.day == dt.date.today()  # noqa: DTZ011
        first = shapes.serial
        shapes.title = "b"
        session.commit()
        assert shapes.serial > first


# ---- readonly ----


def test_readonly_fields_are_set_by_the_constructor(doc: tuple[Session, Doc]):
    _, document = doc
    assert document.id == 1


@pytest.mark.parametrize("field", ["id", "created_at", "version"])
def test_changing_a_readonly_field_is_refused(doc: tuple[Session, Doc], field: str):
    _, document = doc
    before = getattr(document, field)
    value = before + dt.timedelta(days=1) if isinstance(before, dt.datetime) else before + 1
    with pytest.raises(ValidationError, match=field) as raised:
        setattr(document, field, value)
    assert raised.value.errors()[0]["type"] == "frozen_field"
    assert getattr(document, field) == before


def test_writing_back_the_same_value_is_allowed(doc: tuple[Session, Doc]):
    session, document = doc
    document.id = 1
    document.update(id=1, title="b")
    assert not session.is_modified(document, include_collections=False) or document.title == "b"


def test_a_readonly_field_can_be_filled_in_once(doc: tuple[Session, Doc]):
    _, document = doc
    document.owner = "ann"
    with pytest.raises(ValidationError, match="owner"):
        document.owner = "bob"
    assert document.owner == "ann"


def test_an_expired_readonly_field_is_loaded_to_compare(doc: tuple[Session, Doc]):
    session, document = doc
    session.expire(document)
    document.id = 1
    with pytest.raises(ValidationError, match="id"):
        document.id = 2


def test_update_refuses_a_readonly_change_and_writes_nothing(doc: tuple[Session, Doc]):
    _, document = doc
    with pytest.raises(ValidationError, match="id"):
        document.update(title="changed", id=5)
    assert document.title == "a"
    assert document.id == 1


def test_assign_refuses_a_readonly_column():
    with pytest.raises(TypeError, match="readonly"):
        Doc.assign(id=2)


def test_readonly_is_readOnly_in_the_json_schema():
    properties = Doc.model_json_schema()["properties"]
    assert properties["id"]["readOnly"] is True
    assert properties["version"]["readOnly"] is True
    assert "readOnly" not in properties["title"]
    assert not [
        key for field_schema in properties.values() for key in field_schema if key.startswith("alchemic.")
    ]


# ---- version ----


def test_a_version_column_is_the_mappers_version_id_col():
    assert sa_inspect(Doc).version_id_col is Doc.__table__.c.version


def test_a_stale_write_is_refused(engine: Engine):
    with Session(engine) as first, Session(engine) as second:
        first.add(Doc(id=1, title="a"))
        first.commit()
        mine = first.get_one(Doc, 1)
        theirs = second.get_one(Doc, 1)
        theirs.title = "theirs"
        second.commit()
        mine.title = "mine"
        with pytest.raises(StaleDataError):
            first.commit()


def test_version_composes_with_declared_mapper_args():
    mapper = sa_inspect(ComposedArgs)
    assert mapper.version_id_col is ComposedArgs.__table__.c.version
    assert mapper.eager_defaults is True
    assert mapper.confirm_deleted_rows is False


def test_an_inheriting_mapper_keeps_its_parents_version_column(engine: Engine):
    assert sa_inspect(VersionedChild).version_id_col is VersionedParent.__table__.c.version
    with Session(engine) as session:
        child = VersionedChild(id=1)
        assert child.kind == "child"
        session.add(child)
        session.commit()
        assert child.version == 1
        assert "kind" not in child.model_fields_set


def test_an_application_managed_version_is_writable(engine: Engine):
    class Manual(Base):
        __tablename__ = "manual_versions"
        id: Mapped[int] = mapped_column(primary_key=True)
        title: Mapped[str] = mapped_column(String(20))
        version: Mapped[int] = mapped_column(version=True, default=1)

        @declared_attr.directive
        def __mapper_args__(cls) -> dict[str, Any]:
            return {"version_id_generator": False}

    assert sa_inspect(Manual).version_id_col is Manual.__table__.c.version
    assert "readOnly" not in Manual.model_json_schema()["properties"]["version"]
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        manual = Manual(id=1, title="a")
        session.add(manual)
        session.commit()
        manual.update(title="b", version=manual.version + 1)
        session.commit()
        assert manual.version == 2


def test_an_explicit_readonly_wins_over_the_version_default():
    class Pinned(Base):
        __tablename__ = "pinned_versions"
        id: Mapped[int] = mapped_column(primary_key=True)
        version: Mapped[int] = mapped_column(version=True, default=1, readonly=True)

        @declared_attr.directive
        def __mapper_args__(cls) -> dict[str, Any]:
            return {"version_id_generator": False}

    class Loose(Base):
        __tablename__ = "loose_versions"
        id: Mapped[int] = mapped_column(primary_key=True)
        version: Mapped[int] = mapped_column(version=True, init=False, readonly=False)

    assert "version" in Pinned.__alchemic_readonly__
    assert "version" not in Loose.__alchemic_readonly__


def test_a_subclass_can_redeclare_the_version_column():
    class Versioned(Base):
        __abstract__ = True
        id: Mapped[int] = mapped_column(primary_key=True)
        version: Mapped[int] = mapped_column(version=True, init=False)

    class Unversioned(Versioned):
        __tablename__ = "redeclared_unversioned"
        version: Mapped[int] = mapped_column(default=1)

    class SelfManaged(Versioned):
        __tablename__ = "redeclared_self_managed"
        version: Mapped[int] = mapped_column(default=1)

        @declared_attr.directive
        def __mapper_args__(cls) -> dict[str, Any]:
            return {"version_id_col": cls.__table__.c.version, "version_id_generator": False}

    assert sa_inspect(Unversioned).version_id_col is None
    assert sa_inspect(SelfManaged).version_id_col is SelfManaged.__table__.c.version
    assert sa_inspect(SelfManaged).version_id_generator is False


# ---- hooks ----


def test_before_insert_runs_in_the_flush_and_its_changes_are_written(engine: Engine):
    Hooked.calls.clear()
    with Session(engine) as session:
        session.add(Hooked(id=1, title="Hello"))
        session.commit()
        session.expire_all()
        assert session.get_one(Hooked, 1).slug == "hello"
    assert Hooked.calls == [("insert", 1)]


def test_before_update_runs_for_a_changed_row(engine: Engine):
    with Session(engine) as session:
        hooked = Hooked(id=1, title="a")
        session.add(hooked)
        session.commit()
        Hooked.calls.clear()
        hooked.title = "b"
        session.commit()
    assert Hooked.calls == [("update", 1)]


def test_an_inherited_hook_runs_once(engine: Engine):
    Hooked.calls.clear()
    with Session(engine) as session:
        session.add(SubHooked(id=2, title="Sub"))
        session.commit()
    assert Hooked.calls == [("insert", 2)]


def test_before_update_can_refuse(engine: Engine):
    with Session(engine) as session:
        row = AppendOnly(id=1, message="logged")
        session.add(row)
        session.commit()
        row.message = "rewritten"
        with pytest.raises(PermissionError):
            session.commit()
        session.rollback()
        assert row.message == "logged"


def test_a_model_without_hooks_registers_no_listener():
    assert not event.contains(Doc, "before_insert", _run_before_insert)
    assert not event.contains(Doc, "before_update", _run_before_update)
    assert event.contains(Hooked, "before_insert", _run_before_insert)
    assert not event.contains(AppendOnly, "before_insert", _run_before_insert)


# ---- base classes and inherited columns ----


def test_a_base_class_maps_against_the_registry_it_declares():
    shared = registry()

    class SharedBase(Model):
        __abstract__ = True
        registry = shared

    class InShared(SharedBase):
        __tablename__ = "in_shared"
        id: Mapped[int] = mapped_column(primary_key=True)

    try:
        assert shared.metadata.tables["in_shared"] is InShared.__table__
        assert sa_inspect(InShared).registry is shared
    finally:
        shared.dispose()


def test_a_base_class_declaring_metadata_gets_its_own_registry():
    assert Base.metadata.tables["docs"] is Doc.__table__
    assert sa_inspect(Doc).registry is Base.registry
    assert Base.registry.metadata is Base.metadata


def test_redeclaring_an_inherited_column_does_not_warn():
    meta = MetaData()

    class Parent(Model):
        __abstract__ = True
        metadata = meta
        id: Mapped[int] = mapped_column(primary_key=True)
        rank: Mapped[int] = mapped_column(default=1, server_default="1")

    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=".*shadows an attribute")

        class Row(Parent):
            __tablename__ = "rows"
            rank: Mapped[int] = mapped_column(default=2)

    assert Row.__table__.c.rank.default.arg == 2
    Parent.registry.dispose()


def test_other_warnings_still_reach_the_caller():
    meta = MetaData()

    class Parent(Model):
        __abstract__ = True
        metadata = meta

        def label(self) -> str:
            return "parent"

    with pytest.warns(UserWarning, match='"label" .* shadows an attribute'):

        class Row(Parent):
            __tablename__ = "shadowing_rows"
            id: Mapped[int] = mapped_column(primary_key=True)
            label: Mapped[str] = mapped_column(String(10))  # type: ignore[assignment]

    Parent.registry.dispose()


# ---- under an AsyncSession, on Postgres ----


async def test_the_rules_run_inside_an_async_flush(pg_async_engine: Any):
    async with pg_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Hooked.calls.clear()
    async with AsyncSession(pg_async_engine) as session:
        document = Doc(id=1, title="a")
        hooked = Hooked(id=1, title="Hello")
        session.add_all([document, hooked])
        await session.commit()
        assert (document.version, hooked.slug) == (1, "hello")

        later = clock.tick()
        document.update(title="b")
        hooked.title = "b"
        await session.commit()
        assert (document.updated_at, document.version) == (later, 2)
        assert Hooked.calls == [("insert", 1), ("update", 1)]
        with pytest.raises(ValidationError, match="id"):
            document.id = 2


# ---- has_changed, before_delete, and what refuses a bulk update ----


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(10), default="open")
    completed: Mapped[bool] = mapped_column(default=False)
    reopened: Mapped[int] = mapped_column(default=0)

    def before_update(self) -> None:
        if self.has_changed(Task.status):
            self.update(completed=self.status == "done", reopened=self.reopened + (self.status == "open"))

    def before_delete(self) -> None:
        if self.status != "done":
            raise PermissionError("only finished tasks can be deleted")


def test_a_hook_sets_several_fields_from_what_changed(engine: Engine):
    with Session(engine, expire_on_commit=False) as session:
        task = Task(id=1, title="a")
        session.add(task)
        session.commit()
        assert not task.has_changed(Task.status)
        task.status = "done"
        assert task.has_changed(Task.status)
        session.commit()
        assert (task.completed, task.reopened) == (True, 0)
        task.title = "b"
        session.commit()
        assert (task.completed, task.reopened) == (True, 0)
        task.status = "open"
        session.commit()
        assert (task.completed, task.reopened) == (False, 1)


def test_has_changed_refuses_another_models_attribute():
    with pytest.raises(TypeError, match="not an attribute of Task"):
        Task(id=1, title="a").has_changed(Doc.title)


def test_before_delete_can_refuse(engine: Engine):
    with Session(engine, expire_on_commit=False) as session:
        task = Task(id=1, title="a")
        session.add(task)
        session.commit()
        session.delete(task)
        with pytest.raises(PermissionError):
            session.commit()
        session.rollback()
        task.status = "done"
        session.commit()
        session.delete(task)
        session.commit()
        assert session.get(Task, 1) is None


def test_assign_refuses_a_model_whose_hooks_it_would_skip():
    with pytest.raises(TypeError, match=r"Task.before_update\(\).*sqlalchemy.update\(Task\)"):
        Task.assign(status="done")

    class Watched(Base):
        __tablename__ = "watched"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(10))

    @on_commit(Watched, Inserted)
    def on_insert(obj: Watched, change: Inserted) -> None: ...

    Watched.assign(name="x")  # no update receiver: nothing is skipped

    @on_commit(Watched, Updated)
    def on_update(obj: Watched, change: Updated) -> None: ...

    with pytest.raises(TypeError, match="on_commit receivers"):
        Watched.assign(name="x")


def test_an_async_hook_is_refused_before_the_class_is_mapped():
    with pytest.raises(TypeError, match="before_update is async"):

        class AsyncHooked(Base):
            __tablename__ = "async_hooked"
            id: Mapped[int] = mapped_column(primary_key=True)

            async def before_update(self) -> None:  # type: ignore[override]  # pyright: ignore[reportIncompatibleMethodOverride]
                ...

    assert "async_hooked" not in Base.metadata.tables
