"""``references(Team.id)``: a foreign key the type checker checks against the annotation.

At runtime it is a plain ``ForeignKey``. ``test_typing.py`` covers the static half.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from alchemic import Mapped, Model, Related, Session, mapped_column, references, relationship, select


class RefTeam(Model):
    __tablename__ = "ref_teams"
    id: Mapped[int] = mapped_column(primary_key=True)


class RefNode(Model):
    __tablename__ = "ref_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(references(id), default=None)
    team_id: Mapped[int] = mapped_column("team_col", references(RefTeam.id, ondelete="CASCADE"))
    parent: Mapped["RefNode | None"] = relationship(remote_side=[id])
    team: Mapped[RefTeam] = relationship()


def test_references_render_as_foreign_keys():
    ddl = CreateTable(RefNode.__table__).compile().string  # type: ignore[arg-type]
    assert "FOREIGN KEY(parent_id) REFERENCES ref_nodes (id)" in ddl
    assert "FOREIGN KEY(team_col) REFERENCES ref_teams (id) ON DELETE CASCADE" in ddl


def test_references_join_through_the_orm(sqlite_engine):
    with Session(sqlite_engine) as session:
        team = RefTeam(id=1)
        root = RefNode(id=1, team=team, team_id=1)
        session.add_all([team, root, RefNode(id=2, team_id=1, parent=root)])
        session.commit()
        child = session.get_one(RefNode, 2)
        assert child.parent is root
        assert child.team is team


class RefGuild(Model):
    __tablename__ = "ref_guilds"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    members: Related[list["RefMember"]] = relationship()


class RefMember(Model):
    """A NOT NULL key, usually set through its relationship: ``default=None``."""

    __tablename__ = "ref_members"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    guild_id: Mapped[int] = mapped_column(references(RefGuild.id), default=None)
    guild: Related[RefGuild] = relationship()


def test_a_required_key_can_come_from_its_relationship_or_be_given(sqlite_engine):
    with Session(sqlite_engine) as session:
        guild = RefGuild()
        guild.members.append(RefMember())  # built with neither, set on append
        session.add_all([RefMember(guild=guild), guild])
        session.commit()
        session.add(RefMember(guild_id=guild.id))
        session.commit()
        assert [member.guild_id for member in session.exec(select(RefMember)).all()] == [guild.id] * 3


def test_a_required_key_given_neither_way_fails_on_flush(sqlite_engine):
    with Session(sqlite_engine) as session, pytest.raises(IntegrityError, match="NOT NULL"):
        session.add(RefMember())
        session.commit()
