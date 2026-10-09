"""Tests for internal helpers and edge-case paths in ``alchemic.model``."""

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, declared_attr

from alchemic import Mapped, Model, create_model_base, mapped_column
from alchemic.model import _eval_mapper_args

# --- _eval_mapper_args branch matrix ---


def test_eval_mapper_args_none():
    assert _eval_mapper_args(Model, None) == {}


def test_eval_mapper_args_plain_mapping():
    assert _eval_mapper_args(Model, {"polymorphic_on": "x"}) == {"polymorphic_on": "x"}


def test_eval_mapper_args_callable():
    def _args(cls):
        return {"foo": 1}

    assert _eval_mapper_args(Model, _args) == {"foo": 1}


def test_eval_mapper_args_callable_returns_non_dict():
    def _args(cls):
        return "oops-not-a-dict"

    assert _eval_mapper_args(Model, _args) == {}


def test_eval_mapper_args_unknown_object():
    assert _eval_mapper_args(Model, 42) == {}


def test_eval_mapper_args_declared_attr():
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": "v"}

    # A declared_attr instance carries a callable descriptor.
    assert _eval_mapper_args(Model, __mapper_args__) == {"version_id_col": "v"}


def test_eval_mapper_args_fget_property():
    class _Prop:
        @property
        def __mapper_args__(cls) -> dict[str, Any]:
            return {"order_by": "id"}

    assert _eval_mapper_args(Model, _Prop.__dict__["__mapper_args__"]) == {"order_by": "id"}


# --- non-table abstraction: manager_of_class raises UnmappedClassError ---


def test_non_table_abstract_model_construct_raises():
    class Bare(Model):
        __abstract__ = True

    from sqlalchemy.orm.exc import UnmappedClassError

    with pytest.raises(UnmappedClassError):
        Bare()


# --- Model.__delattr__ non-descriptor fallthrough ---


def test_instance_delattr_non_descriptor_falls_back():
    class DeleteModel(Model):
        __tablename__ = "delete_model"
        id: Mapped[int] = mapped_column(primary_key=True)
        extra: Mapped[str | None] = mapped_column(default=None)

    instance = DeleteModel(id=1, extra="x")
    instance._scratch = 5
    del instance._scratch
    assert not hasattr(instance, "_scratch")


# --- loader state ---


def test_new_instance_has_pydantic_state_before_init():
    """Pydantic state is initialized on ``__new__`` prior to ``__init__``."""

    class LoaderModel(Model):
        __tablename__ = "loader_model"
        id: Mapped[int] = mapped_column(primary_key=True)

    target = LoaderModel.__new__(LoaderModel)
    assert object.__getattribute__(target, "__pydantic_fields_set__") == set()
    assert object.__getattribute__(target, "__pydantic_extra__") is None
    assert object.__getattribute__(target, "__pydantic_private__") is None


# --- create_model_base paths ---


def test_create_model_base_with_registry():
    from sqlalchemy.orm import DeclarativeBase

    class Foreign(DeclarativeBase):
        pass

    Base = create_model_base(registry=Foreign.registry, name="RegBase")
    assert Base.metadata is Foreign.metadata


def test_create_model_base_without_metadata_or_registry_isolate():
    Meta1 = create_model_base(name="MetaA")
    Meta2 = create_model_base(name="MetaB")

    class T1(Meta1):  # type: ignore[misc,valid-type]
        __tablename__ = "t1_reg"
        id: Mapped[int] = mapped_column(primary_key=True)

    assert "t1_reg" in Meta1.metadata.tables
    assert "t1_reg" not in Meta2.metadata.tables


def test_sync_round_trip_with_shared_session(sqlite_engine):
    class SessModel(Model):
        __tablename__ = "sess_model"
        id: Mapped[int] = mapped_column(primary_key=True)
        val: Mapped[str] = mapped_column(sa.String(20))

    # Create tables for dynamically declared model.
    SessModel.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        session.add(SessModel(id=1, val="hi"))
        session.commit()
        got = session.execute(sa.select(SessModel)).scalar_one()
        assert got.val == "hi"
