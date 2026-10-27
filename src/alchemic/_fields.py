"""Reconcile a mapped class's Pydantic fields with its SQLAlchemy columns.

Relationships and other non-column attributes are removed from the fields.
"""

from collections.abc import Iterable, Iterator, Mapping
from decimal import Decimal
from enum import Enum
from types import MappingProxyType, NoneType, UnionType
from typing import Annotated, Any, NewType, TypeIs, Union, get_args, get_origin

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo as PydanticFieldInfo
from pydantic_core import PydanticUndefined
from sqlalchemy import Column, Numeric, String
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import InstrumentedAttribute, Mapped, MappedColumn, Mapper

from alchemic._constants import (
    ATTR_DECLARED_DEFAULTS,
    ATTR_NO_INIT,
    ATTR_NON_COLUMN_ANNOTATIONS,
    ATTR_NON_COLUMNS,
    ATTR_READONLY,
    FIELD_INFO_PASSTHROUGH_ATTRS,
    INFO_READONLY,
    INFO_VERSION,
)
from alchemic._metadata import extract_metadata_items, normalize_meta_dict
from alchemic._sqlalchemy_private import NO_ARG, attribute_options
from alchemic.types import ScaledDecimal

__all__ = ["Declared", "declared_defaults", "find_column", "is_mapped_column", "repair_model_fields"]


class Declared(Enum):
    """How a column's declaration makes it a constructor argument."""

    REQUIRED = "required"  # no ``default=``
    DEFAULT = "default"  # ``default=...``, ``default=None`` included
    NO_INIT = "no_init"  # ``init=False``: not a constructor argument


def is_mapped_column(value: object) -> TypeIs[MappedColumn[Any]]:
    """Whether ``value`` is a ``mapped_column()`` not yet consumed by mapping."""
    return isinstance(value, MappedColumn)


def _declared(column: MappedColumn[Any]) -> Declared:
    options = attribute_options(column)
    if options.dataclasses_init is False:
        return Declared.NO_INIT
    if options.dataclasses_default is not NO_ARG:
        return Declared.DEFAULT
    return Declared.REQUIRED


def _declared_in(klass: type) -> dict[str, Declared]:
    """``declared_defaults`` as seen from an existing class, nearest declaration first.

    A ``ModelMeta`` class carries its record; any other class is scanned for ``MappedColumn`` objects.
    """
    view: dict[str, Declared] = {}
    for ancestor in klass.__mro__:
        for name, flag in ancestor.__dict__.get(ATTR_DECLARED_DEFAULTS, {}).items():
            view.setdefault(name, flag)
        for name, value in ancestor.__dict__.items():
            if is_mapped_column(value):
                view.setdefault(name, _declared(value))
    return view


def declared_defaults(bases: tuple[type, ...], namespace: Mapping[str, Any]) -> Mapping[str, Declared]:
    """For each declared column, how its ``mapped_column()`` call makes it a constructor argument.

    Call before the class is created, which consumes the ``MappedColumn`` objects.
    """
    view = {name: _declared(value) for name, value in namespace.items() if is_mapped_column(value)}
    for base in bases:
        for name, flag in _declared_in(base).items():
            view.setdefault(name, flag)
    return MappingProxyType(view)


def _default_factory_from(column: Any) -> Any:
    """Wrap a SQLAlchemy callable default, with or without a context parameter, as a no-argument factory."""
    fn = column.default.arg

    def factory() -> Any:
        try:
            return fn()
        except TypeError:
            return fn(None)

    return factory


def find_column(cls: type, name: str) -> Any | None:
    """Return the ``Column`` mapped to attribute ``name``, or ``None`` if it maps none.

    Safe before the mapper is configured. Under joined inheritance it is the subclass's own column.
    """
    mapper: Mapper[Any] | None = sa_inspect(cls, raiseerr=False)
    if mapper is None or name not in mapper.columns:
        return None
    column = getattr(cls, name).property.columns[0]
    return column if isinstance(column, Column) else None


