"""``mapped_column(init=False)``: a value the database generates is not a constructor argument.

The type checker leaves such a field out of the constructor, ``update`` and
``assign`` (``test_typing.py``). At runtime, passing one is refused the same way,
and the database fills it in on flush.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import String, func

from alchemic import Mapped, Model, Session, mapped_column, select


class _Generated(Model):
    """An abstract base, so the inherited ``init=False`` is covered too."""

    __abstract__ = True
    id: Mapped[int] = mapped_column(primary_key=True, init=False)


class Entry(_Generated):
    __tablename__ = "init_false_entries"
    created: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)
    title: Mapped[str] = mapped_column(String(20))


def test_generated_columns_are_filled_in_by_the_database(sqlite_engine):
    entry = Entry(title="a")
    assert (entry.id, entry.created) == (None, None)
    with Session(sqlite_engine) as session:
        session.add(entry)
        session.commit()
        assert entry.id == 1
        assert isinstance(entry.created, datetime)
    with Session(sqlite_engine) as session:
        assert session.exec(select(Entry.id)).one() == 1


def test_passing_a_generated_column_is_refused():
    with pytest.raises(ValidationError, match="Declared init=False") as raised:
        Entry(id=1, created=datetime.now(UTC), title="a")  # type: ignore[call-arg]
    assert [(error["loc"], error["type"]) for error in raised.value.errors()] == [
        (("created",), "init_false"),
        (("id",), "init_false"),
    ]


@pytest.mark.parametrize(
    "call",
    [lambda instance: instance.update(id=2), lambda instance: instance.update({"created": None})],
    ids=["keywords", "patch"],
)
def test_update_refuses_a_generated_column(call):
    entry = Entry(title="a")
    with pytest.raises(ValidationError, match="Declared init=False"):
        call(entry)


def test_generated_columns_are_still_serialized():
    assert Entry(title="a").model_dump() == {"id": None, "created": None, "title": "a"}
