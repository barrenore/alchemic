# Interfaces & Public API Reference

This document details every public class, function, decorator, and type exported by `alchemic`. The rules and examples are in [Relationships](relationships.md), [Typing](typing.md) and [Errors](errors.md).

## Example models

Sections 2 to 5 use these models. Each relationship is declared once, on the side that holds it.

```python
import uuid
from alchemic import Mapped, Model, Related, mapped_column, references, relationship

class Project(Model):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str]
    archived: Mapped[bool] = mapped_column(default=False)
    issues: Related[list["Issue"]] = relationship()

class Issue(Model):
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    project_id: Mapped[uuid.UUID] = mapped_column(references(Project.id, ondelete="CASCADE"))
    project: Related[Project] = relationship()
    labels: Related[list["Label"]] = relationship()

class Label(Model):
    __tablename__ = "labels"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    issues: Related[list[Issue]] = relationship()
```

---

## 1. Models & Metaclasses

### `Model`
The primary base class for all alchemic database models. It inherits from Pydantic's `BaseModel`, SQLAlchemy's `DeclarativeBase` (`DeclarativeRoot`), and uses `ModelMeta`.

- **Validation**: Instances validate upon `__init__()` and when assignments are made (if `validate_assignment=True` or via `update()`). Rows loaded from the database bypass validation for performance and backward compatibility.
- **Identity & Hashing**: Table model instances are equal only to themselves (object identity `is`) and hash by object identity, making them safe to store in `set`s or use as dictionary keys.
- **Immutability & Safety**: 
  - `model_copy()`, `copy.copy()`, `copy.deepcopy()`, `copy.replace()` and `model_construct()` are disabled and raise `TypeError`, to prevent detached or corrupted SQLAlchemy instance state.
  - Instances cannot be copied; construct a new instance or use `update()` instead.

### `create_model_base`
Signature: `create_model_base(*, metadata=None, registry=None, name="Model")`.

Factory function that creates a new `Model` base class bound to a custom SQLAlchemy `registry` and/or `MetaData`. 

```python
from sqlalchemy.orm import registry
from alchemic import create_model_base

shared_registry = registry()
Base = create_model_base(registry=shared_registry, name="Base")
```
To map a model hierarchy into a shared registry, subclass `Model` with a `registry` attribute:

```python
from sqlalchemy.orm import registry
from alchemic import Mapped, Model, mapped_column

shared_registry = registry()

class Base(Model):
    __abstract__ = True
    registry = shared_registry

class Thing(Base):
    __tablename__ = "things"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
```

### `ModelMeta`
The metaclass of `Model`, fusing Pydantic's `ModelMetaclass` and SQLAlchemy's `DeclarativeAttributeIntercept`.

### `DeclarativeRoot`
The SQLAlchemy declarative base root (`DeclarativeBase`).

---

## 2. Fields & Column Declarations

### `Mapped`
At runtime this is SQLAlchemy's `sqlalchemy.orm.Mapped`, patched so Pydantic validates `Mapped[T]` as `T`. For type checkers it is alchemic's own class, so import it from `alchemic`.
- At class level, `Mapped[T]` is a `TypedAttribute[T]`, which carries typed comparison operators.
- At instance level, it is a plain `T`.
- For NULL checks use `.is_(None)`; comparisons accept `T`.

### Declaring columns
Three forms, from least to most explicit:

```python
from alchemic import Mapped, Model, mapped_column

class Author(Model):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)  # options needed
    name: Mapped[str]                                              # NOT NULL, required argument
    pen_name: Mapped[str | None]                                   # nullable, still a required argument
    rating: Mapped[int] = mapped_column(default=1)                 # optional argument
```

- A bare `Mapped[T]` annotation is a column. `T` maps to `NOT NULL`; `T | None` maps to a nullable column.
- A field is optional in the constructor only when its column has a `default=`. A nullable column without a default is still a required argument: `Author(name="Ada")` raises `ValidationError`.
- Use `mapped_column(...)` for anything an annotation cannot express: a primary key, a foreign key (`references`), `default`, `server_default`, `unique`, `index`, a column type such as `String(80)`, or `init=False`, `readonly=True` and `version=True`.
- A primary key must be declared with `mapped_column(primary_key=True)`. A bare `id: Mapped[int]` raises `ArgumentError` when the class is mapped.
- A Pydantic `Field(...)` on an attribute that is not a `Mapped[...]` column is removed from the model: the instance has no such attribute, and passing it to the constructor raises `ValueError`. For a derived value, use a `@property`.