def _unwrap_mapped_annotation(field: PydanticFieldInfo) -> bool:
    """Replace ``Mapped[T]`` in ``field.annotation`` with ``T``, as a plain ``BaseModel`` field would read.

    An ``Annotated`` ``T`` is unwrapped too, its extras added to ``field.metadata``.
    Returns ``True`` if the annotation was rewritten.
    """
    if get_origin(field.annotation) is not Mapped:
        return False

    inner = get_args(field.annotation)
    if not inner:
        return False

    unwrapped = inner[0]
    if get_origin(unwrapped) is Annotated:
        parts = get_args(unwrapped)
        unwrapped = parts[0]
        field.metadata = [*field.metadata, *parts[1:]]
    field.annotation = unwrapped
    return True


def _resolve_column_default(field: PydanticFieldInfo, column: Any, has_default: bool) -> bool:
    """Rebuild the Pydantic default: required unless declared, valued from the ``Column``.

    Returns ``True`` if the field changed.
    """
    if not has_default:
        field.default = PydanticUndefined
        field.default_factory = None
    elif column.default is not None and column.default.is_callable:
        field.default = PydanticUndefined
        field.default_factory = _default_factory_from(column)
    else:
        field.default = None if column.default is None else column.default.arg
        field.default_factory = None
    return True


def _value_type(annotation: Any) -> Any:
    """The type under ``T | None`` and any ``NewType``: ``str`` for ``Name | None``."""
    if get_origin(annotation) in (Union, UnionType):
        members = [arg for arg in get_args(annotation) if arg is not NoneType]
        if len(members) != 1:
            return None
        annotation = members[0]
    while isinstance(annotation, NewType):
        annotation = annotation.__supertype__
    return annotation


def _declared_limits(metadata: Iterable[Any], constraint: str) -> Iterator[int]:
    """Each value of ``constraint`` in ``metadata``, including inside a nested ``Field()``."""
    for item in metadata:
        if isinstance(item, PydanticFieldInfo):
            yield from _declared_limits(item.metadata, constraint)
        elif (value := getattr(item, constraint, None)) is not None:
            yield value


def _tighten(field: PydanticFieldInfo, constraint: str, limit: int, enforced: PydanticFieldInfo) -> bool:
    """Make ``field`` enforce ``constraint <= limit``, unless it already enforces as much.

    ``enforced`` is ``Field(constraint=limit)``, whose metadata is appended.
    """
    declared = list(_declared_limits(field.metadata, constraint))
    if declared and min(declared) <= limit:
        return False
    field.metadata.extend(enforced.metadata)
    return True


def _constrain_to_column_type(field: PydanticFieldInfo, column: Any) -> bool:
    """Validate the limits the column type sets: a ``String``'s length, a ``Numeric``'s digits.

    A ``ScaledDecimal`` gives the same digit limits as a ``Numeric`` of its precision and scale.
    Only a ``str`` or ``Decimal`` value, or a ``NewType`` of one, is constrained.
    Returns ``True`` if the field changed.
    """
    value_type = _value_type(field.annotation)
    column_type = column.type
    if not isinstance(value_type, type):
        return False
    if isinstance(column_type, String):
        if column_type.length is None or not issubclass(value_type, str) or issubclass(value_type, Enum):
            return False
        length = column_type.length
        return _tighten(field, "max_length", length, Field(max_length=length))
    if isinstance(column_type, (Numeric, ScaledDecimal)) and issubclass(value_type, Decimal):
        changed = False
        if (digits := column_type.precision) is not None:
            changed = _tighten(field, "max_digits", digits, Field(max_digits=digits)) or changed
        if (places := column_type.scale) is not None:
            changed = _tighten(field, "decimal_places", places, Field(decimal_places=places)) or changed
        return changed
    return False


def is_readonly(cls: type, column: Any) -> bool:
    """Whether ``column`` is ``readonly``: declared so, or a version SQLAlchemy generates."""
    declared = column.info.get(INFO_READONLY)
    if declared is not None:
        return bool(declared)
    if not column.info.get(INFO_VERSION):
        return False
    mapper: Mapper[Any] | None = sa_inspect(cls, raiseerr=False)
    return mapper is None or mapper.version_id_generator is not False


