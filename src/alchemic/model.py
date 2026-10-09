"""The metaclass and base class that make one class both a SQLAlchemy and a Pydantic model."""

import inspect
import re
import warnings
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Never, Self, cast, dataclass_transform, overload

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError
from pydantic._internal._model_construction import ModelMetaclass
from pydantic_core import InitErrorDetails, PydanticCustomError
from sqlalchemy import MetaData, Select, Update, event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import update as sa_update
from sqlalchemy.orm import (
    DeclarativeBase,
    InstanceState,
    MappedColumn,
    Mapper,
    declared_attr,
    instrumentation,
)
from sqlalchemy.orm import mapped_column as sa_mapped_column
from sqlalchemy.orm import registry as orm_registry
from sqlalchemy.orm.base import DEFAULT_STATE_ATTR, InspectionAttr
from sqlalchemy.orm.decl_api import DeclarativeAttributeIntercept
from sqlalchemy.orm.exc import UnmappedClassError, UnmappedColumnError
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.util.concurrency import greenlet_spawn

from alchemic._constants import (
    ATTR_MAPPER_ARGS,
    INFO_VERSION,
    INHERITED_MAPPER_ARGS_EXCLUSIONS,
    PYDANTIC_EXTRA,
    PYDANTIC_FIELDS_SET,
    PYDANTIC_PRIVATE,
    SA_VERSION_ID_COL,
)
from alchemic._fields import Declared, declared_defaults, is_mapped_column, repair_model_fields
from alchemic._mapped_schema import install_mapped_schema_hook
from alchemic._metadata import as_dict
from alchemic._receivers import Updated, install_change_recording, receivers_for
from alchemic._relationships import check_relationship_optionality, pair_relationships
from alchemic._typing import TypedAttribute, mapped_column
from alchemic._typing import prefetch as _prefetch

if TYPE_CHECKING:
    from alchemic._typing import Path, Replaceable

install_mapped_schema_hook()

__all__ = ["DeclarativeRoot", "Model", "ModelMeta", "create_model_base"]


def _eval_mapper_args(cls: type, target: object) -> dict[str, Any]:
    """Evaluate a ``__mapper_args__`` value (``None``, descriptor, callable or mapping) to a dict."""
    if target is None:
        return {}

    fget: object = getattr(target, "fget", None)
    if callable(fget):
        return as_dict(fget(cls)) or {}

    if callable(target):
        return as_dict(target(cls)) or {}

    return as_dict(target) or {}


def _compose_mapper_args(bases: tuple[type, ...], ns: dict[str, Any]) -> None:
    """Merge inherited ``__mapper_args__`` into those the class declares, if it declares any."""
    if ATTR_MAPPER_ARGS not in ns:
        return

    child_target = ns[ATTR_MAPPER_ARGS]
    parent_targets: list[Any] = []

    for base in bases:
        for ancestor in base.__mro__:
            if ancestor is DeclarativeBase or ancestor is BaseModel:
                continue
            if ATTR_MAPPER_ARGS in ancestor.__dict__:
                parent_targets.append(ancestor.__dict__[ATTR_MAPPER_ARGS])

    if not parent_targets:
        return

    @declared_attr.directive
    def __mapper_args__(cls: type) -> dict[str, Any]:
        combined: dict[str, Any] = {}
        has_mapped_base_table = any(
            getattr(ancestor, "__table__", None) is not None for ancestor in cls.__mro__[1:]
        )

        for pt in reversed(parent_targets):
            evaluated = _eval_mapper_args(cls, pt)
            if has_mapped_base_table and INHERITED_MAPPER_ARGS_EXCLUSIONS:
                evaluated = {
                    key: value
                    for key, value in evaluated.items()
                    if key not in INHERITED_MAPPER_ARGS_EXCLUSIONS
                }
            combined.update(evaluated)

        child_evaluated = _eval_mapper_args(cls, child_target)
        combined.update(child_evaluated)
        return combined

    ns[ATTR_MAPPER_ARGS] = __mapper_args__


