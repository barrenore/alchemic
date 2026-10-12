import uuid
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import InstrumentedAttribute, Session

from alchemic import Mapped, Model, col, mapped_column, relationship


class Actor(Model):
    __tablename__ = "actors"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(80))
    characters: Mapped[list["Character"]] = relationship(back_populates="actor")


class Character(Model):
    __tablename__ = "characters"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(80))
    screen_time: Mapped[timedelta | None] = mapped_column(default=None)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("actors.id"), default=None)
    actor: Mapped[Actor | None] = relationship(back_populates="characters")


# --- construction validation ---


def test_col_is_mapped_column_and_declares_columns():
    import alchemic

    assert col is mapped_column
    assert "col" in alchemic.__all__

    class Shelf(Model):
        __tablename__ = "shelves_col"
        id: Mapped[int] = col(primary_key=True, init=False)
        label: Mapped[str] = col(sa.String(20), default="misc")

    assert set(Shelf.__table__.columns.keys()) == {"id", "label"}
    assert Shelf(label="books").label == "books"
    assert Shelf().label == "misc"


def test_construction_validates_types():
    with pytest.raises(ValidationError):
        Character(name=12345)  # type: ignore[arg-type]


def test_construction_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        Character()  # type: ignore[call-arg]


def test_construction_accepts_valid_input():
    character = Character(name="Rick")
    assert character.name == "Rick"
    assert isinstance(character.id, uuid.UUID)


# --- class vs instance attributes ---


def test_class_attribute_is_queryable():
    assert isinstance(Character.name, InstrumentedAttribute)
    assert str(Character.name == "x") == "characters.name = :name_1"


def test_instance_attribute_is_the_value():
    assert Character(name="Rick").name == "Rick"


# --- ORM round trip & relationships ---


def test_round_trip(session: Session):
    alice = Actor(name="Alice")
    session.add(alice)
    session.add(Character(name="Rick", actor=alice, screen_time=timedelta(minutes=12)))
    session.commit()

    got = session.execute(sa.select(Character).where(Character.name == "Rick")).scalar_one()
    assert got.name == "Rick"
    actor = got.actor
    assert actor is not None
    assert actor.name == "Alice"
    assert got.screen_time == timedelta(minutes=12)


def test_relationship_collection(session: Session):
    actor = Actor(name="Bob")
    session.add_all([actor, Character(name="X", actor=actor), Character(name="Y", actor=actor)])
    session.commit()
    assert {character.name for character in actor.characters} == {"X", "Y"}


def test_relationship_assignment_on_instance(session: Session):
    charlie = Actor(name="Charlie")
    morty = Character(name="Morty")
    morty.actor = charlie
    session.add_all([charlie, morty])
    session.commit()
    actor = morty.actor
    assert actor is not None
    assert actor.name == "Charlie"

    morty.actor = None
    session.commit()
    assert morty.actor is None
    assert morty.actor_id is None


# --- Pydantic serialization & schemas ---


def test_model_dump():
    character = Character(name="Rick")
    dumped = character.model_dump()
    assert dumped["name"] == "Rick"
    assert set(dumped) == {"id", "name", "screen_time", "actor_id"}


def test_json_schema_has_all_columns():
    props = Character.model_json_schema()["properties"]
    assert {"id", "name", "screen_time", "actor_id"} <= set(props)
    # Ensure relationship is NOT in JSON schema properties
    assert "actor" not in props


def test_column_types_are_native():
    cols = {column.name: str(column.type) for column in Character.__table__.columns}
    assert cols["name"] == "VARCHAR(80)"
    assert cols["screen_time"] == "DATETIME"
    assert Character.__table__.c.id.primary_key


def test_model_validate():
    character = Character(name="Rick", screen_time=timedelta(minutes=5))
    c2 = Character.model_validate(character)
    assert c2.name == "Rick"
    assert c2.screen_time == timedelta(minutes=5)
    assert c2.id == character.id


def test_detached_instance_dump(session: Session):
    actor = Actor(name="Alice")
    character = Character(name="Rick", actor=actor)
    session.add_all([actor, character])
    session.commit()

    char = session.execute(sa.select(Character).where(Character.name == "Rick")).scalar_one()
    assert char.actor is not None
    assert char.actor.name == "Alice"
    session.expunge(char)

    dumped = char.model_dump()
    assert dumped["name"] == "Rick"
    assert "actor" not in dumped


def test_repr():
    character = Character(name="Rick")
    representation = repr(character)
    assert representation.startswith("<Character(id=")


def test_server_default_column_declared_optional_is_filled_by_the_database(session: Session):
    """``default=None`` makes a DB-filled column optional, and the DB fills it.

    Optionality follows the declaration, not the column (see
    ``_fields.declared_defaults``): ``server_default`` alone is required, as the
    type checker sees it. Declared ``default=None``, the attribute stays ``None``
    until flush, and ``None`` is left out of the ``INSERT``, so the database's
    default applies rather than a NULL.
    """

    class Row(Model):
        __tablename__ = "server_default_rows"
        id: Mapped[int] = mapped_column(primary_key=True)
        created: Mapped[datetime] = mapped_column(server_default=sa.func.now(), default=None)
        seq: Mapped[int] = mapped_column(server_default=sa.text("7"), default=None)
        required: Mapped[int] = mapped_column(server_default=sa.text("1"))

    assert Row.model_fields["created"].is_required() is False
    assert Row.model_fields["seq"].is_required() is False
    assert Row.model_fields["required"].is_required() is True

    row = Row(id=1, required=2)
    assert row.created is None  # the DB has not filled it yet
    Model.metadata.tables["server_default_rows"].create(session.get_bind())
    session.add(row)
    session.commit()
    assert isinstance(row.created, datetime)
    assert row.seq == 7


def test_mapped_column_refuses_a_column():
    with pytest.raises(TypeError, match="takes a Column's arguments, not a Column"):
        mapped_column(sa.Column(sa.String(20)))
