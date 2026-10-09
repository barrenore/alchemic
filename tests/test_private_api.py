"""The private SQLAlchemy and Pydantic behaviour alchemic relies on, pinned in one place.

A removed name fails loudly at import anyway. These pin what could change
quietly: a field renamed inside a private tuple, an argument that stops being
settable, an event that starts running at another point. When one fails after
an upgrade, the assertion message names the code to revisit. The ``<2.1`` pin
on SQLAlchemy in ``pyproject.toml`` exists for these.
"""

import inspect

import pytest
from pydantic import BaseModel
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped as SAMapped
from sqlalchemy.orm import Mapper, RelationshipProperty
from sqlalchemy.orm import mapped_column as sa_mapped_column
from sqlalchemy.orm.base import DEFAULT_STATE_ATTR, _MappedAnnotationBase
from sqlalchemy.sql.base import _NoArg
from sqlalchemy.types import TypeEngine

from alchemic import Mapped, Model, Related, create_model_base, mapped_column, relationship


def test_mapped_column_records_init_and_default_where_alchemic_reads_them():
    """``_typing.mapped_column`` writes ``dataclasses_init``; ``_fields._declared`` reads both."""
    options = sa_mapped_column(default=None)._attribute_options
    assert options.dataclasses_default is None, "_fields._declared: default=None is no longer recorded"
    assert sa_mapped_column()._attribute_options.dataclasses_default is _NoArg.NO_ARG, "_fields._declared"
    replaced = options._replace(dataclasses_init=False)
    assert replaced.dataclasses_init is False, "_typing.mapped_column: dataclasses_init moved"


def test_init_false_set_after_construction_does_not_count_as_a_dataclass_argument():
    """``_typing.mapped_column`` sets ``init`` after construction to stay out of dataclass mode."""
    column = sa_mapped_column()
    column._attribute_options = column._attribute_options._replace(dataclasses_init=False)
    assert column._has_dataclass_arguments is False, "_typing.mapped_column: SQLAlchemy now re-checks"


def test_relationship_arguments_can_still_be_set_before_configuration():
    """``_relationships.link`` and ``_pairable`` read and set these."""
    rel = RelationshipProperty("Target")
    for name in ("secondary", "primaryjoin", "secondaryjoin", "foreign_keys", "remote_side"):
        arg = getattr(rel._init_args, name)
        assert not arg._is_populated(), f"_relationships: _init_args.{name}"
        arg.argument = "set"
        assert arg._is_populated(), f"_relationships: _init_args.{name}.argument is not settable"
    rel.back_populates = "other"


def test_mapper_props_and_get_property_do_not_configure():
    """``_relationships._relationships_of`` and ``link`` read these before configuration."""
    base = create_model_base()

    class Owner(base):  # type: ignore[valid-type,misc]
        __tablename__ = "owner"
        id: Mapped[int] = mapped_column(primary_key=True)
        items: Related[list["NotYetDefined"]] = relationship()  # type: ignore[name-defined]  # noqa: F821

    mapper: Mapper[object] = Owner.__mapper__
    assert isinstance(mapper._props["items"], RelationshipProperty), "_relationships._relationships_of"
    assert inspect.signature(Mapper.get_property).parameters["_configure_mappers"].default is False, "link"
    # Inspect property on unconfigured mapper
    mapper.get_property("items")
    assert mapper.configured is False
    base.registry.dispose()


def test_a_mapper_marked_unconfigured_is_revisited_and_stays_failed():
    """``_relationships.check_relationship_optionality`` relies on this to make its error sticky."""
    base = create_model_base()

    class Parent(base):  # type: ignore[valid-type,misc]
        __tablename__ = "parent"
        id: Mapped[int] = mapped_column(primary_key=True)

    class Child(base):  # type: ignore[valid-type,misc]
        __tablename__ = "child"
        id: Mapped[int] = mapped_column(primary_key=True)
        parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("parent.id"))
        parent: Mapped[Parent] = relationship()

    with pytest.raises(TypeError):
        base.registry.configure()
    with pytest.raises(Exception, match="failed to initialize"):
        base.registry.configure()
    base.registry.dispose()


def test_remaining_names_alchemic_reads():
    assert DEFAULT_STATE_ATTR == "_sa_instance_state", "Model.__setattr__ and Model._assign"
    assert issubclass(SAMapped, _MappedAnnotationBase), "_relationships._allows_none"
    assert hasattr(TypeEngine, "_cached_bind_processor"), "_typing.ColumnTypeFor (typing only)"
    assert hasattr(Model.__pydantic_decorators__, "model_validators"), "Model._validated"
    assert "__eq__" in vars(BaseModel), "model._compare_table_rows_by_identity"


def test_after_transaction_end_is_the_last_step_of_closing_the_outermost_transaction():
    """``_receivers.after_transaction_end`` runs receivers there, and lets them raise."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    observed: list[tuple[object, bool]] = []

    def end(session: Session, transaction: object) -> None:
        observed.append((session.get_transaction(), transaction.parent is None))  # type: ignore[attr-defined]
        raise RuntimeError("from after_transaction_end")

    with Session(engine) as session:
        session.begin()
        from sqlalchemy import event

        event.listen(session, "after_transaction_end", end)
        with pytest.raises(RuntimeError):
            session.commit()
        assert observed == [(None, True)], "the transaction is detached before the event"
        event.remove(session, "after_transaction_end", end)
        session.begin()  # and the session can start another
        session.commit()
    engine.dispose()


def test_greenlet_helpers_alchemic_uses():
    from sqlalchemy.util.concurrency import await_only, greenlet_spawn, in_greenlet

    assert not in_greenlet(), "_receivers._dispatch: outside greenlet_spawn"
    assert callable(await_only) and callable(greenlet_spawn), "_receivers._dispatch, Model.load"