def _declares_version_column(bases: tuple[type, ...], ns: Mapping[str, Any]) -> bool:
    """Whether ``mapped_column(version=True)`` is declared here or on an unmapped base."""
    classes = [ns, *(vars(ancestor) for base in bases for ancestor in base.__mro__)]
    return any(
        is_mapped_column(value) and value.column.info.get(INFO_VERSION)
        for namespace in classes
        for value in namespace.values()
    )


def _add_version_id_col(bases: tuple[type, ...], ns: dict[str, Any]) -> None:
    """Add the ``version=True`` column to the class's ``__mapper_args__`` as ``version_id_col``.

    Only on the class that owns the table; an inheriting mapper takes its parent's.
    """
    if not _declares_version_column(bases, ns):
        return
    inner = ns.get(ATTR_MAPPER_ARGS)
    if inner is None:
        inner = next(
            (
                vars(ancestor)[ATTR_MAPPER_ARGS]
                for base in bases
                for ancestor in base.__mro__
                if ancestor is not DeclarativeBase
                and ancestor is not BaseModel
                and ATTR_MAPPER_ARGS in vars(ancestor)
            ),
            None,
        )

    @declared_attr.directive
    def __mapper_args__(cls: type) -> dict[str, Any]:
        args = _eval_mapper_args(cls, inner)
        has_mapped_base = any(
            getattr(ancestor, "__table__", None) is not None for ancestor in cls.__mro__[1:]
        )
        if SA_VERSION_ID_COL not in args and not has_mapped_base:
            # A subclass may redeclare the column without ``version=True``.
            table = cast("Any", cls).__table__
            column = next(
                (candidate for candidate in table.columns if candidate.info.get(INFO_VERSION)), None
            )
            if column is not None:
                args[SA_VERSION_ID_COL] = column
        return args

    ns[ATTR_MAPPER_ARGS] = __mapper_args__


def _registry_from_attributes(ns: dict[str, Any]) -> None:
    """Set ``_sa_registry``, which SQLAlchemy maps against, from a declared ``registry`` or ``metadata``."""
    if "_sa_registry" in ns:
        return
    reg = ns.get("registry")
    meta = ns.get("metadata")
    if isinstance(reg, orm_registry):
        ns["_sa_registry"] = reg
        ns.setdefault("metadata", reg.metadata)
    elif isinstance(meta, MetaData):
        ns["_sa_registry"] = ns["registry"] = orm_registry(metadata=meta)


_SHADOW_WARNING = re.compile(r'Field name "(?P<name>[^"]+)" in "[^"]+" shadows an attribute in parent')


def _is_mapped_attribute(value: object) -> bool:
    """Whether ``value`` is a class attribute SQLAlchemy owns: a declared or an instrumented one."""
    return isinstance(value, (MappedColumn, InspectionAttr))


def _shadows_mapped_attribute(warning: warnings.WarningMessage, bases: tuple[type, ...]) -> bool:
    """Whether ``warning`` is Pydantic's "shadows an attribute" warning for an inherited mapped attribute."""
    match = _SHADOW_WARNING.match(str(warning.message))
    if match is None:
        return False
    name = match["name"]
    return any(_is_mapped_attribute(getattr(base, name, None)) for base in bases)


def _run_before_insert(_mapper: Any, _connection: Any, target: "Model") -> None:
    target.before_insert()


def _run_before_update(_mapper: Any, _connection: Any, target: "Model") -> None:
    target.before_update()


def _run_before_delete(_mapper: Any, _connection: Any, target: "Model") -> None:
    target.before_delete()


_HOOKS = {
    "before_insert": _run_before_insert,
    "before_update": _run_before_update,
    "before_delete": _run_before_delete,
}


def _overrides(cls: type, hook: str) -> bool:
    return getattr(cls, hook) is not getattr(Model, hook)


def _refuse_async_hooks(name: str, bases: tuple[type, ...], ns: Mapping[str, Any]) -> None:
    """Raise ``TypeError`` for an ``async def`` flush hook, before the class is mapped."""
    for hook in _HOOKS:
        fn = ns.get(hook) or next((getattr(base, hook) for base in bases if hasattr(base, hook)), None)
        if inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"{name}.{hook} is async, but it runs inside the flush, which cannot await. "
                "Make it a plain method, or move the async work to an on_commit receiver."
            )


