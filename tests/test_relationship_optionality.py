"""A single-object relationship that can be ``None`` must be annotated ``| None``.

Checked when mappers are configured. Each case builds its own registry and
disposes of it, because a failed configuration stays failed.
"""

from collections.abc import Callable, Iterator
from typing import Optional

import pytest
from sqlalchemy import ForeignKey
from sqlalchemy.exc import InvalidRequestError

from alchemic import Mapped, Model, create_model_base, mapped_column, references, relationship


@pytest.fixture
def base() -> Iterator[type[Model]]:
    base = create_model_base()
    yield base
    base.registry.dispose()


def _nullable_many_to_one(base: type[Model]) -> None:
    class Team(base):  # type: ignore[valid-type,misc]
        __tablename__ = "t"
        id: Mapped[int] = mapped_column(primary_key=True)

    class Hero(base):  # type: ignore[valid-type,misc]
        __tablename__ = "h"
        id: Mapped[int] = mapped_column(primary_key=True)
        team_id: Mapped[int | None] = mapped_column(references(Team.id))
        team: Mapped[Team] = relationship()


def _string_annotation(base: type[Model]) -> None:
    class Hero(base):  # type: ignore[valid-type,misc]
        __tablename__ = "h"
        id: Mapped[int] = mapped_column(primary_key=True)
        team_id: Mapped[int | None] = mapped_column(ForeignKey("t.id"))
        team: "Mapped[Team]" = relationship()

    class Team(base):  # type: ignore[valid-type,misc]
        __tablename__ = "t"
        id: Mapped[int] = mapped_column(primary_key=True)


def _one_to_one_parent_side(base: type[Model]) -> None:
    class User(base):  # type: ignore[valid-type,misc]
        __tablename__ = "u"
        id: Mapped[int] = mapped_column(primary_key=True)
        profile: Mapped["Profile"] = relationship(back_populates="user")

    class Profile(base):  # type: ignore[valid-type,misc]
        __tablename__ = "p"
        id: Mapped[int] = mapped_column(primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("u.id"))
        user: Mapped[User] = relationship(back_populates="profile")


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (
            _nullable_many_to_one,
            r"Hero\.team can be None, since team_id is nullable.*Related\[Team \| None\]",
        ),
        (_string_annotation, r"Hero\.team can be None, since team_id is nullable"),
        (_one_to_one_parent_side, r"User\.profile can be None, since there may be no related row"),
    ],
    ids=["many-to-one", "string-annotation", "one-to-one"],
)
def test_a_relationship_that_can_be_none_must_say_so(
    base: type[Model], build: Callable[[type[Model]], None], message: str
):
    build(base)
    with pytest.raises(TypeError, match=message):
        base.registry.configure()
    # It stays failed, as SQLAlchemy's own configuration errors do.
    with pytest.raises(InvalidRequestError, match=message):
        base.registry.configure()


def test_annotations_that_allow_none_or_cannot_be_none_pass(base: type[Model]):
    class Hero(base):  # type: ignore[valid-type,misc]
        __tablename__ = "h"
        id: Mapped[int] = mapped_column(primary_key=True)
        maybe: Mapped[int | None] = mapped_column(ForeignKey("t.id"))
        always: Mapped[int] = mapped_column(ForeignKey("t.id"))
        quoted_union: Mapped["Team | None"] = relationship(foreign_keys=[maybe], viewonly=True)
        optional_team: Mapped[Optional["Team"]] = relationship(foreign_keys=[maybe], viewonly=True)
        quoted_mapped: "Mapped[Team | None]" = relationship(foreign_keys=[maybe], viewonly=True)
        required_team: Mapped["Team"] = relationship(foreign_keys=[always], viewonly=True)
        # Unresolvable forward reference
        unknowable_team: Mapped["Unknowable"] = relationship("Team", foreign_keys=[maybe], viewonly=True)  # type: ignore[name-defined]  # noqa: F821

    class Team(base):  # type: ignore[valid-type,misc]
        __tablename__ = "t"
        id: Mapped[int] = mapped_column(primary_key=True)
        heroes: Mapped[list[Hero]] = relationship(foreign_keys=[Hero.maybe], viewonly=True)

    base.registry.configure()
