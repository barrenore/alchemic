"""A model instance is a row: equal only to itself, and hashable."""

from sqlalchemy import String

from alchemic import Mapped, Model, Related, Session, mapped_column, references, relationship


class IdSquad(Model):
    __tablename__ = "identity_squads"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    members: Related[list["IdMember"]] = relationship()


class IdMember(Model):
    __tablename__ = "identity_members"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str] = mapped_column(String(20))
    squad_id: Mapped[int | None] = mapped_column(references(IdSquad.id), default=None)
    squad: Related[IdSquad | None] = relationship()


class ValueEqual(Model):
    """Defines its own equality, which is kept."""

    __tablename__ = "identity_value_equal"
    id: Mapped[int] = mapped_column(primary_key=True)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ValueEqual) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)


def test_instances_with_the_same_fields_are_different_rows():
    first, second = IdMember(name="x"), IdMember(name="x")
    assert first != second
    squad = IdSquad(members=[first, second])
    squad.members.remove(second)
    assert squad.members[0] is first


def test_instances_are_hashable_by_identity():
    first, second = IdMember(name="x"), IdMember(name="x")
    assert len({first, second, first}) == 2
    assert {first: 1}[first] == 1


def test_hash_survives_the_primary_key_being_assigned(sqlite_engine):
    member = IdMember(name="x")
    members = {member}
    with Session(sqlite_engine) as session:
        session.add(member)
        session.commit()
        assert member.id is not None
        assert member in members


def test_the_same_row_in_a_session_is_the_same_object(sqlite_engine):
    with Session(sqlite_engine) as session:
        session.add(IdMember(name="x"))
        session.commit()
        first = session.get_one(IdMember, 1)
        assert session.get_one(IdMember, 1) == first


def test_an_equality_the_model_defines_is_kept():
    assert ValueEqual(id=1) == ValueEqual(id=1)
    assert len({ValueEqual(id=1), ValueEqual(id=1)}) == 1
