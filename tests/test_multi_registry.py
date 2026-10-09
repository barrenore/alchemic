import sqlalchemy as sa
from sqlalchemy.orm import Session

from alchemic import Mapped, create_model_base, mapped_column


def test_create_model_base_isolated():
    meta1 = sa.MetaData()
    meta2 = sa.MetaData()

    Base1 = create_model_base(metadata=meta1, name="Base1")
    Base2 = create_model_base(metadata=meta2, name="Base2")

    class Model1(Base1):
        __tablename__ = "table_one"
        id: Mapped[int] = mapped_column(primary_key=True)
        val: Mapped[str]

    class Model2(Base2):
        __tablename__ = "table_two"
        id: Mapped[int] = mapped_column(primary_key=True)
        count: Mapped[int]

    assert "table_one" in meta1.tables
    assert "table_one" not in meta2.tables
    assert "table_two" in meta2.tables
    assert "table_two" not in meta1.tables

    # Verify both can be independently created in separate engines
    engine1 = sa.create_engine("sqlite://")
    engine2 = sa.create_engine("sqlite://")

    meta1.create_all(engine1)
    meta2.create_all(engine2)

    with Session(engine1) as s1:
        m1 = Model1(id=1, val="hello")
        s1.add(m1)
        s1.commit()
        res = s1.execute(sa.select(Model1)).scalar_one()
        assert res.val == "hello"

    with Session(engine2) as s2:
        m2 = Model2(id=1, count=42)
        s2.add(m2)
        s2.commit()
        res = s2.execute(sa.select(Model2)).scalar_one()
        assert res.count == 42


def test_same_table_name_in_two_registries_does_not_collide():
    """Separate registries must be genuinely separate.

    A shared process-global MetaData is a real footgun for a multi-tenant app --
    two modules defining ``User`` collide at import with
    ``Table 'user' is already defined``. Distinct bases must not.
    """
    from alchemic import Model

    Base1 = create_model_base(name="NameBase1")
    Base2 = create_model_base(name="NameBase2")

    class UserA(Base1):  # type: ignore[misc,valid-type]
        __tablename__ = "users"
        id: Mapped[int] = mapped_column(primary_key=True)
        email: Mapped[str]

    class UserB(Base2):  # type: ignore[misc,valid-type]
        __tablename__ = "users"
        id: Mapped[int] = mapped_column(primary_key=True)
        handle: Mapped[str]

    assert UserA.__table__ is not UserB.__table__
    assert set(UserA.__table__.c.keys()) == {"id", "email"}
    assert set(UserB.__table__.c.keys()) == {"id", "handle"}
    # and neither leaked into the default registry
    assert "users" not in Model.metadata.tables


def test_create_all_on_one_registry_ignores_the_other():
    Base1 = create_model_base(name="CreateBase1")
    Base2 = create_model_base(name="CreateBase2")

    class Only1(Base1):  # type: ignore[misc,valid-type]
        __tablename__ = "only_one"
        id: Mapped[int] = mapped_column(primary_key=True)

    class Only2(Base2):  # type: ignore[misc,valid-type]
        __tablename__ = "only_two"
        id: Mapped[int] = mapped_column(primary_key=True)

    engine = sa.create_engine("sqlite://")
    Base1.metadata.create_all(engine)
    tables = set(sa.inspect(engine).get_table_names())
    assert tables == {"only_one"}


def test_shares_a_registry_with_another_declarative_base():
    """An alchemic base can join a registry owned by a different declarative system."""
    from sqlalchemy.orm import DeclarativeBase

    from alchemic import relationship

    class Foreign(DeclarativeBase):
        pass

    Shared = create_model_base(registry=Foreign.registry, name="SharedBase")

    class Team(Foreign):
        __tablename__ = "shared_teams"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]
        heroes: Mapped[list["Hero"]] = relationship(back_populates="team")

    class Hero(Shared):  # type: ignore[misc,valid-type]
        __tablename__ = "shared_heroes"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(sa.String(50))
        team_id: Mapped[int | None] = mapped_column(sa.ForeignKey("shared_teams.id"), default=None)
        team: Mapped["Team | None"] = relationship(back_populates="heroes")

    assert Hero._sa_registry is Foreign.registry
    assert {"shared_teams", "shared_heroes"} <= set(Foreign.metadata.tables)

    engine = sa.create_engine("sqlite://")
    Foreign.metadata.create_all(engine)
    with Session(engine) as session:
        team = Team(id=1, name="Alpha")
        session.add_all([team, Hero(id=1, name="Rick", team_id=1)])
        session.commit()
    with Session(engine) as session:
        fetched_team = session.execute(sa.select(Team)).scalar_one()
        fetched_hero = session.execute(sa.select(Hero)).scalar_one()
        assert [hero.name for hero in fetched_team.heroes] == ["Rick"]  # foreign -> alchemic
        assert fetched_hero.team is not None
        assert fetched_hero.team.name == "Alpha"  # alchemic -> foreign
