# Codebase Architecture & Internal Mechanics

This document provides a deep architectural walkthrough of `alchemic`'s codebase, explaining how it bridges SQLAlchemy 2.0 and Pydantic v2.

---

## 1. Module Structure (`src/alchemic/`)

- `__init__.py`: Public API re-exports, package version.
- `_constants.py`: Names of the class attributes and `info` keys that `alchemic` writes onto models and columns.
- `model.py`: Core `Model` base class, `ModelMeta` metaclass, lifecycle hooks, `update`, `assign`, `load`.
- `_typing.py`: Typed declarations for type checkers (`Mapped`, `TypedAttribute`, `Related`, `RelatedAttribute`, `Path`, `references`, `mapped_column`, `col`, `relationship`, `link`, `prefetch`).
- `_fields.py`: Pydantic field reconciliation, default rebuilding, constraint tightening (`String` max length, `Numeric` and `ScaledDecimal` precision/scale), read-only detection.
- `_mapped_schema.py`: Pydantic core schema hook for `Mapped[T]`.
- `_relationships.py`: Runtime relationship utilities (`path`, `prefetch`, `link`, reverse relationship pairing, and optionality checks).
- `_receivers.py`: Transaction change tracking (`on_commit` receivers, SQLAlchemy event listeners for `after_insert`, `after_update`, `after_delete`, `after_commit`, `after_transaction_end`).
- `_metadata.py`: `Annotated` metadata extraction and normalization.
- `extensions.py`: `Extension`, `FieldExtension`, and `get_field_metadata`.
- `session.py`: `Session` and `AsyncSession` subclasses implementing arity-based `exec()` shaping.
- `types.py`: `ScaledDecimal`, a `Decimal` column type stored as a scaled `BIGINT`.
- `sqlite.py`: `configure_sqlite`, which sets SQLite's PRAGMAs, emits `BEGIN` in the mode each transaction asks for, and refuses row locks.
- `_sqlalchemy_private.py`: Encapsulated access to SQLAlchemy internal attributes and methods (pinned to SQLAlchemy `<2.1`).

---

## 2. Core Mechanism: Fusing Metaclasses

The central challenge in combining SQLAlchemy declarative models with Pydantic models is that both use metaclasses (`DeclarativeAttributeIntercept` for SQLAlchemy, `ModelMetaclass` for Pydantic).

In `model.py`, `ModelMeta` fuses both metaclasses directly:
```python
class ModelMeta(ModelMetaclass, DeclarativeAttributeIntercept):
    ...
```
Mypy rejects a metaclass built with `type(...)`, so `ModelMeta` names `ModelMetaclass` (from `pydantic._internal`) and `DeclarativeAttributeIntercept` directly.

### The `Mapped[T]` Pydantic Hook (`_mapped_schema.py`)
Pydantic normally raises a `PydanticSchemaGenerationError` when encountering `Mapped[T]` because it doesn't know how to validate it. `alchemic` adds a core schema generation method to SQLAlchemy's `Mapped` class. `model.py` calls `install_mapped_schema_hook()` at import time, and the hook does nothing if SQLAlchemy already provides one:
```python
def _mapped_core_schema(_cls: Any, source: Any, handler: Any) -> Any:
    args = get_args(source)
    return handler.generate_schema(args[0] if args else Any)

def install_mapped_schema_hook() -> None:
    if hasattr(Mapped, "__get_pydantic_core_schema__"):
        return
    Mapped.__get_pydantic_core_schema__ = classmethod(_mapped_core_schema)
```
This unwraps `Mapped[T]` into `T` during Pydantic schema generation without altering runtime annotations.

---

## 3. Field Reconciliation (`_fields.py`)

When a model class is created, `repair_model_fields()` runs via `ModelMeta`:
1. **Pruning non-column attributes**: Relationships, unannotated descriptors, and class attributes that are not SQLAlchemy columns are removed from Pydantic's `model_fields` and recorded in `__alchemic_non_columns__`.
2. **Unwrapping `Mapped` & `Annotated`**: Pydantic annotations are cleaned from `Mapped[T]` to `T`. Any `Annotated` metadata (like `Extension` or custom validators) is extracted and preserved.
3. **Rebuilding Defaults**: A field is optional in the Pydantic constructor *only* if its column declaration specifies a default (`default=...`, `default=None`). Nullable columns without defaults remain required constructor arguments. SQLAlchemy callable defaults (like `default=uuid.uuid4` or `default=utcnow`) are wrapped into clean no-argument Pydantic default factories.
4. **Constraining Column Types**: Automatically tightens Pydantic validation limits based on SQLAlchemy column definitions (e.g., `String(80)` automatically applies `max_length=80`; `Numeric(precision=10, scale=2)` enforces `max_digits` and `decimal_places`).
5. **Read-only Detection**: Identifies `readonly=True` fields and version columns (`version=True`), marking them as `readOnly` in JSON schemas and guarding them against post-creation edits.

---

## 4. Relationship Pairing & Optionality (`_relationships.py`)

`alchemic` automatically wires bidirectional relationships and validates optionality:
- **Reverse Pairing (`pair_relationships`)**: Triggered on `before_mapper_configured`. For relationships lacking explicit `back_populates`, alchemic inspects target mappers and automatically pairs them if exactly one matching reverse relationship exists. With more than one candidate, or with a rival relationship already on that side, it raises `TypeError` asking for `back_populates`. Relationships that set `foreign_keys`, `primaryjoin`, `secondaryjoin`, `remote_side`, `viewonly` or `backref` are skipped, so their `back_populates` is never set automatically.
- **Optionality Check (`check_relationship_optionality`)**: Triggered on `mapper_configured`. If a single-object relationship points to a nullable foreign key (or is the parent side of a one-to-one), alchemic verifies that its type annotation allows `None` (e.g., `Related[Project | None]`). If not, it raises a helpful `TypeError`.

---

## 5. Change Recording & Transaction Pipeline (`_receivers.py`)

To support `@on_commit` receivers without tying alchemic to a specific session implementation, event listeners are attached to SQLAlchemy's base `Model` and `Session`:
- **Mapper Events**: `after_insert`, `after_update`, and `after_delete` record changes (`Inserted`, `Updated`, `Deleted`) into `session.info`. For updates, changed attribute keys are computed from SQLAlchemy instance state history (`state.attrs[attr.key].history.has_changes()`).
- **Session Events**:
  - `after_commit`: Folds committed savepoints into parent layers or stores changes for the outermost commit.
  - `after_soft_rollback`: Cleans up pending records on rollback.
  - `after_transaction_end`: When the outermost transaction commits successfully, `_dispatch()` runs all matching registered receivers.
- **Error Handling**: Receiver errors are collected and raised as an `ExceptionGroup` after the database transaction has committed, even when only one receiver fails.

---

## 6. Private API Encapsulation (`_sqlalchemy_private.py`)

To interact with SQLAlchemy internals (such as `column._attribute_options`, `rel._init_args`, `mapper._props`, and `_MappedAnnotationBase`), `alchemic` isolates all private attribute accesses into `_sqlalchemy_private.py`. 
- **SQLAlchemy Version Pinning**: SQLAlchemy is pinned to `>=2.0.31,<2.1` because 2.1 types statements as `Select[*Ts]`, while alchemic's signatures use `Select[tuple[...]]`. The pin also covers the private attributes listed in this section.
- `tests/test_private_api.py` explicitly tests these private access points to ensure resilience across patch releases.
