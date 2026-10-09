"""Relationships at runtime: prefetch paths, ``link``, reverse pairing, and the ``None`` check.

The typed signatures live in ``_typing``.
"""

import sys
import typing
from collections.abc import Iterable, Mapping
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Any, ForwardRef, TypeIs, Union, cast, get_args, get_origin

from sqlalchemy import Column, ForeignKeyConstraint, Table, and_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import (
    InstrumentedAttribute,
    Mapper,
    MapperProperty,
    RelationshipDirection,
    RelationshipProperty,
    joinedload,
    selectinload,
)
from sqlalchemy.orm.interfaces import LoaderOption

from alchemic._sqlalchemy_private import is_mapped_annotation, is_populated, mapper_props, relationship_args

if TYPE_CHECKING:
    from alchemic.model import Model

__all__ = [
    "Path",
    "check_relationship_optionality",
    "link",
    "pair_relationships",
    "path",
    "prefetch",
]


class Path[O, E]:
    """``path()``'s result: relationship attributes to follow, in order."""

    __slots__ = ("steps",)

    def __init__(self, steps: tuple[InstrumentedAttribute[Any], ...]) -> None:
        self.steps = steps


def path(*steps: InstrumentedAttribute[Any]) -> Path[Any, Any]:
    """The ``Path`` through ``steps``; raises ``TypeError`` for fewer than two."""
    if len(steps) < 2:
        raise TypeError("path() takes two or more relationships. Prefetch a single one as it is.")
    return Path(steps)


def prefetch(root: type, /, *paths: Path[Any, Any] | InstrumentedAttribute[Any]) -> list[LoaderOption]:
    """Loader options that load each path from ``root``, every step along the way.

    Each collection step is loaded with ``selectinload``, each single-object step
    with ``joinedload``. Raises ``TypeError`` for a path that does not start at ``root``.
    """
    options: list[LoaderOption] = []
    for each in paths:
        steps = each.steps if isinstance(each, Path) else (each,)
        if not issubclass(root, steps[0].parent.class_):
            raise TypeError(f"{steps[0]} does not start at {root.__name__}")
        option: Any = None
        for attr in steps:
            collection = attr.property.uselist
            if option is None:
                option = selectinload(attr) if collection else joinedload(attr)
            else:
                option = option.selectinload(attr) if collection else option.joinedload(attr)
        options.append(option)
    return options


class _Unresolved(Exception):
    pass


def _namespace(mapper: Mapper[Any]) -> dict[str, Any]:
    module = sys.modules.get(mapper.class_.__module__)
    namespace: dict[str, Any] = {**vars(typing), **(vars(module) if module else {})}
    namespace.update(
        (registered.class_.__name__, registered.class_) for registered in mapper.registry.mappers
    )
    return namespace


def _allows_none(annotation: Any, namespace: Mapping[str, Any]) -> bool:
    """Whether ``annotation`` admits ``None``, looking through ``Mapped[...]`` and forward references."""
    if isinstance(annotation, str | ForwardRef):
        source = annotation if isinstance(annotation, str) else annotation.__forward_arg__
        try:
            # A type expression from the class body, evaluated as typing itself would.
            annotation = eval(source, dict(namespace))  # pylint: disable=eval-used
        except Exception as error:
            raise _Unresolved from error
        return _allows_none(annotation, namespace)
    origin = get_origin(annotation)
    if isinstance(origin, type) and is_mapped_annotation(origin):
        return _allows_none(get_args(annotation)[0], namespace)
    if origin in (Union, UnionType):
        return NoneType in get_args(annotation)
    return annotation is None or annotation is NoneType


def _why_it_can_be_none(rel: RelationshipProperty[Any]) -> str | None:
    if rel.uselist:
        return None
    if rel.direction is RelationshipDirection.MANYTOONE:
        nullable = sorted(column.name for column in rel.local_columns if column.nullable)
        if not nullable:
            return None
        return f"{', '.join(nullable)} {'is' if len(nullable) == 1 else 'are'} nullable"
    return "there may be no related row"


