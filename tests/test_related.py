"""``Related``, ``link``, reverse pairing and ``prefetch``, at runtime.

``test_typing.py`` covers the static half: wrong paths, roots and links are type
errors. Models here live in their own registry, so their names cannot clash with
other test modules', and each error case builds and disposes of another one.
"""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from alchemic import (
    Mapped,
    Model,
    Related,
    Session,
    create_model_base,
    link,
    mapped_column,
    path,
    prefetch,
    references,
    relationship,
)

Base = create_model_base(name="RelatedBase")


class Team(Base):  # type: ignore[valid-type,misc]
    __tablename__ = "rel_teams"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    heroes: Related[list["Hero"]] = relationship()


class Hero(Base):  # type: ignore[valid-type,misc]
    __tablename__ = "rel_heroes"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    team_id: Mapped[int | None] = mapped_column(references(Team.id), default=None)
    team: Related[Team | None] = relationship()
    powers: Related[list["Power"]] = relationship()
    following: Related[list["Hero"]] = relationship()
    followers: Related[list["Hero"]] = relationship()
    tags: Related[set["Tag"]] = relationship()


class Tag(Base):  # type: ignore[valid-type,misc]
    __tablename__ = "rel_tags"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    label: Mapped[str]
    heroes: Related[set[Hero]] = relationship()


class Power(Base):  # type: ignore[valid-type,misc]
    __tablename__ = "rel_powers"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    heroes: Related[list[Hero]] = relationship()


hero_power = link(Hero.powers, Power.heroes)
hero_tag = link(Hero.tags, Tag.heroes)
follows = link(Hero.following, Hero.followers, name="rel_follows", prefixes=("follower", "followed"))


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        red, fly = Team(name="red"), Power(name="fly")
        ann = Hero(name="ann", team=red, powers=[fly])
        bea = Hero(name="bea", team=red)
        ann.following.append(bea)
        session.add(red)
        session.commit()
    yield engine
    engine.dispose()


def test_link_builds_the_table():
    assert hero_power.name == "rel_heroes_rel_powers"
    assert [column.name for column in hero_power.c] == ["hero_id", "power_id"]
    assert [column.name for column in follows.primary_key] == ["follower_id", "followed_id"]
    assert {fk.ondelete for fk in hero_power.foreign_key_constraints} == {"CASCADE"}


def test_both_sides_stay_in_step_without_back_populates():
    red, fly = Team(name="red"), Power(name="fly")
    ann = Hero(name="ann", team=red, powers=[fly])
    bea = Hero(name="bea")
    bea.team = red
    ann.following.append(bea)
    assert red.heroes == [ann, bea]
    assert fly.heroes == [ann]
    assert bea.followers == [ann]
    brave = Tag(label="brave")
    ann.tags.add(brave)
    assert brave.heroes == {ann}


def test_prefetch_loads_every_step_up_front(engine: sa.Engine):
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    with Session(engine) as session:
        stmt = Team.where(
            Team.name == "red", prefetch=[path(Team.heroes, Hero.powers), path(Team.heroes, Hero.followers)]
        )
        team = session.exec(stmt).one()
        session.expunge_all()
    loaded = {
        hero.name: ([power.name for power in hero.powers], [follower.name for follower in hero.followers])
        for hero in team.heroes
    }
    assert loaded == {"ann": (["fly"], []), "bea": ([], ["ann"])}
    assert len(statements) == 4  # teams, heroes, powers, followers


def test_prefetch_joins_in_a_single_object(engine: sa.Engine):
    with Session(engine) as session:
        hero = session.get_one(Hero, 1, options=prefetch(Hero, Hero.team))
        assert "team" in hero.__dict__
        assert hero.team is not None and hero.team.name == "red"


def test_prefetch_refuses_a_path_from_another_model():
    with pytest.raises(TypeError, match="Hero.powers does not start at Team"):
        prefetch(Team, Hero.powers)  # type: ignore[arg-type]


