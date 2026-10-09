"""Tests for the field-repair internals in ``alchemic._fields``."""

import pytest
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from alchemic._fields import (
    _default_factory_from,
    _resolve_column_default,
    _sync_field_metadata,
    _unwrap_mapped_annotation,
    find_column,
)
from alchemic.extensions import Extension


class _FakeColumn:
    """A stand-in exposing just enough of SQLAlchemy's ``Column`` API."""

    def __init__(self, *, default=None, server_default=None, nullable=False):
        self.default = default
        self.server_default = server_default
        self.nullable = nullable
        self.info = {}


def test_default_factory_from_zero_arg():
    def _zero():
        return 7

    col = _FakeColumn(default=_TypeC(default=_zero, is_callable=True))
    assert _default_factory_from(col)() == 7


def test_default_factory_from_context_arg():
    """A callable default that takes a context argument is called with ``None``."""

    def _ctx(ctx):
        return 3 if ctx is None else ctx

    col = _FakeColumn(default=_TypeC(default=_ctx, is_callable=True))
    assert _default_factory_from(col)() == 3


class _TypeC:
    """Mimic ``SQLAlchemy ColumnDefault.arg/.is_callable``."""

    def __init__(self, *, default, is_callable):
        self.arg = default
        self.is_callable = is_callable


def test_unwrap_mapped_annotation():
    from sqlalchemy.orm import Mapped

    field = FieldInfo(annotation=Mapped[int])
    assert _unwrap_mapped_annotation(field) is True
    assert field.annotation is int


def test_unwrap_mapped_nested_annotated():
    from typing import Annotated

    from sqlalchemy.orm import Mapped

    field = FieldInfo(annotation=Mapped[Annotated[int, Extension(editable=False)]])
    assert _unwrap_mapped_annotation(field) is True
    assert field.annotation is int
    assert field.metadata and isinstance(field.metadata[0], Extension)


def test_unwrap_non_mapped_returns_false():
    field = FieldInfo(annotation=int)
    assert _unwrap_mapped_annotation(field) is False


def test_resolve_column_default_callable():
    field = FieldInfo(annotation=int)
    col = _FakeColumn(default=_TypeC(default=lambda: 5, is_callable=True))
    assert _resolve_column_default(field, col, has_default=True) is True
    assert field.default is PydanticUndefined
    factory = field.default_factory
    assert factory is not None
    assert _default_factory_from(col)() == 5
    assert callable(factory)


def test_resolve_column_default_plain():
    field = FieldInfo(annotation=int)
    col = _FakeColumn(default=_TypeC(default=9, is_callable=False))
    assert _resolve_column_default(field, col, has_default=True) is True
    assert field.default == 9


def test_resolve_column_default_declared_none():
    """``default=None`` leaves no ``Column.default``, but the declaration had one."""
    field = FieldInfo(annotation=int)
    col = _FakeColumn(server_default="now()")
    assert _resolve_column_default(field, col, has_default=True) is True
    assert field.default is None


@pytest.mark.parametrize(
    "col",
    [_FakeColumn(), _FakeColumn(nullable=True), _FakeColumn(server_default="now()")],
    ids=["plain", "nullable", "server_default"],
)
def test_resolve_column_default_undeclared_is_required(col: "_FakeColumn"):
    """Without ``default=`` in the declaration, the column's shape does not matter."""
    field = FieldInfo(annotation=int)
    assert _resolve_column_default(field, col, has_default=False) is True
    assert field.default is PydanticUndefined
    assert field.default_factory is None


def test_sync_field_metadata_passthrough_attrs():
    from typing import Annotated

    from pydantic import Field

    field = FieldInfo(annotation=str)
    col = _FakeColumn()
    meta = Field(exclude=True, alias="foo")
    declared = Annotated[str, meta]
    assert _sync_field_metadata(field, col, declared) is True
    assert field.exclude is True
    assert field.alias == "foo"


def test_sync_field_metadata_column_info_to_json_extra():
    field = FieldInfo(annotation=str)
    col = _FakeColumn()
    col.info["editable"] = False
    assert _sync_field_metadata(field, col, None) is True
    assert field.json_schema_extra == {"editable": False}


def test_find_column_and_none():
    from alchemic import Mapped, Model, mapped_column

    class FinderModel(Model):
        __tablename__ = "finder_table"
        id: Mapped[int] = mapped_column(primary_key=True)

    found = find_column(FinderModel, "id")
    assert found is not None
    assert found.name == "id"
    assert find_column(FinderModel, "missing") is None


# --- columns whose name differs from their attribute ---


def test_renamed_column_is_a_validated_field():
    """``mapped_column("db_name")`` maps the attribute to a differently named column with validation."""
    from pydantic import ValidationError
    from sqlalchemy import String

    from alchemic import Mapped, Model, mapped_column

    class Renamed(Model):
        __tablename__ = "renamed_fields"
        id: Mapped[int] = mapped_column("renamed_id", primary_key=True)
        handle: Mapped[str] = mapped_column("handle_col", String(20), default="anon")

    assert set(Renamed.model_fields) == {"id", "handle"}
    assert Renamed.__alchemic_non_columns__ == frozenset()
    assert Renamed.model_fields["handle"].default == "anon"
    handle = find_column(Renamed, "handle")
    assert handle is not None
    assert handle.name == "handle_col"
    assert find_column(Renamed, "handle_col") is None
    with pytest.raises(ValidationError):
        Renamed(id=1, handle=5)  # type: ignore[arg-type]
    renamed = Renamed(id=1)
    with pytest.raises(ValidationError):
        renamed.update(handle=5)  # type: ignore[arg-type]
    assert renamed.handle == "anon"
    assert repr(renamed) == "<Renamed(id=1)>"


def test_joined_subclass_finds_its_own_column():
    """Both tables have ``id``. The subclass's own column carries its default."""
    from sqlalchemy import ForeignKey

    from alchemic import Mapped, Model, mapped_column

    class JoinedBase(Model):
        __tablename__ = "joined_find_base"
        id: Mapped[int] = mapped_column(primary_key=True)

    class JoinedChild(JoinedBase):
        __tablename__ = "joined_find_child"
        id: Mapped[int] = mapped_column(ForeignKey("joined_find_base.id"), primary_key=True)

    column = find_column(JoinedChild, "id")
    assert column is not None
    assert column.table.name == "joined_find_child"


def test_renamed_column_metadata_is_found_by_attribute():
    from typing import Annotated

    from alchemic import Mapped, Model, mapped_column
    from alchemic.extensions import get_field_metadata

    class RenamedMeta(Model):
        __tablename__ = "renamed_meta"
        id: Mapped[int] = mapped_column(primary_key=True)
        slug: Mapped[Annotated[str, Extension(editable=False)]] = mapped_column("slug_col")

    assert get_field_metadata(RenamedMeta, "slug") == {"editable": False}