def _listen_for_hooks(cls: type) -> None:
    """Listen for the flush hooks a mapped class overrides, on that class only (not propagated)."""
    mapper: Mapper[Any] | None = sa_inspect(cls, raiseerr=False)
    if mapper is None or mapper.class_ is not cls:
        return
    for hook, run in _HOOKS.items():
        if _overrides(cls, hook):
            event.listen(cls, hook, run)


class DeclarativeRoot(DeclarativeBase):
    """SQLAlchemy declarative root. Subclass ``Model``, not this."""


# Named metaclass bases for type checker compatibility.
# dataclass_transform specifies field definitions and identity-based comparisons.
@dataclass_transform(
    kw_only_default=True,
    eq_default=False,
    field_specifiers=(mapped_column, sa_mapped_column, Field, PrivateAttr),
)
class ModelMeta(ModelMetaclass, DeclarativeAttributeIntercept):
    """The metaclass of ``Model``: Pydantic's ``ModelMetaclass`` fused with SQLAlchemy's declarative one."""

    def __new__(  # pylint: disable=signature-differs
        mcls, name: str, bases: tuple[type, ...], ns: dict[str, Any], **kw: Any
    ) -> Any:
        _refuse_async_hooks(name, bases, ns)
        _registry_from_attributes(ns)
        _compose_mapper_args(bases, ns)
        _add_version_id_col(bases, ns)
        declared = declared_defaults(bases, ns)
        # Only the shadowing warning is dropped; the rest are re-issued to the caller's filters.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cls = cast("type[BaseModel]", super().__new__(mcls, name, bases, ns, **kw))
        for warning in caught:
            if not _shadows_mapped_attribute(warning, bases):
                warnings.warn_explicit(warning.message, warning.category, warning.filename, warning.lineno)
        repair_model_fields(cls, declared)
        _compare_table_rows_by_identity(cls)
        _listen_for_hooks(cls)
        return cls

    def __delattr__(cls, key: str) -> None:
        """Keep mapped attributes when Pydantic deletes field defaults off the class after building it."""
        if _is_mapped_attribute(getattr(cls, key, None)):
            return
        try:
            super().__delattr__(key)
        except NotImplementedError:
            pass


def _compare_table_rows_by_identity(cls: type) -> None:
    """Make a table model's instances equal only to themselves, and hashable.

    A class that defines its own ``__eq__`` or ``__hash__`` keeps it.
    """
    if getattr(cls, "__table__", None) is None:
        return
    if cls.__eq__ is BaseModel.__eq__:  # type: ignore[comparison-overlap]
        cls.__eq__ = object.__eq__  # type: ignore[method-assign,assignment]
    if cls.__hash__ is None:
        cls.__hash__ = object.__hash__


def _copy_error(cls: type) -> TypeError:
    name = cls.__name__
    return TypeError(
        f"a {name} cannot be copied: the copy would share or break its SQLAlchemy instance state. "
        f"Construct a new {name}(...), or change this one with update()."
    )


_NO_INIT_ERROR = PydanticCustomError(
    "init_false", "Declared init=False: the database sets it, not the caller"
)


def _refuse_no_init(cls: "type[Model]", values: Mapping[str, Any]) -> None:
    """Raise ``ValidationError`` for any value given to an ``init=False`` field."""
    given = sorted(values.keys() & cls.__alchemic_no_init__)
    if given:
        errors: list[InitErrorDetails] = [
            {"type": _NO_INIT_ERROR, "loc": (name,), "input": values[name]} for name in given
        ]
        raise ValidationError.from_exception_data(cls.__name__, errors)