def _sync_field_metadata(
    field: PydanticFieldInfo, column: Any, declared_annotation: Any, readonly: bool = False
) -> bool:
    """Copy ``Annotated`` metadata into ``column.info``, and ``column.info`` into the JSON schema.

    ``Field()`` attributes Pydantic drops from a nested ``Annotated`` are set on ``field``.
    Returns ``True`` if the field changed.
    """
    changed = False

    meta_items = list(field.metadata)
    meta_items.extend(extract_metadata_items(declared_annotation))

    for meta in meta_items:
        plain = normalize_meta_dict(meta)
        if plain is not None:
            column.info.update(plain)
        elif isinstance(meta, PydanticFieldInfo):
            for attribute in FIELD_INFO_PASSTHROUGH_ATTRS:
                value = getattr(meta, attribute, None)
                if value is not None and getattr(field, attribute, None) is None:
                    setattr(field, attribute, value)
                    changed = True

    schema_extra = {
        key: value for key, value in column.info.items() if key not in (INFO_READONLY, INFO_VERSION)
    }
    if readonly:
        schema_extra["readOnly"] = True
    if schema_extra:
        extra = field.json_schema_extra
        if extra is None:
            field.json_schema_extra = schema_extra
            changed = True
        elif not callable(extra):
            extra.update(schema_extra)
            changed = True

    return changed


def _prune_non_column_fields(
    cls: type, fields: dict[str, PydanticFieldInfo], non_column_names: set[str], annotations: dict[str, Any]
) -> bool:
    """Drop non-column fields from the Pydantic field set, their names and annotations recorded.

    Returns ``True`` if any field was removed.
    """
    changed = False
    for name in list(fields):
        if find_column(cls, name) is None:
            annotations[name] = fields.pop(name).annotation
            non_column_names.add(name)
            changed = True
    return changed


def _collect_unannotated_descriptors(cls: type, non_column_names: set[str]) -> None:
    """Record any class-level ``InstrumentedAttribute``s that are not columns."""
    for base in cls.__mro__:
        for attr_name, attr_val in base.__dict__.items():
            if isinstance(attr_val, InstrumentedAttribute) and find_column(cls, attr_name) is None:
                non_column_names.add(attr_name)


def repair_model_fields(cls: type[BaseModel], declared: Mapping[str, Declared]) -> None:
    """Reconcile Pydantic fields with SQLAlchemy columns on a mapped table class.

    ``declared`` is this class's ``declared_defaults``; it is recorded even on a class without a table.
    """
    setattr(cls, ATTR_DECLARED_DEFAULTS, declared)
    setattr(
        cls,
        ATTR_NO_INIT,
        frozenset(name for name, declared_as in declared.items() if declared_as is Declared.NO_INIT),
    )
    table = getattr(cls, "__table__", None)
    if table is None:
        return

    # The mapper must not be configured here: a relationship may name a class not defined yet.
    fields = cls.model_fields
    non_column_names: set[str] = set()
    annotations: dict[str, Any] = {}
    readonly: set[str] = set()
    changed = _prune_non_column_fields(cls, fields, non_column_names, annotations)

    for name, field in fields.items():
        column = find_column(cls, name)
        field_is_readonly = column is not None and is_readonly(cls, column)
        if field_is_readonly:
            readonly.add(name)

        declared_annotation = field.annotation

        changed = _unwrap_mapped_annotation(field) or changed
        has_default = declared.get(name, Declared.REQUIRED) is not Declared.REQUIRED
        changed = _resolve_column_default(field, column, has_default) or changed
        changed = _sync_field_metadata(field, column, declared_annotation, field_is_readonly) or changed
        changed = _constrain_to_column_type(field, column) or changed

    _collect_unannotated_descriptors(cls, non_column_names)

    setattr(cls, ATTR_NON_COLUMNS, frozenset(non_column_names))
    setattr(cls, ATTR_NON_COLUMN_ANNOTATIONS, MappingProxyType(annotations))
    setattr(cls, ATTR_READONLY, frozenset(readonly))

    if changed:
        cls.model_rebuild(force=True)
