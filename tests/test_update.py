"""``Model.update`` and assignment validation.

Both validate through Pydantic's assignment validation, enforcing types,
``Field()`` constraints, and validators.
"""

from typing import Annotated, Self

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.orm import Session

from alchemic import Mapped, Model, mapped_column, relationship


class _PlayerColumns(Model):
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[Annotated[str, Field(max_length=8)]]
    level: Mapped[int] = mapped_column(default=1)
    min_rank: Mapped[int] = mapped_column(default=0)
    max_rank: Mapped[int] = mapped_column(default=10)
    nickname: Mapped[str | None] = mapped_column(default=None)

    @field_validator("level")
    @classmethod
    def _level_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("level must be positive")
        return value

    @model_validator(mode="after")
    def _ranks_ordered(self) -> Self:
        if self.min_rank > self.max_rank:
            raise ValueError("min_rank above max_rank")
        return self


class Player(_PlayerColumns):
    __tablename__ = "update_players"


class StrictPlayer(_PlayerColumns):
    __tablename__ = "update_strict_players"
    model_config = ConfigDict(validate_assignment=True)


class PlayerPatch(BaseModel):
    name: str | None = None
    level: int | None = None
    nickname: str | None = None


class Crew(Model):
    __tablename__ = "update_crews"
    id: Mapped[int] = mapped_column(primary_key=True)
    sailors: Mapped[list["Sailor"]] = relationship(back_populates="crew")


class Sailor(Model):
    __tablename__ = "update_sailors"
    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str] = mapped_column("handle_col", String(20))
    rank: Mapped[int] = mapped_column(default=0)
    crew_id: Mapped[int | None] = mapped_column(ForeignKey("update_crews.id"), default=None)
    crew: Mapped[Crew | None] = relationship(back_populates="sailors")


# --- validate_assignment ---


def test_assignment_enforces_field_constraints():
    player = StrictPlayer(id=1, name="ok")
    with pytest.raises(ValidationError, match="at most 8 characters"):
        player.name = "far too long"
    assert player.name == "ok"


def test_assignment_runs_field_validators():
    player = StrictPlayer(id=1, name="ok")
    with pytest.raises(ValidationError, match="level must be positive"):
        player.level = 0
    assert player.level == 1


def test_assignment_runs_model_validators():
    player = StrictPlayer(id=1, name="ok")
    with pytest.raises(ValidationError, match="min_rank above max_rank"):
        player.min_rank = 11
    assert player.min_rank == 0


def test_assignment_coerces_like_construction():
    player = StrictPlayer(id=1, name="ok")
    player.level = "7"  # type: ignore[assignment]
    assert player.level == 7


def test_same_field_name_on_two_models_validates_against_its_own_type():
    """Shared field names on separate models validate against their respective types."""

    class IntCounter(Model):
        __tablename__ = "update_int_counters"
        model_config = ConfigDict(validate_assignment=True)
        id: Mapped[int] = mapped_column(primary_key=True)
        count: Mapped[int]

    class StrCounter(Model):
        __tablename__ = "update_str_counters"
        model_config = ConfigDict(validate_assignment=True)
        id: Mapped[int] = mapped_column(primary_key=True)
        count: Mapped[str]

    IntCounter(id=1, count=1).count = 2
    session = StrCounter(id=1, count="a")
    session.count = "b"
    assert session.count == "b"


def test_construction_runs_before_validators_once():
    """Construction validates once. Re-applying the values must not validate them again."""

    class Tagged(Model):
        __tablename__ = "update_tagged"
        model_config = ConfigDict(validate_assignment=True)
        id: Mapped[int] = mapped_column(primary_key=True)
        tag: Mapped[str]

        @field_validator("tag", mode="before")
        @classmethod
        def _suffix(cls, value: str) -> str:
            return f"{value}!"

    assert Tagged(id=1, tag="a").tag == "a!"


# --- Model.update ---


def test_update_from_model_applies_only_fields_set():
    player = Player(id=1, name="ann", level=3, nickname="a")
    player.update(PlayerPatch(level=4, nickname=None))
    assert (player.name, player.level, player.nickname) == ("ann", 4, None)


def test_update_from_mapping():
    player = Player(id=1, name="ann")
    player.update({"name": "bea", "level": 2})
    assert (player.name, player.level) == ("bea", 2)


def test_update_returns_self():
    player = Player(id=1, name="ann")
    assert player.update({"level": 2}) is player


def test_update_validates_without_validate_assignment():
    player = Player(id=1, name="ann")
    with pytest.raises(ValidationError, match="level must be positive"):
        player.update({"level": 0})


def test_update_is_all_or_nothing():
    player = Player(id=1, name="ann", level=3)
    with pytest.raises(ValidationError, match="at most 8 characters"):
        player.update({"level": 5, "name": "far too long"})
    assert (player.name, player.level) == ("ann", 3)


