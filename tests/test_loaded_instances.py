"""Instances built by SQLAlchemy's loader behave like Pydantic models."""

import copy
import pickle
import uuid

import pytest
import sqlalchemy as sa
from pydantic import ConfigDict, ValidationError
from sqlalchemy.orm import Session

from alchemic import Mapped, Model, mapped_column


class Widget(Model):
    __tablename__ = "widgets_loaded"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(50))
    count: Mapped[int] = mapped_column(default=0)


class ValidatedWidget(Model):
    __tablename__ = "validated_widgets_loaded"
    model_config = ConfigDict(validate_assignment=True)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(50))
    count: Mapped[int] = mapped_column(default=0)


def _store(sqlite_engine, cls, **kwargs):
    with Session(sqlite_engine) as session:
        session.add(cls(**kwargs))
        session.commit()


def _load(sqlite_engine, cls):
    """Load in a session that never saw the constructed instance."""
    with Session(sqlite_engine) as session:
        yield session, session.execute(sa.select(cls)).scalars().one()


def test_loaded_instance_has_pydantic_state(sqlite_engine):
    _store(sqlite_engine, Widget, name="a")
    for _s, obj in _load(sqlite_engine, Widget):
        assert hasattr(obj, "__pydantic_fields_set__")
        assert obj.__pydantic_fields_set__ == set()  # loaded, not set


def test_loaded_instance_accepts_assignment(sqlite_engine):
    _store(sqlite_engine, Widget, name="a")
    for _s, obj in _load(sqlite_engine, Widget):
        obj.name = "b"
        assert obj.name == "b"
        assert "name" in obj.__pydantic_fields_set__


def test_loaded_instance_assignment_is_dirty_and_persists(sqlite_engine):
    _store(sqlite_engine, Widget, name="a")
    with Session(sqlite_engine) as session:
        obj = session.execute(sa.select(Widget)).scalars().one()
        obj.name = "b"
        assert obj in session.dirty
        session.commit()
    with Session(sqlite_engine) as session:
        assert session.execute(sa.text("select name from widgets_loaded")).scalar_one() == "b"


def test_loaded_instance_supports_deletion(sqlite_engine):
    _store(sqlite_engine, Widget, name="a", count=7)
    for _s, obj in _load(sqlite_engine, Widget):
        del obj.count
        assert "count" not in obj.__dict__


def test_loaded_instance_supports_pickle(sqlite_engine):
    _store(sqlite_engine, Widget, name="a")
    for _s, obj in _load(sqlite_engine, Widget):
        clone = pickle.loads(pickle.dumps(obj))
        assert clone.name == "a"


# Loaded rows refuse copying like constructed rows.
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, lambda widget: widget.model_copy()])
def test_loaded_instance_refuses_copy_like_a_constructed_one(sqlite_engine, operation):
    _store(sqlite_engine, Widget, name="a")
    for _s, obj in _load(sqlite_engine, Widget):
        with pytest.raises(TypeError, match="cannot be copied"):
            operation(obj)


def test_loaded_instance_still_validates_assignment(sqlite_engine):
    _store(sqlite_engine, ValidatedWidget, name="a")
    for _s, obj in _load(sqlite_engine, ValidatedWidget):
        with pytest.raises(ValidationError):
            obj.count = "not-a-number"  # type: ignore[assignment]
        obj.count = 3
        assert obj.count == 3


def test_loading_does_not_validate(sqlite_engine):
    """A database row that violates model constraints still loads."""
    with Session(sqlite_engine) as session:
        session.execute(
            sa.text("insert into widgets_loaded (id, name, count) values (:i, :n, 0)"),
            {"i": str(uuid.uuid4()), "n": "x" * 300},  # column is VARCHAR(50)
        )
        session.commit()
    for _s, obj in _load(sqlite_engine, Widget):
        assert len(obj.name) == 300


def test_loading_stays_off_the_slow_paths(sqlite_engine, monkeypatch):
    """Row loading does not invoke Model._assign or register load event listeners."""
    with Session(sqlite_engine) as session:
        session.add_all(Widget(name=str(index)) for index in range(20))
        session.commit()

    assigned: list[str] = []
    real_assign = Model._assign
    monkeypatch.setattr(
        Model, "_assign", lambda self, name, value: (assigned.append(name), real_assign(self, name, value))
    )
    with Session(sqlite_engine) as session:
        assert len(session.execute(sa.select(Widget)).scalars().all()) == 20
    assert assigned == []
    assert not sa.inspect(Widget).class_manager.dispatch.load