### `mapped_column(...)` and `col(...)`
Declares a database column and is typed against the annotation. `col` is the same function under a shorter name: `id: Mapped[int] = col(primary_key=True, init=False)`. It is not in a released version yet.
- Accepts SQLAlchemy's arguments (`primary_key`, `nullable`, `default`, `onupdate`, `server_default`, `unique`, `index`, etc.), except a `Column` object: pass its type and constraints directly.
- Special alchemic arguments:
  - `init=False`: Leaves the field out of the constructor, for values the database generates.
  - `readonly=True`: The constructor can set the field. Changing it later, while it holds a non-`None` value, raises `ValidationError` through assignment or `update()`; `assign()` raises `TypeError`. Marked as `readOnly` in JSON schemas.
  - `version=True`: Configures optimistic locking (`version_id_col`), starting at 1. Declare it with `init=False`; otherwise it is a required constructor argument.

```python
from datetime import UTC, datetime
from sqlalchemy import DateTime
from alchemic import Mapped, Model, mapped_column

def utcnow() -> datetime:
    return datetime.now(UTC)

class Event(Model):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, readonly=True)
```

**Limit:** a reusable alias such as `type Pk = Annotated[int, mapped_column(primary_key=True, init=False)]` maps correctly at runtime, but type checkers do not read the column options from `Annotated`. With that alias, Pyright and Mypy still require the `id` argument. Write `mapped_column(...)` on the field itself when the options affect the constructor.

### `references`
Signature: `references(column, ...)`.