def check_relationship_optionality(mapper: Mapper[Any], cls: "type[Model]") -> None:
    """Raise ``TypeError`` for a single-object relationship that can be ``None`` but is not annotated so."""
    annotations = cls.__alchemic_non_column_annotations__
    namespace: dict[str, Any] | None = None
    for rel in mapper.relationships:
        if rel.key not in annotations or (why := _why_it_can_be_none(rel)) is None:
            continue
        namespace = namespace if namespace is not None else _namespace(mapper)
        try:
            if _allows_none(annotations[rel.key], namespace):
                continue
        except _Unresolved:
            continue
        # SQLAlchemy marks the mapper configured before this event runs. Clearing the
        # flag makes every later configure fail with this error, as for SQLAlchemy's own.
        mapper.configured = False
        raise TypeError(
            f"{cls.__name__}.{rel.key} can be None, since {why}, but its annotation does not allow None. "
            f"Annotate it Related[{rel.mapper.class_.__name__} | None]."
        )


def _relationships_of(mapper: Mapper[Any]) -> list[RelationshipProperty[Any]]:
    """The relationships ``mapper`` declares itself, read without configuring it."""
    return [
        prop
        for prop in mapper_props(mapper).values()
        if isinstance(prop, RelationshipProperty) and prop.parent is mapper
    ]


def _is_mapper(value: object) -> TypeIs[Mapper[Any]]:
    return isinstance(value, Mapper)


def _target(rel: RelationshipProperty[Any], mappers: Iterable[Mapper[Any]]) -> type[Any] | None:
    """The class ``rel`` points at, resolved by name as SQLAlchemy would.

    ``None`` if the class is not defined yet, or two registry classes share the name.
    """
    argument = rel.argument
    if isinstance(argument, str):

        def named(cls: type) -> bool:
            return argument in (cls.__name__, f"{cls.__module__}.{cls.__name__}")

        found = [registered.class_ for registered in mappers if named(registered.class_)]
        return found[0] if len(found) == 1 else None
    if _is_mapper(argument):
        return argument.class_
    if isinstance(argument, type):
        return argument
    if callable(argument):
        try:
            resolved = argument()
        except NameError:  # a class not defined yet
            return None
        return resolved if isinstance(resolved, type) else getattr(resolved, "class_", None)
    return None


def _pairable(rel: RelationshipProperty[Any]) -> bool:
    args = relationship_args(rel)
    joins = (args.primaryjoin, args.secondaryjoin, args.foreign_keys, args.remote_side)
    return not (any(is_populated(join) for join in joins) or rel.viewonly or rel.backref)


def pair_relationships(mapper: Mapper[Any], cls: type) -> None:
    """Set ``back_populates`` on both sides where exactly one relationship points back."""
    mappers = list(mapper.registry.mappers)
    for rel in _relationships_of(mapper):
        if rel.back_populates or not _pairable(rel):
            continue
        target = _target(rel, mappers)
        target_mapper = cast("Mapper[Any] | None", sa_inspect(target, raiseerr=False)) if target else None
        if target is None or target_mapper is None:
            continue

        def points_back(
            other: RelationshipProperty[Any], to: type, rel: RelationshipProperty[Any] = rel
        ) -> bool:
            return (
                other is not rel
                and _pairable(other)
                and other.back_populates in (None, rel.key)
                and _target(other, mappers) is to
                and relationship_args(other).secondary.argument is relationship_args(rel).secondary.argument
            )

        back = [other for other in _relationships_of(target_mapper) if points_back(other, cls)]
        if not back:
            continue
        rivals = [other for other in _relationships_of(mapper) if points_back(other, target)]
        if len(back) > 1 or rivals:
            candidates = ", ".join(sorted(f"{target.__name__}.{other.key}" for other in back))
            also = ", ".join(sorted(f"{cls.__name__}.{other.key}" for other in rivals))
            raise TypeError(
                f"{cls.__name__}.{rel.key} could pair with {candidates}"
                + (f", and so could {also}" if also else "")
                + ". Name each pair with back_populates=... on both sides."
            )
        (other,) = back
        rel.back_populates = other.key
        other.back_populates = rel.key


