"""Typed ``Mapped``, ``mapped_column``, ``relationship`` and relationship paths.

The typed forms are declared for type checkers; at runtime the names are SQLAlchemy's, or thin wrappers.
"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, Protocol, TypedDict, TypeVar, Unpack, overload

from sqlalchemy import Column, ForeignKey
from sqlalchemy.orm import InstrumentedAttribute, MappedColumn
from sqlalchemy.orm import Mapped as _SAMapped
from sqlalchemy.orm import mapped_column as _sa_mapped_column
from sqlalchemy.orm import relationship as _sa_relationship

from alchemic._constants import INFO_READONLY, INFO_VERSION

__all__ = [
    "Mapped",
    "Path",
    "Related",
    "RelatedAttribute",
    "TypedAttribute",
    "link",
    "mapped_column",
    "path",
    "prefetch",
    "references",
    "relationship",
]

if TYPE_CHECKING:
    # Declarations for type checkers: parameters without bodies, which pyright requires to be ``...``.
    # pylint: disable=unused-argument,unnecessary-ellipsis
    from sqlalchemy import Table
    from sqlalchemy.engine.interfaces import Dialect
    from sqlalchemy.orm import QueryableAttribute
    from sqlalchemy.orm.interfaces import LoaderOption
    from sqlalchemy.sql import roles
    from sqlalchemy.sql.base import SchemaEventTarget
    from sqlalchemy.sql.elements import BinaryExpression, BindParameter, ColumnElement, SQLCoreOperations
    from sqlalchemy.sql.schema import SchemaConst

    from alchemic._sqlalchemy_private import (
        AutoIncrementType,
        BindProcessorType,
        InfoType,
        NoArg,
        ServerDefaultArgument,
        ServerOnUpdateArgument,
    )

    # Expression operands accept ``SQLCoreOperations[T | None]``; value operands accept ``T``.
    class TypedAttribute[T](InstrumentedAttribute[T]):
        """A mapped attribute at class level, with comparisons typed to ``T``.

        A comparison takes a ``T`` or a SQL expression of one. Test for ``NULL`` with ``.is_(None)``.
        """

        # Match bool operands before the general overload, returning bool on non-boolean columns.
        @overload  # type: ignore[override]
        def __eq__(  # type: ignore[overload-overlap]
            self: "TypedAttribute[bool]", other: bool | SQLCoreOperations[bool | None]
        ) -> ColumnElement[bool]: ...
        @overload
        def __eq__(  # type: ignore[overload-overlap]
            self: "TypedAttribute[bool | None]", other: bool | None | SQLCoreOperations[bool | None]
        ) -> ColumnElement[bool]: ...
        @overload
        def __eq__(self, other: bool) -> bool: ...  # type: ignore[overload-overlap]  # pyright: ignore[reportOverlappingOverload]
        @overload
        def __eq__(self, other: T | SQLCoreOperations[T | None]) -> ColumnElement[bool]: ...
        def __eq__(self, other: object) -> Any: ...
        @overload  # type: ignore[override]
        def __ne__(  # type: ignore[overload-overlap]
            self: "TypedAttribute[bool]", other: bool | SQLCoreOperations[bool | None]
        ) -> ColumnElement[bool]: ...
        @overload
        def __ne__(  # type: ignore[overload-overlap]
            self: "TypedAttribute[bool | None]", other: bool | None | SQLCoreOperations[bool | None]
        ) -> ColumnElement[bool]: ...
        @overload
        def __ne__(self, other: bool) -> bool: ...  # type: ignore[overload-overlap]  # pyright: ignore[reportOverlappingOverload]
        @overload
        def __ne__(self, other: T | SQLCoreOperations[T | None]) -> ColumnElement[bool]: ...
        def __ne__(self, other: object) -> Any: ...
        def __lt__(self, other: T | SQLCoreOperations[T | None]) -> ColumnElement[bool]: ...
        def __le__(self, other: T | SQLCoreOperations[T | None]) -> ColumnElement[bool]: ...
        def __gt__(self, other: T | SQLCoreOperations[T | None]) -> ColumnElement[bool]: ...
        def __ge__(self, other: T | SQLCoreOperations[T | None]) -> ColumnElement[bool]: ...
        def __hash__(self) -> int: ...
        def in_(
            self, other: Iterable[T] | BindParameter[Any] | roles.InElementRole
        ) -> BinaryExpression[bool]:
            """``self IN other``."""
            ...

        def not_in(
            self, other: Iterable[T] | BindParameter[Any] | roles.InElementRole
        ) -> BinaryExpression[bool]:
            """``self NOT IN other``."""
            ...

    T_contra = TypeVar("T_contra", contravariant=True)

    # Contravariant parameter: ``mapped_column()``'s ``Mapped[E | None]`` is assignable
    # to ``Mapped[A]`` when ``A`` is a subtype of ``E | None``.
    # pylint: disable-next=abstract-method
    class Mapped(_SAMapped[T_contra], Generic[T_contra]):  # type: ignore[type-var]  # noqa: UP046  # pyright: ignore[reportGeneralTypeIssues]  # ty: ignore[invalid-generic-class]
        """``sqlalchemy.orm.Mapped``, returning ``TypedAttribute[T]`` at class level."""

        @overload
        def __get__(
            self, instance: None, owner: Any
        ) -> TypedAttribute[T_contra]: ...  # pyright: ignore[reportGeneralTypeIssues]
        @overload
        def __get__(self, instance: object, owner: Any) -> T_contra: ...  # type: ignore[misc]  # pyright: ignore[reportGeneralTypeIssues]
        def __get__(self, instance: object | None, owner: Any) -> Any: ...

        # ``T`` alone: this is also the constructor's parameter type.
        def __set__(self, instance: Any, value: T_contra) -> None: ...  # type: ignore[override]  # ty: ignore[invalid-method-override]

    # Invariant type parameters for Path and RelatedAttribute step checking.
    OwnerT = TypeVar("OwnerT")
    EndT = TypeVar("EndT")
    ValueT = TypeVar("ValueT")
    T_co = TypeVar("T_co", covariant=True)

    class Path(Generic[OwnerT, EndT]):  # noqa: UP046
        """Relationships to follow from an ``OwnerT``, ending at an ``EndT``: what ``prefetch`` takes."""

    class RelatedAttribute(
        TypedAttribute[ValueT], Path[OwnerT, EndT], Generic[OwnerT, EndT, ValueT]  # noqa: UP046
    ):
        """A relationship at class level: ``Team.heroes`` goes from ``Team`` to ``Hero``.

        It is still the attribute SQLAlchemy's ``join``, ``selectinload`` and
        ``any`` take, and the first step of a ``Path``.
        """

    class Related(Generic[T_co]):  # noqa: UP046
        """``Mapped`` for a relationship: ``heroes: Related[list["Hero"]] = relationship()``.

        At class level it gives a ``RelatedAttribute``, which ``path``, ``prefetch`` and ``link`` check.
        At runtime it is ``Mapped``.
        """

        @overload
        def __get__[O, E](
            self: "Related[list[E]]", instance: None, owner: type[O]
        ) -> RelatedAttribute[O, E, list[E]]: ...

        # One overload per collection type SQLAlchemy builds from an annotation.
        @overload
        def __get__[O, E](
            self: "Related[set[E]]", instance: None, owner: type[O]
        ) -> RelatedAttribute[O, E, set[E]]: ...
        @overload
        def __get__[O, E](
            self: "Related[E | None]", instance: None, owner: type[O]
        ) -> RelatedAttribute[O, E, T_co]: ...
        @overload
        def __get__(self, instance: object, owner: Any) -> T_co: ...
        def __get__(self, instance: object | None, owner: Any) -> Any: ...

        # Also the constructor's parameter type, as for ``Mapped``.
        def __set__(self, instance: Any, value: T_co) -> None: ...  # type: ignore[misc]  # pyright: ignore[reportGeneralTypeIssues]  # ty: ignore[invalid-generic-class]

    @overload
    def path[O, A, B](
        first: RelatedAttribute[O, A, Any], second: RelatedAttribute[A, B, Any], /
    ) -> Path[O, B]: ...
    @overload
    def path[O, A, B, C](
        first: RelatedAttribute[O, A, Any],
        second: RelatedAttribute[A, B, Any],
        third: RelatedAttribute[B, C, Any],
        /,
    ) -> Path[O, C]: ...
    @overload
    def path[O, A, B, C, D](
        first: RelatedAttribute[O, A, Any],
        second: RelatedAttribute[A, B, Any],
        third: RelatedAttribute[B, C, Any],
        fourth: RelatedAttribute[C, D, Any],
        /,
    ) -> Path[O, D]: ...
    def path(*steps: Any) -> Any:
        """Relationships followed one after another: ``path(Team.heroes, Hero.powers)``.

        Each step must start at the class the previous one ends at, or it is a
        type error. For ``prefetch``, which loads every step along the way.
        """

    def prefetch[O](root: type[O], /, *paths: Path[O, Any]) -> list[LoaderOption]:
        """Loader options for ``root``: ``session.get(Team, 1, options=prefetch(Team, Team.heroes))``.

        Each path must start at ``root``, or it is a type error.
        """
        ...

    def link[A, B](
        left: RelatedAttribute[A, B, list[B]] | RelatedAttribute[A, B, set[B]],
        right: RelatedAttribute[B, A, list[A]] | RelatedAttribute[B, A, set[A]],
        /,
        *,
        name: str | None = None,
        prefixes: tuple[str, str] | None = None,
    ) -> Table:
        """Create the table behind a many-to-many: ``link(Hero.powers, Power.heroes)``.

        ``left`` and ``right`` must be collections pointing at each other, or it is a type error.
        ``name`` names the table; ``prefixes`` prefix its columns, and are needed when both
        sides are one class. Raises ``TypeError`` once mappers are configured, at the first query.
        """
        ...

    class ColumnTypeFor[E](Protocol):
        """A column type that binds a Python ``E``, and so any subtype of ``E``."""

        def _cached_bind_processor(self, dialect: Dialect) -> BindProcessorType[E] | None: ...

    class _ColumnOptions(TypedDict, total=False):
        """``mapped_column``'s keyword arguments except ``default`` and ``type_``, and no dialect options."""

        init: bool
        readonly: bool
        version: bool
        nullable: bool | Literal[SchemaConst.NULL_UNSPECIFIED] | None
        primary_key: bool | None
        deferred: NoArg | bool
        deferred_group: str | None
        deferred_raiseload: bool | None
        use_existing_column: bool
        name: str | None
        autoincrement: AutoIncrementType
        doc: str | None
        key: str | None
        index: bool | None
        unique: bool | None
        info: InfoType | None
        insert_default: Any | None
        server_default: ServerDefaultArgument | None
        server_onupdate: ServerOnUpdateArgument | None
        active_history: bool
        quote: bool | None
        system: bool
        comment: str | None
        sort_order: NoArg | int

    class Reference[E]:
        """A foreign key typed as the column it references, matching ``ColumnTypeFor[E]``.

        A ``ForeignKey`` at runtime, and deliberately not a subclass of one here.
        """

        def _cached_bind_processor(self, dialect: Dialect) -> BindProcessorType[E] | None: ...

    # pylint: disable-next=too-many-arguments
    def references[E](
        column: TypedAttribute[E] | Mapped[E] | QueryableAttribute[E],
        /,
        *,
        name: str | None = None,
        onupdate: str | None = None,
        ondelete: str | None = None,
        deferrable: bool | None = None,
        initially: str | None = None,
        use_alter: bool = False,
        link_to_name: bool = False,
        match: str | None = None,
        info: InfoType | None = None,
        comment: str | None = None,
    ) -> Reference[E]:
        """A foreign key to ``column``, checked against the annotation.

            team_id: Mapped[int | None] = mapped_column(references(Team.id, ondelete="CASCADE"))

        ``Mapped[str]`` there is a type error, since ``Team.id`` is an ``int``,
        and so is a misspelled ``Team.idd``. Inside the class body, a column of
        the same class is referenced by its bare name: ``references(id)``. A
        class defined later needs ``ForeignKey("teams.id")``, unchecked. The
        keyword arguments are ``ForeignKey``'s.
        """
        ...

    type _NameOrItem = str | SchemaEventTarget | None

    # Class-form overloads (``DateTime``) precede instance-form overloads (``DateTime()``)
    # for type checker protocol matching.
    @overload
    def mapped_column[E](
        type_pos: type[ColumnTypeFor[E]],
        /,
        *args: SchemaEventTarget,
        default: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        onupdate: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[E | None]: ...
    @overload
    def mapped_column[E](
        type_pos: ColumnTypeFor[E],
        /,
        *args: SchemaEventTarget,
        default: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        onupdate: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[E | None]: ...
    @overload
    def mapped_column[E](
        name_pos: str,
        type_pos: type[ColumnTypeFor[E]],
        /,
        *args: SchemaEventTarget,
        default: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        onupdate: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[E | None]: ...
    @overload
    def mapped_column[E](
        name_pos: str,
        type_pos: ColumnTypeFor[E],
        /,
        *args: SchemaEventTarget,
        default: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        onupdate: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[E | None]: ...
    @overload
    def mapped_column[E](
        name_pos: _NameOrItem = None,
        type_pos: SchemaEventTarget | None = None,
        /,
        *args: SchemaEventTarget,
        type_: type[ColumnTypeFor[E]],
        default: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        onupdate: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[E | None]: ...
    @overload
    def mapped_column[E](
        name_pos: _NameOrItem = None,
        type_pos: SchemaEventTarget | None = None,
        /,
        *args: SchemaEventTarget,
        type_: ColumnTypeFor[E],
        default: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        onupdate: E | Callable[[], E] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[E | None]: ...

    # Overloads without a column type; solves ``D`` from the default or factory.
    @overload
    def mapped_column(
        name_pos: _NameOrItem = None,
        type_pos: SchemaEventTarget | None = None,
        /,
        *args: SchemaEventTarget,
        default: SQLCoreOperations[Any] | None = ...,
        onupdate: SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[Any]: ...
    @overload
    def mapped_column[D](
        name_pos: _NameOrItem = None,
        type_pos: SchemaEventTarget | None = None,
        /,
        *args: SchemaEventTarget,
        default: Callable[[], D],
        onupdate: D | Callable[[], D] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[D]: ...
    @overload
    def mapped_column[D](
        name_pos: _NameOrItem = None,
        type_pos: SchemaEventTarget | None = None,
        /,
        *args: SchemaEventTarget,
        default: D,
        onupdate: D | Callable[[], D] | SQLCoreOperations[Any] | None = ...,
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[D]: ...
    @overload
    def mapped_column[D](
        name_pos: _NameOrItem = None,
        type_pos: SchemaEventTarget | None = None,
        /,
        *args: SchemaEventTarget,
        default: SQLCoreOperations[Any] | None = ...,
        onupdate: Callable[[], D],
        **kw: Unpack[_ColumnOptions],
    ) -> Mapped[D]: ...
    def mapped_column(*args: Any, **kw: Any) -> Any:
        """``sqlalchemy.orm.mapped_column``, typed by its overloads."""
        ...

    def _returning_any[**P](fn: Callable[P, object]) -> Callable[P, Any]: ...

    # Returns ``Any`` to accommodate ``WriteOnlyMapped`` and ``DynamicMapped``.
    relationship: Final = _returning_any(_sa_relationship)

    class Replaceable[**P, R](Protocol):
        """An object with a ``__replace__``, capturing its parameters for ``update`` and ``assign``."""

        def __replace__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...

else:
    from alchemic._relationships import Path, link, path, prefetch
    from alchemic._sqlalchemy_private import exclude_from_init

    TypedAttribute = InstrumentedAttribute
    RelatedAttribute = InstrumentedAttribute
    Mapped = _SAMapped
    Related = _SAMapped
    relationship = _sa_relationship

    def mapped_column(
        *args: Any, init: bool = True, readonly: bool | None = None, version: bool = False, **kw: Any
    ) -> MappedColumn[Any]:
        """SQLAlchemy's ``mapped_column``, plus ``init``, ``readonly`` and ``version``.

        ``init=False`` leaves the field out of the constructor, for a value the
        database generates. ``readonly=True`` lets the constructor set the field,
        and makes a later change raise ``ValidationError``. ``version=True`` makes
        the column the mapper's ``version_id_col``, read-only unless declared
        otherwise or the application writes it (``version_id_generator=False``).

        Raises ``TypeError`` for a ``Column`` among ``args``.
        """
        for argument in args:
            if isinstance(argument, Column):
                raise TypeError(
                    "mapped_column() takes a Column's arguments, not a Column: pass its type, "
                    "constraints and keywords to mapped_column() directly"
                )
        column = _sa_mapped_column(*args, **kw)
        if not init:
            exclude_from_init(column)
        if readonly is not None:
            column.column.info[INFO_READONLY] = readonly
        if version:
            column.column.info[INFO_VERSION] = True
        return column

    def references(column: Any, /, **kw: Any) -> ForeignKey:
        """``ForeignKey(column)``. A bare ``mapped_column()`` stands for its ``Column``."""
        if isinstance(column, MappedColumn):
            column = column.column
        return ForeignKey(column, **kw)