def test_update_rejects_unknown_fields():
    player = Player(id=1, name="ann")
    with pytest.raises(ValidationError, match="no_such_field"):
        player.update({"level": 5, "no_such_field": 1})
    assert player.level == 1


def test_update_validates_each_value_against_the_ones_before_it():
    player = Player(id=1, name="ann")
    player.update({"max_rank": 20, "min_rank": 15})
    assert (player.min_rank, player.max_rank) == (15, 20)
    with pytest.raises(ValidationError, match="min_rank above max_rank"):
        player.update({"min_rank": 30, "max_rank": 40})


def test_update_of_loaded_row_is_persisted(session: Session):
    session.add(Player(id=1, name="ann", level=3))
    session.commit()
    session.expunge_all()

    loaded = session.scalars(select(Player)).one()
    loaded.update(PlayerPatch(level=9))
    assert loaded in session.dirty
    session.commit()
    session.expunge_all()

    assert session.scalars(select(Player.level)).one() == 9


# --- Model.update with keyword arguments ---


def test_update_with_keywords():
    player = Player(id=1, name="ann")
    assert player.update(name="bea", level=2) is player
    assert (player.name, player.level) == ("bea", 2)


def test_update_with_keywords_validates():
    player = Player(id=1, name="ann", level=3)
    with pytest.raises(ValidationError, match="at most 8 characters"):
        player.update(level=5, name="far too long")
    assert (player.name, player.level) == ("ann", 3)


def test_update_takes_a_patch_or_keywords_not_both():
    player = Player(id=1, name="ann")
    with pytest.raises(TypeError, match="either one patch or keyword arguments"):
        player.update({"level": 2}, name="bea")  # type: ignore[call-overload]
    with pytest.raises(TypeError, match="either one patch or keyword arguments"):
        player.update({"level": 2}, {"name": "bea"})  # type: ignore[call-overload]
    assert (player.name, player.level) == ("ann", 1)


def test_update_sets_relationships_through_the_orm(session: Session):
    crew = Crew(id=1)
    sailor = Sailor(id=1, handle="ann")
    sailor.update(crew=crew)
    assert sailor in crew.sailors
    session.add(crew)
    session.commit()
    session.expunge_all()
    assert session.scalars(select(Sailor.crew_id)).one() == 1


def test_update_applies_relationships_only_after_the_fields_pass():
    crew = Crew(id=1)
    sailor = Sailor(id=1, handle="ann")
    with pytest.raises(ValidationError):
        sailor.update({"crew": crew, "rank": "high"})
    assert sailor.crew is None


# --- Model.assign ---


def test_assign_builds_an_update_of_the_table(session: Session):
    session.add_all([Sailor(id=1, handle="ann"), Sailor(id=2, handle="bea")])
    session.commit()
    session.execute(Sailor.assign(handle="cid").where(Sailor.id == 2))
    session.commit()
    session.expunge_all()
    # ``handle`` maps to the column ``handle_col``: assign takes attribute names.
    assert session.execute(select(Sailor.id, Sailor.handle).order_by(Sailor.id)).all() == [
        (1, "ann"),
        (2, "cid"),
    ]


def test_assign_refuses_relationships():
    with pytest.raises(TypeError, match="sets columns, not crew"):
        Sailor.assign(crew=Crew(id=1))


def test_assign_takes_keywords_only():
    with pytest.raises(TypeError, match="keyword arguments only"):
        Sailor.assign({"handle": "x"})  # type: ignore[call-arg]


class Span(Model):
    """A ``model_validator`` that assigns another field."""

    __tablename__ = "update_spans"
    id: Mapped[int] = mapped_column(primary_key=True)
    start: Mapped[int]
    end: Mapped[int] = mapped_column(default=0)

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        self.end = max(self.end, self.start)
        return self


def test_a_model_validator_can_assign_during_construction(sqlite_engine):
    """A model_validator can assign attributes during construction."""
    span = Span(id=1, start=5)
    assert span.end == 5
    with Session(sqlite_engine) as session:
        session.add(span)
        session.commit()
        assert session.execute(select(Span.end)).scalar_one() == 5


def test_update_keeps_what_a_model_validator_changed(sqlite_engine):
    """Partial updates retain fields modified by a model_validator."""
    with Session(sqlite_engine) as session:
        span = Span(id=1, start=5)
        session.add(span)
        session.flush()
        span.update(start=9)
        assert span.end == 9
        assert span in session.dirty
        session.commit()  # expires attributes on commit
        span.update(start=12)  # validator accesses expired fields
        session.commit()
        assert session.execute(select(Span.end)).scalar_one() == 12