def link(
    left: InstrumentedAttribute[Any],
    right: InstrumentedAttribute[Any],
    /,
    *,
    name: str | None = None,
    prefixes: tuple[str, str] | None = None,
) -> Table:
    """Create the table behind a many-to-many, and wire ``left`` and ``right`` to it and to each other.

    The table has one column per primary-key column of each side, named
    ``{prefix}_{column}``, all of them its primary key, each side a foreign key
    with ``ON DELETE CASCADE``. The prefix defaults to the lower-cased class name;
    two relationships of one class need ``prefixes``. Call it before the first query.
    """
    left_mapper, right_mapper = left.parent.mapper, right.parent.mapper
    if left_mapper.configured or right_mapper.configured:
        raise TypeError("link() must run before mappers are configured, which is at the first query")
    left_rel, right_rel = left_mapper.get_property(left.key), right_mapper.get_property(right.key)
    for rel in (left_rel, right_rel):
        if not isinstance(rel, RelationshipProperty):
            raise TypeError(f"link() takes relationships, and {rel} is not one")
        if is_populated(relationship_args(rel).secondary) or rel.back_populates:
            raise TypeError(f"{rel} already names its secondary or back_populates. link() sets both.")
    left_table, right_table = left_mapper.local_table, right_mapper.local_table
    if not (isinstance(left_table, Table) and isinstance(right_table, Table)):
        raise TypeError("link() needs both classes mapped to a table")
    self_referential = left_table is right_table
    if prefixes is None:
        if self_referential:
            raise TypeError(
                f"link() between two relationships of {left.class_.__name__} needs prefixes=(...), "
                "one per side, such as prefixes=('follower', 'followed')"
            )
        prefixes = (left_mapper.class_.__name__.lower(), right_mapper.class_.__name__.lower())
    left_columns = _link_columns(prefixes[0], left_mapper)
    right_columns = _link_columns(prefixes[1], right_mapper)
    table = Table(
        name or f"{left_table.name}_{right_table.name}",
        left_table.metadata,
        *left_columns,
        *right_columns,
        ForeignKeyConstraint(left_columns, list(left_mapper.primary_key), ondelete="CASCADE"),
        ForeignKeyConstraint(right_columns, list(right_mapper.primary_key), ondelete="CASCADE"),
    )
    _wire_link(
        table,
        (left_rel, left_mapper, left_columns),
        (right_rel, right_mapper, right_columns),
        self_referential,
    )
    _wire_link(
        table,
        (right_rel, right_mapper, right_columns),
        (left_rel, left_mapper, left_columns),
        self_referential,
    )
    return table


def _link_columns(prefix: str, mapper: Mapper[Any]) -> list[Column[Any]]:
    """One primary-key column of a link table per primary-key column of ``mapper``."""
    return [Column(f"{prefix}_{column.name}", column.type, primary_key=True) for column in mapper.primary_key]


type _LinkSide = tuple[MapperProperty[Any], Mapper[Any], list[Column[Any]]]


def _wire_link(table: Table, near: _LinkSide, far: _LinkSide, self_referential: bool) -> None:
    """Point the relationship of ``near`` through ``table`` at the relationship of ``far``."""
    rel, near_mapper, near_columns = near
    other, far_mapper, far_columns = far
    assert isinstance(rel, RelationshipProperty) and isinstance(other, RelationshipProperty)
    args = relationship_args(rel)
    args.secondary.argument = table
    rel.back_populates = other.key
    if self_referential:
        # Explicit join conditions for self-referential relationships.
        near_keys = zip(near_mapper.primary_key, near_columns, strict=True)
        far_keys = zip(far_mapper.primary_key, far_columns, strict=True)
        args.primaryjoin.argument = and_(*(key == column for key, column in near_keys))
        args.secondaryjoin.argument = and_(*(column == key for key, column in far_keys))