class Model(BaseModel, DeclarativeRoot, metaclass=ModelMeta):
    """Base for models that are simultaneously a SQLAlchemy table and a Pydantic model.

        class Character(Model):
            __tablename__ = "characters"
            id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
            name: Mapped[str] = mapped_column(String(80))
            actor: Related[Actor | None] = relationship()

    ``Character.name`` is an ``InstrumentedAttribute[str]`` (queryable, and a type
    checker knows it); ``character.name`` is a ``str``. Construction validates.
    """

    __abstract__ = True

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True,
        ignored_types=(orm_registry, MetaData),
    )

    __alchemic_non_columns__: ClassVar[frozenset[str]] = frozenset()
    __alchemic_non_column_annotations__: ClassVar[Mapping[str, Any]] = MappingProxyType({})
    __alchemic_declared_defaults__: ClassVar[Mapping[str, Declared]] = MappingProxyType({})
    __alchemic_no_init__: ClassVar[frozenset[str]] = frozenset()
    __alchemic_readonly__: ClassVar[frozenset[str]] = frozenset()

    # Hidden from type checkers; dataclass_transform supplies the constructor signature.
    if not TYPE_CHECKING:

        def __new__(cls, *_args: Any, **_kwargs: Any) -> Self:
            """Create an instance with Pydantic's private attributes set, and no fields marked as set."""
            # Initialize Pydantic attributes for rows instantiated via __new__.
            self = super().__new__(cls)
            object.__setattr__(self, PYDANTIC_FIELDS_SET, set())
            object.__setattr__(self, PYDANTIC_EXTRA, None)
            object.__setattr__(self, PYDANTIC_PRIVATE, None)
            return self

        def __init__(self, **data: Any) -> None:
            """Validate ``data`` like a Pydantic model, then set it through the ORM so the session sees it.

            Raises ``ValidationError`` for invalid input, including a value for an ``init=False`` field.
            """
            _refuse_no_init(type(self), data)
            non_columns = type(self).__alchemic_non_columns__
            related = {name: data.pop(name) for name in list(data) if name in non_columns}

            super().__init__(**data)

            self._init_orm_instance_state(related)

    def _init_orm_instance_state(self, related: dict[str, Any]) -> None:
        """Create the ORM instance state, then apply the validated fields and ``related`` through it."""
        manager = instrumentation.opt_manager_of_class(type(self))
        if manager is None:
            raise UnmappedClassError(type(self))

        validated = dict(self.__dict__)
        given = set(self.__pydantic_fields_set__)
        manager.setup_instance(self)
        # Assign validated attributes without re-triggering validation.
        for key, value in validated.items():
            self._assign(key, value)
        # Restore polymorphic discriminator if replaced by field defaults.
        mapper = manager.mapper
        if mapper.polymorphic_identity is not None and mapper.polymorphic_on is not None:
            discriminator: str | None
            try:
                discriminator = mapper.get_property_by_column(mapper.polymorphic_on).key
            except UnmappedColumnError:  # a SQL expression, not a column of this model
                discriminator = None
            if discriminator is not None and discriminator not in given:
                self._assign(discriminator, mapper.polymorphic_identity)
                self.__pydantic_fields_set__.discard(discriminator)

        # Assign relationship attributes without Pydantic field validation.
        for key, value in related.items():
            setattr(self, key, value)

    def _validated(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Validate ``values``, in order, as assignments to a stand-in copy of this instance.

        Returns them with any other field a ``model_validator`` changed. Raises ``ValidationError``.
        """
        cls = type(self)
        # Pre-load missing fields when model validators are defined.
        if cls.__pydantic_decorators__.model_validators:
            for name in cls.__pydantic_fields__.keys() - self.__dict__.keys():
                getattr(self, name)
        proxy = cls.__new__(cls)
        fields = {name: value for name, value in self.__dict__.items() if name in cls.__pydantic_fields__}
        object.__setattr__(proxy, "__dict__", fields)
        private = getattr(self, PYDANTIC_PRIVATE, None)
        object.__setattr__(proxy, PYDANTIC_PRIVATE, None if private is None else dict(private))
        for name, value in values.items():
            cls.__pydantic_validator__.validate_assignment(proxy, name, value)
        # Collect values provided in input or modified on the proxy during validation.
        missing = object()
        return {
            name: value
            for name, value in proxy.__dict__.items()
            if name in values or self.__dict__.get(name, missing) is not value
        }

    def _assign(self, name: str, value: Any) -> None:
        """Assign an already-validated value through the ORM descriptor, if there is one."""
        cls = type(self)
        attr = getattr(cls, name, None)
        if attr is not None and hasattr(attr, "__set__"):
            if DEFAULT_STATE_ATTR in self.__dict__:
                object.__setattr__(self, name, value)
            else:
                # Store in instance dict when ORM state is not yet initialized.
                vars(self)[name] = value
            if name in cls.__pydantic_fields__:
                self.__pydantic_fields_set__.add(name)
            return
        super().__setattr__(name, value)

    def __setattr__(self, name: str, value: Any) -> None:
        """Validate under ``validate_assignment``, refuse ``readonly`` changes, and assign through the ORM."""
        if name.startswith("_sa_"):
            # Pass through internal SQLAlchemy instance state attributes.
            object.__setattr__(self, name, value)
            return
        cls = type(self)
        if name in cls.__pydantic_fields__ and cls.model_config.get("validate_assignment"):
            value = self._validated({name: value})[name]
        if name in cls.__alchemic_readonly__:
            self._refuse_readonly({name: value})
        self._assign(name, value)

    def _refuse_readonly(self, values: Mapping[str, Any]) -> None:
        """Raise ``ValidationError`` for changing a ``readonly=True`` field that holds a non-``None`` value.

        Nothing is refused before the instance state exists. An unloaded field is loaded to compare.
        """
        if DEFAULT_STATE_ATTR not in self.__dict__:
            return
        cls = type(self)
        errors: list[InitErrorDetails] = []
        for name in sorted(values.keys() & cls.__alchemic_readonly__):
            current = getattr(self, name)
            if current is not None and current != values[name]:
                errors.append({"type": "frozen_field", "loc": (name,), "input": values[name]})
        if errors:
            raise ValidationError.from_exception_data(cls.__name__, errors)

    @overload
    def update(self, patch: BaseModel | Mapping[str, Any], /) -> Self: ...
    @overload
    def update[**P, R](self: "Replaceable[P, R]", /, *args: P.args, **fields: P.kwargs) -> R: ...

    # Dispatches either a single patch object or keyword updates.
    def update(self, /, *args: Any, **fields: Any) -> Any:
        """Apply a partial update and return ``self``.

            hero.update(name="Rick", age=None)   # checked like the constructor
            hero.update(patch)                   # a Pydantic model or a mapping

        The keywords take any subset of the constructor's arguments, relationships
        included, and are checked statically. ``patch`` is checked at runtime only.
        For a Pydantic model, only the fields it explicitly set are applied: an
        omitted field is left alone, and an explicit ``None`` is written.

        Each value is validated as ``validate_assignment`` would, whether or not the
        model enables it, and nothing is written unless every value passes. Raises
        ``ValidationError`` for an invalid value, an unknown field, an ``init=False``
        field or a change to a ``readonly=True`` one, and ``TypeError`` for both a
        patch and keywords.
        """
        if len(args) > 1 or (args and fields):
            raise TypeError("update() takes either one patch or keyword arguments")
        if not args:
            values = fields
        elif isinstance(patch := args[0], BaseModel):
            values = {name: getattr(patch, name) for name in patch.model_fields_set}
        else:
            values = dict(patch)

        _refuse_no_init(type(self), values)
        # Apply relationship updates after validated column fields pass.
        non_columns = type(self).__alchemic_non_columns__
        related = {name: values.pop(name) for name in list(values) if name in non_columns}
        validated = self._validated(values)
        self._refuse_readonly(validated)
        for name, value in validated.items():
            self._assign(name, value)
        for name, value in related.items():
            setattr(self, name, value)
        return self

    @classmethod
    def assign[**P, R](cls: "type[Replaceable[P, R]]", /, *args: P.args, **values: P.kwargs) -> Update:
        """An ``UPDATE`` of this model's table that sets ``values``.

            session.exec(Hero.assign(name="Rick").where(Hero.id == hero_id))

        The keywords are checked statically, like ``update``'s, and not validated at
        runtime. Without a ``.where()``, every row is updated. Raises ``TypeError``
        for a relationship or a ``readonly=True`` field, and for a model with a
        ``before_update`` hook or ``on_commit`` update receivers, which a bulk
        ``UPDATE`` would skip.
        """
        model = cast("type[Model]", cls)
        if args:
            raise TypeError(f"{model.__name__}.assign() takes keyword arguments only")
        related = sa_inspect(model).relationships.keys() & values.keys()
        if related:
            raise TypeError(f"{model.__name__}.assign() sets columns, not {', '.join(sorted(related))}")
        readonly = model.__alchemic_readonly__ & values.keys()
        if readonly:
            raise TypeError(
                f"{model.__name__}.assign() cannot set {', '.join(sorted(readonly))}: declared readonly=True"
            )
        skipped = _skipped_by_bulk_update(model)
        if skipped:
            raise TypeError(
                f"{model.__name__}.assign() would skip {' and '.join(skipped)}: a bulk UPDATE runs no "
                f"per-object code. Update the loaded objects, or use sqlalchemy.update({model.__name__}) "
                "to bypass it deliberately."
            )
        return sa_update(model).values(**values)

    def has_changed(self, attr: TypedAttribute[Any], /) -> bool:
        """Whether ``attr`` has a change not yet flushed: ``self.has_changed(Task.status)``.

        In ``before_update`` it tells which fields this update changes. Setting a
        field to the value it holds is not a change; a relationship change is.
        Nothing is loaded to answer. Raises ``TypeError`` for another model's attribute.
        """
        if not isinstance(self, attr.parent.class_):
            raise TypeError(f"{attr} is not an attribute of {type(self).__name__}")
        state: InstanceState[Self] = sa_inspect(self)
        return state.attrs[attr.key].history.has_changes()

    def before_insert(self) -> None:
        """Called by the flush that inserts this row, just before its ``INSERT``.

        Override it to fill in or check values. Column attributes assigned here
        are part of the ``INSERT``. It runs inside the flush, so it must not add
        or delete objects, or flush.
        """

    def before_update(self) -> None:
        """Called by a flush for this row when it is dirty, before its ``UPDATE``.

        As ``before_insert``; raise to refuse the update. It is called for every
        instance the session considers dirty, including one that will issue no
        ``UPDATE`` because its only change is a relationship or a value set back
        to what it was. ``has_changed`` tells which fields changed. For a value
        set on every ``UPDATE``, use ``mapped_column(onupdate=...)``. A model with
        this hook cannot use ``Model.assign()``.
        """

    def before_delete(self) -> None:
        """Called by the flush that deletes this row, before its ``DELETE``.

        Raise to refuse the delete. It runs inside the flush, as the other hooks do.
        """

    async def load[T](self, attr: TypedAttribute[T], /) -> T:
        """Load an attribute of this instance, typed, where lazy loading cannot run.

            team = await hero.load(Hero.team)   # Team | None

        For an ``AsyncSession``, which cannot lazy-load on attribute access. An
        attribute that is already loaded is returned without a query. Raises
        ``TypeError`` for another model's attribute.
        """
        if not isinstance(self, attr.parent.class_):
            raise TypeError(f"{attr} is not an attribute of {type(self).__name__}")
        return await greenlet_spawn(getattr, self, attr.key)

    def __delattr__(self, name: str) -> None:
        """Delete through the ORM descriptor, if there is one, and unmark the field as set."""
        cls = self.__class__
        attr = getattr(cls, name, None)
        if attr is not None and hasattr(attr, "__delete__"):
            attr.__delete__(self)
            if name in cls.__pydantic_fields__:
                self.__pydantic_fields_set__.discard(name)
            return
        super().__delattr__(name)

    # Copying and model_construct are prohibited to preserve SQLAlchemy instance state.
    if TYPE_CHECKING:
        # Statically disallow model_copy and model_construct calls.
        # __replace__ supplies parameter types for update and assign.
        # pylint: disable-next=arguments-differ,unused-argument
        def model_copy(self, use_the_constructor_instead: Never, /) -> Self: ...  # type: ignore[override]  # ty: ignore[invalid-method-override]
        @classmethod
        # pylint: disable-next=arguments-differ,unused-argument
        def model_construct(cls, use_the_constructor_instead: Never, /) -> Self: ...  # type: ignore[override]  # ty: ignore[invalid-method-override]

        # Only table models have instances, and they hash by ``_compare_table_rows_by_identity``.
        def __hash__(self) -> int: ...

    else:

        def __copy__(self):
            raise _copy_error(type(self))

        def __deepcopy__(self, memo=None):
            raise _copy_error(type(self))

        def __replace__(self, /, **changes):  # pylint: disable=arguments-differ
            raise _copy_error(type(self))

        @classmethod
        def model_construct(cls, *args, **kwargs):
            raise TypeError(
                f"{cls.__name__}.model_construct() would build an instance without SQLAlchemy's "
                f"instance state. Construct it with {cls.__name__}(...)."
            )

    def __repr__(self) -> str:
        # Read primary key from instance state without refreshing attributes.
        state = cast("InstanceState[Self] | None", sa_inspect(self, raiseerr=False))
        if state is None:
            return super().__repr__()
        mapper = state.mapper
        keys = [mapper.get_property_by_column(column).key for column in mapper.primary_key]
        values = state.identity or tuple(state.dict.get(key) for key in keys)
        pk = ", ".join(f"{key}={value!r}" for key, value in zip(keys, values, strict=True))
        return f"<{type(self).__name__}({pk})>"

    @classmethod
    def where(
        cls: type[Self], *crit: ColumnElement[bool], prefetch: "Iterable[Path[Self, Any]]" = ()
    ) -> Select[tuple[Self]]:
        """``sqlalchemy.select(cls).where(*crit)``, loading ``prefetch`` with the rows.

            Team.where(Team.name == "red", prefetch=[Team.heroes, path(Team.heroes, Hero.powers)])

        Each path must start at this model, and each step where the last one
        ended, or it is a type error. See ``alchemic.prefetch`` for how each step
        is loaded.
        """
        statement = select(cls).where(*crit)
        paths = tuple(prefetch)
        return statement.options(*_prefetch(cls, *paths)) if paths else statement


# Propagated to every mapped subclass, in every registry. Pairing runs before configuration,
# which reads ``back_populates``; the optionality check after it, once directions are known.
event.listen(Model, "before_mapper_configured", pair_relationships, propagate=True)
event.listen(Model, "mapper_configured", check_relationship_optionality, propagate=True)

install_change_recording(Model)


def _skipped_by_bulk_update(model: type[Model]) -> list[str]:
    """Name alchemic's per-object code that a bulk ``UPDATE`` of ``model`` would skip."""
    skipped: list[str] = []
    if _overrides(model, "before_update"):
        skipped.append(f"{model.__name__}.before_update()")
    if receivers_for(model, Updated):
        skipped.append("its on_commit receivers")
    return skipped


def _ensure_registry(
    metadata: MetaData | None, registry: orm_registry | None
) -> tuple[orm_registry, MetaData]:
    """Return the ``(registry, metadata)`` pair to build a model base against."""
    if registry is not None:
        return registry, registry.metadata
    meta = metadata if metadata is not None else MetaData()
    return orm_registry(metadata=meta), meta


def _build_model_base(reg: orm_registry, meta: MetaData, name: str) -> type[Model]:
    """Create the concrete ``Model`` subclass bound to ``reg``/``meta``."""

    class CustomModel(Model):
        """``Model`` bound to ``reg`` and ``meta``."""

        __abstract__ = True
        # Bind both _sa_registry and registry for declarative mapping.
        _sa_registry = reg
        registry = reg
        metadata = meta

    CustomModel.__name__ = name
    return CustomModel


def create_model_base(
    *,
    metadata: MetaData | None = None,
    registry: orm_registry | None = None,
    name: str = "Model",
) -> type[Model]:
    """Create a new ``Model`` base class bound to ``registry``, or to ``metadata``.

    With neither, the base gets a new ``MetaData``. mypy rejects the returned class
    as a base class, since it is a variable; declaring the base is equivalent and
    type-checks in mypy and pyright:

        class Base(Model):
            __abstract__ = True
            registry = shared_registry   # or: metadata = MetaData()
    """
    reg, meta = _ensure_registry(metadata, registry)
    return _build_model_base(reg, meta, name)