def test_path_takes_two_steps_or_more():
    with pytest.raises(TypeError, match="two or more"):
        path(Team.heroes)  # type: ignore[call-overload]


@pytest.fixture
def base() -> Iterator[type[Model]]:
    base = create_model_base()
    yield base
    base.registry.dispose()


def test_an_ambiguous_reverse_must_be_named(base: type[Model]):
    class Club(base):  # type: ignore[valid-type,misc]
        __tablename__ = "club"
        id: Mapped[int] = mapped_column(primary_key=True)
        members: Related[list["Member"]] = relationship()

    class Member(base):  # type: ignore[valid-type,misc]
        __tablename__ = "member"
        id: Mapped[int] = mapped_column(primary_key=True)
        club_id: Mapped[int | None] = mapped_column(references(Club.id))
        club: Related[Club | None] = relationship()
        same_club: Related[Club | None] = relationship()

    with pytest.raises(TypeError, match=r"could pair with .*Name each pair with back_populates"):
        base.registry.configure()


def test_a_named_pair_and_a_viewonly_are_left_alone(base: type[Model]):
    class Club(base):  # type: ignore[valid-type,misc]
        __tablename__ = "club"
        id: Mapped[int] = mapped_column(primary_key=True)
        members: Related[list["Member"]] = relationship(back_populates="club")

    class Member(base):  # type: ignore[valid-type,misc]
        __tablename__ = "member"
        id: Mapped[int] = mapped_column(primary_key=True)
        club_id: Mapped[int | None] = mapped_column(references(Club.id))
        club: Related[Club | None] = relationship()
        also_club: Related[Club | None] = relationship(viewonly=True)

    base.registry.configure()
    assert _back(Member.club) == "members"
    assert _back(Member.also_club) is None


def test_targets_given_as_a_class_a_lambda_or_a_mapper_pair_too(base: type[Model]):
    class Club(base):  # type: ignore[valid-type,misc]
        __tablename__ = "club"
        id: Mapped[int] = mapped_column(primary_key=True)
        members: Related[list["Member"]] = relationship(lambda: Member)

    class Member(base):  # type: ignore[valid-type,misc]
        __tablename__ = "member"
        id: Mapped[int] = mapped_column(primary_key=True)
        club_id: Mapped[int | None] = mapped_column(references(Club.id))
        club: Related[Club | None] = relationship(Club)
        badges: Related[list["Badge"]] = relationship()

    class Badge(base):  # type: ignore[valid-type,misc]
        __tablename__ = "badge"
        id: Mapped[int] = mapped_column(primary_key=True)
        member_id: Mapped[int | None] = mapped_column(references(Member.id))
        member: Related[Member | None] = relationship(sa.inspect(Member))

    base.registry.configure()
    assert (_back(Club.members), _back(Member.club)) == ("club", "members")
    assert (_back(Badge.member), _back(Member.badges)) == ("badges", "member")


def _back(attr: Any) -> str | None:
    return attr.property.back_populates


def test_a_target_not_defined_yet_is_not_resolved():
    from alchemic._relationships import _target

    not_yet = SimpleNamespace(argument=lambda: Undefined)  # type: ignore[name-defined]  # noqa: F821
    assert _target(cast(Any, not_yet), []) is None


def test_link_refuses_what_it_cannot_wire(base: type[Model]):
    class Node(base):  # type: ignore[valid-type,misc]
        __tablename__ = "node"
        id: Mapped[int] = mapped_column(primary_key=True)
        out: Related[list["Node"]] = relationship()
        into: Related[list["Node"]] = relationship()

    with pytest.raises(TypeError, match="needs prefixes"):
        link(Node.out, Node.into)
    with pytest.raises(TypeError, match="takes relationships"):
        link(Node.id, Node.into)  # type: ignore[arg-type]
    link(Node.out, Node.into, prefixes=("source", "target"))
    base.registry.configure()
    with pytest.raises(TypeError, match="before mappers are configured"):
        link(Node.out, Node.into, prefixes=("a", "b"))
