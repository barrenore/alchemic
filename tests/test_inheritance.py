from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, declared_attr

from alchemic import Mapped, Model, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class AbstractVersioned(Model):
    __abstract__ = True
    version: Mapped[int] = mapped_column(default=1)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": cls.version}


class Document(AbstractVersioned, TimestampMixin):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]


class Employee(AbstractVersioned):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]
    name: Mapped[str]

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"polymorphic_on": "type", "polymorphic_identity": "emp"}


class Manager(Employee):
    __tablename__ = "managers"
    id: Mapped[int] = mapped_column(sa.ForeignKey("employees.id"), primary_key=True)
    department: Mapped[str] = mapped_column(default="General")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"polymorphic_identity": "mgr"}


@pytest.fixture
def session(sqlite_engine):
    with Session(sqlite_engine) as session:
        yield session


def test_abstract_model_and_mixin(session: Session):
    doc = Document(id=1, title="Report")
    assert isinstance(doc.created_at, datetime)
    assert doc.version == 1

    session.add(doc)
    session.commit()

    reloaded = session.execute(sa.select(Document).where(Document.id == 1)).scalar_one()
    assert reloaded.title == "Report"
    assert reloaded.version == 1


def test_mapper_args_composition_and_polymorphism(session: Session):
    emp_mapper = Employee.__mapper__
    assert emp_mapper.version_id_col is not None
    assert emp_mapper.version_id_col.key == "version"
    assert emp_mapper.polymorphic_on is not None

    mgr_mapper = Manager.__mapper__
    assert mgr_mapper.version_id_col is not None
    assert mgr_mapper.polymorphic_identity == "mgr"

    emp = Employee(id=1, name="Alice", type="emp")
    mgr = Manager(id=2, name="Bob", type="mgr", department="Engineering")
    session.add_all([emp, mgr])
    session.commit()

    all_employees = session.execute(sa.select(Employee).order_by(Employee.id)).scalars().all()
    assert len(all_employees) == 2
    assert isinstance(all_employees[0], Employee)
    assert isinstance(all_employees[1], Manager)
    assert all_employees[1].department == "Engineering"

    doc = Document(id=10, title="Initial")
    session.add(doc)
    session.commit()
    assert doc.version == 1

    doc.title = "Updated"
    session.commit()
    assert doc.version == 2


def test_classvar_mapper_args_composes_instead_of_shadowing():
    """A child assigning ``__mapper_args__`` keeps the parent's, including ``version_id_col``."""
    from typing import ClassVar

    from alchemic import create_model_base

    Base = create_model_base(name="ComposeBase")

    class Versioned(Base):  # type: ignore[misc,valid-type]
        __abstract__ = True
        id: Mapped[int] = mapped_column(primary_key=True)
        version: Mapped[int] = mapped_column(default=1)

        @declared_attr.directive
        def __mapper_args__(cls) -> dict[str, Any]:
            return {"version_id_col": cls.__table__.c.version}

    class Plain(Versioned):
        __tablename__ = "compose_plain"
        label: Mapped[str] = mapped_column(sa.String(20))

    class WithClassVar(Versioned):
        __tablename__ = "compose_classvar"
        # The incompatible override pyright reports is the pattern under test.
        __mapper_args__: ClassVar[dict] = {  # pyright: ignore[reportIncompatibleVariableOverride]
            "confirm_deleted_rows": False
        }
        label: Mapped[str] = mapped_column(sa.String(20))

    # the parent's contribution survives on both
    assert Plain.__mapper__.version_id_col is not None
    assert WithClassVar.__mapper__.version_id_col is not None
    # and the child's own is applied, not discarded
    assert Plain.__mapper__.confirm_deleted_rows is True
    assert WithClassVar.__mapper__.confirm_deleted_rows is False


def test_fields_accumulate_down_a_three_level_chain():
    """Two abstract levels, then a concrete one."""

    from alchemic import create_model_base

    Base = create_model_base(name="ChainBase")

    class L1(Base):  # type: ignore[misc,valid-type]
        __abstract__ = True
        id: Mapped[int] = mapped_column(primary_key=True)

    class L2(L1):
        __abstract__ = True
        project_id: Mapped[int] = mapped_column(default=0)

    class Leaf(L2):
        __tablename__ = "chain_leaf"
        name: Mapped[str] = mapped_column(sa.String(20))

    assert {"id", "project_id", "name"} <= set(Leaf.__table__.c.keys())
    assert set(Leaf.model_fields) == {"id", "project_id", "name"}
    leaf = Leaf(id=1, name="x")
    assert leaf.project_id == 0  # inherited default still applies