Declares a foreign key on a column. The column is checked against the referenced column's type. Keyword arguments are SQLAlchemy's `ForeignKey` options (`ondelete`, `onupdate`, `deferrable`, `name`, and so on). See [Foreign keys](relationships.md#foreign-keys).

Shown on `Issue` in the example models above:

```python
project_id: Mapped[uuid.UUID] = mapped_column(references(Project.id, ondelete="CASCADE"))
```

---

## 3. Relationships & Navigation

The rules, examples and error messages are in [Relationships](relationships.md). This section lists the signatures.

### `Related`
The relationship equivalent of `Mapped`. At class level it gives a `RelatedAttribute`, which `path`, `prefetch` and `link` check. At runtime it is `Mapped`.
```python
issues: Related[list["Issue"]] = relationship()
project: Related[Project | None] = relationship()
```
*(A single-object relationship over a nullable foreign key must be annotated `| None`. See [Nullability](relationships.md#nullability).)*

### `relationship(...)`
Re-exported from SQLAlchemy (`sqlalchemy.orm.relationship`) at runtime. Type checkers see it typed as returning `Any`, so the `Related[...]` annotation supplies the type.

### `link`
Signature: `link(left, right, /, *, name=None, prefixes=None) -> Table`.

Creates the association table for a many-to-many relationship and wires both sides together (`back_populates` and `secondary`). `left` and `right` are positional-only. `prefixes` is required when both sides belong to one class. Call it before the first query. See [Many-to-many](relationships.md#many-to-many).
```python
issue_label = link(Issue.labels, Label.issues, name="issue_labels")
```

### `path`
Signature: `path(*steps)`, returning `Path[O, E]`.

A path of relationship attributes, each step starting where the previous one ended. Type checkers accept two to four steps and check the chain. At runtime `path()` accepts two or more steps and does not check the chain. To prefetch a single relationship, pass the attribute itself rather than a path.
```python
p = path(Project.issues, Issue.labels)
```

### `prefetch`
Signature: `prefetch(root, *paths)`.

Generates SQLAlchemy loader options (`selectinload` for collections, `joinedload` for single objects) for the given paths, loading every step along the way. Raises `TypeError` if a path does not start at `root`.
```python
project = session.get(Project, project_id, options=prefetch(Project, Project.issues))
```

---

## 4. Queries, Sessions & Execution

### `Model.where`
Classmethod: `where(*criteria, prefetch=())`.

Classmethod returning `select(Model).where(*criteria)`. Each criterion is a SQL expression. A mistyped comparison such as `Issue.id == "1"` is a type error, because the comparison types as `bool`; see [Typing](typing.md#comparisons).
- `prefetch`: a sequence of paths, or single relationship attributes. The rows load with their related objects, so the relationships need no lazy loads.
```python
session.exec(Project.where(Project.name == "Billing", prefetch=[path(Project.issues, Issue.labels)]))
```

### `Session`
A subclass of SQLAlchemy's `Session` adding the `exec(...)` method.
- `exec(statement, params=..., ...)` executes statements and automatically shapes the result based on arity:
  - 1 entity/column selected -> `ScalarResult[T]`
  - Multiple entities/columns selected -> `TupleResult[tuple[...]]`
  - DML with `.returning(...)` -> rows, shaped as a scalar or tuple result
  - DML without `RETURNING` -> `CursorResult[Any]`
  - Bulk write (list of parameter mappings) -> `Result[Any]`

### `AsyncSession`
A subclass of SQLAlchemy's `AsyncSession`.
- Defaults `expire_on_commit=False` (preventing `MissingGreenlet` errors in async `on_commit` receivers).
- Provides `exec(...)` (buffered) and `stream_exec(...)` (unbuffered streaming). `stream_exec` accepts statements that return rows, including DML with `.returning(...)`, and returns `AsyncScalarResult` or `AsyncTupleResult`.

### `async_sessionmaker`
Alchemic's wrapper around SQLAlchemy's `async_sessionmaker`. It produces `AsyncSession` instances with `expire_on_commit=False`.

### Statement helpers
`select`, `insert`, `update` and `delete` are re-exported from SQLAlchemy unchanged. Use `Model.where` for a filtered select on one model and these for everything else.

---

## 5. Instance Operations & Writes

### `Model.update`
Signatures: `update(patch)` and `update(**fields)`.

Applies partial updates to an instance and returns `self`.
- Validates all values (as `validate_assignment=True`), operating all-or-nothing.
- Accepts keyword arguments, which type checkers check, or a Pydantic model or mapping, which is checked at runtime. An unknown name raises `ValidationError`.
- Respects `init=False` and `readonly=True`: an `init=False` field raises `ValidationError`, as it does in the constructor, and a `readonly=True` field that holds a value raises `ValidationError` when it changes.

### `Model.assign`
Classmethod: `assign(**values)`.

Returns a SQLAlchemy `Update` statement for bulk updates.
- Keyword names are checked against the model's fields by type checkers.
- Refuses relationships, `readonly=True` fields, and models with `before_update` hooks or `on_commit` update receivers (which bulk updates bypass). Type checkers accept relationship names here; see [Typing](typing.md#known-gaps).
```python
session.exec(Project.assign(archived=True).where(Project.id == project_id))
```

### `Model.load`
Async: `async def load(attr)`.

Async lazy loader for attributes on an `AsyncSession` instance where synchronous lazy loading cannot execute.
```python
project = await issue.load(Issue.project)
```

### `Model.has_changed`
Signature: `has_changed(attr)`.

Returns `bool` indicating whether the given attribute has unflushed changes. Used inside `before_update` hooks. In `on_commit` receivers the commit has already happened, so use `change.has_changed(...)` instead.

---

## 6. Lifecycle Hooks

Override these methods on a `Model` subclass to execute custom logic during flushes:
- `before_insert(self) -> None`: Runs just before the `INSERT` statement.
- `before_update(self) -> None`: Runs for each instance the session considers dirty, just before its `UPDATE` statement. This includes an instance with no net change, such as a value set back to what it held. `has_changed` tells which fields changed. Raise an exception to abort.
- `before_delete(self) -> None`: Runs just before the `DELETE` statement. Raise an exception to abort.

*(Note: Hooks must be synchronous plain methods, not `async def`.)*

---

## 7. `on_commit` Receivers

### `on_commit`
Decorator: `@on_commit(Model, *kinds)`.

Decorator registering a function to run after the outermost transaction successfully commits. The function is called as `receiver(instance, change)`, where `change` is an `Inserted`, `Updated` or `Deleted` value.
- `kinds`: Zero or more of `Inserted`, `Updated`, `Deleted`. If none are given, listens to all change kinds.
- Can be synchronous or asynchronous (`async def`). Async receivers are run correctly inside greenlets when committing via `AsyncSession`.
- Failures are raised as an `ExceptionGroup` after the data has committed, even when only one receiver fails. Passing an async receiver to a sync `Session` counts as a failure.

### `Change` Types
- `Inserted`: Row was inserted.
- `Updated(fields: frozenset[str])`: Row was updated. Provides `change.has_changed(Model.attr)` to check specific changed fields.
- `Deleted`: Row was deleted.

---

## 8. Field Metadata

### `Extension` and `FieldExtension`
Declare metadata on fields using `Annotated`:
```python
from typing import Annotated
from alchemic import Extension, Mapped, Model, mapped_column

class Upload(Model):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    file: Mapped[Annotated[str, Extension(file_field=True, allow_upload=True)]]
```

`editable`, `file_field` and `allow_upload` are typed keys. A key left `None` is omitted from `as_dict()`. `extra` takes a mapping of keys the class does not name.

`FieldExtension(**kwargs)` takes arbitrary keyword arguments, and compares and hashes by its contents. Use it when the keys are not known in advance:

```python
note: Mapped[Annotated[str, FieldExtension(widget="textarea")]]
```

### `get_field_metadata`
Signature: `get_field_metadata(cls_or_instance, field_name)`, also available as `get_field_info`.

Retrieves merged field metadata combining SQLAlchemy column `info`, `Annotated` extension attributes, and `json_schema_extra`.
