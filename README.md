# alchemic

**alchemic** lets a single class be both a SQLAlchemy 2.0 declarative model and
a validating Pydantic v2 model. You declare each field once, with `Mapped[T]`,
and pyright and mypy can tell a column apart from a value.

```sh
pip install alchemic
```

alchemic requires Python 3.13 or later, SQLAlchemy 2.0.31 or later (2.1 is not
yet supported), and Pydantic 2.11 or later.

The reference for every public name, with its rules, typing details and error messages, is in [docs/](docs/index.md).

## Quickstart

```python
import uuid

from sqlalchemy import String, create_engine

from alchemic import Mapped, Model, Session, mapped_column


class Project(Model):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80))
    archived: Mapped[bool] = mapped_column(default=False)


engine = create_engine("sqlite://")
Model.metadata.create_all(engine)

with Session(engine) as session:
    session.add(Project(name="Billing"))  # validated by Pydantic
    session.commit()
    billing = session.exec(Project.where(Project.name == "Billing")).one()  # a Project
```

On the class, `Project.name` is a `TypedAttribute[str]`: an
`InstrumentedAttribute` you can query with, whose comparisons are typed against
`str`. On an instance, `billing.name` is a plain `str`.

## Why alchemic

SQLModel annotates columns as plain values, such as `str` or `UUID`, because
Pydantic cannot build a schema for `Mapped[str]`. As a result, a type checker
cannot tell a column from a value. In practice this leads to generated stub
files, suppressed type errors, and mistakes like `where(Project.name in names)`,
which quietly compiles to `WHERE false`.

alchemic instead teaches Pydantic to read `Mapped[T]` as `T`. Both libraries
read the same annotation, and the type checker can see what is a column and
what is a value:

```python
names = ["Billing", "Payroll"]
project = Project(name="Billing")

Project.where(Project.name == "Billing")  # fine: the usual idiom still works
Project.where(Project.name.in_(names))    # fine
Project.where(Project.name in names)      # type error in pyright and mypy
Project.where(Project.archived == "no")   # type error: compared against the column's type
Project(nmae="Billing")                   # type error: constructors are checked
project.update(nmae="Billing")            # type error: so are partial updates


class Invoice(Model):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    amount_cents: Mapped[int] = mapped_column(String(12))  # type error: column type vs. annotation
```

Import `Mapped`, `mapped_column` and `relationship` from `alchemic` rather than
from `sqlalchemy.orm`. At runtime they are SQLAlchemy's own objects; type
checkers see stricter signatures that check comparisons and constructors. For
convenience, alchemic also re-exports `select`, `insert`, `update` and `delete`
from SQLAlchemy.

## Relationships

Declare a relationship once on each side, and alchemic pairs the two sides up.
Here a project has many issues, and issues and labels form a many-to-many:

```python
import uuid

from sqlalchemy import String

from alchemic import Mapped, Model, Related, link, mapped_column, path, prefetch
from alchemic import references, relationship


class Project(Model):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80))
    issues: Related[list["Issue"]] = relationship()


class Issue(Model):
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)  # assigned by the database
    title: Mapped[str] = mapped_column(String(200))  # a 201-character title is a ValidationError
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        references(Project.id, ondelete="CASCADE"), default=None
    )
    project: Related[Project | None] = relationship()  # must allow None: project_id is nullable
    labels: Related[list["Label"]] = relationship()


class Label(Model):
    __tablename__ = "labels"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str] = mapped_column(String(40))
    issues: Related[list[Issue]] = relationship()


issue_label = link(Issue.labels, Label.issues)  # the association table, wired to both sides

session.exec(Project.where(Project.name == "Billing", prefetch=[path(Project.issues, Issue.labels)]))
session.get(Issue, 1, options=prefetch(Issue, Issue.project))
path(Project.issues, Label.issues)  # type error: Label.issues does not start at Issue
```

These are ordinary SQLAlchemy relationships, so `Issue.project` also works with
`join()`, `selectinload()` and `any()`.

`references(Project.id)` declares a foreign key and checks it against the
annotation: `project_id: Mapped[int] = mapped_column(references(Project.id))` is
a type error, because `Project.id` is a `UUID`. Give a NOT NULL foreign key
`default=None` when it is usually set through its relationship. Then both
`Issue(title=..., project=billing)` and `Issue(title=..., project_id=billing.id)`
work.

A many-to-many relationship needs `link()`, which creates the association table.
The table has one column for each primary-key column on either side, and each
is a foreign key with `ON DELETE CASCADE`. Pass `name=` to name the table, and
`prefixes=` to prefix its columns; prefixes are required when both sides are the
same class. Call `link()` before running the first query.

## Construction and validation

Unlike SQLModel's `table=True` models, alchemic models validate on construction:
type checks, constraints and `@field_validator`s all apply. Rows loaded from the
database are not validated again, so reads cost nothing extra and rows written
under older rules can still be loaded.

A constructor argument is optional exactly when its declaration has a default,
including `mapped_column(default=None)`. A nullable column, or one with a
`server_default`, is still required unless it has a default. This is the same
rule Pydantic applies to `x: str | None`. `init=False` leaves a column out of
the constructor entirely, for values the database assigns, such as an
autoincrementing primary key.

An instance represents a row: it is equal only to itself, and it can be put in a
`set`. To compare data, compare `model_dump()` results. Copying an instance
raises an error, because the copy would share the original's SQLAlchemy state;
construct a new instance or `update()` the existing one instead.

## Writing

Writes are checked the same way:

```python
issue.update(title="Refunds fail for EUR invoices")  # validated, all or nothing
issue.update(patch)  # a Pydantic model or a mapping, as in a PATCH endpoint
session.exec(Label.assign(name="bug").where(Label.name == "defect"))  # a typed UPDATE
project = await issue.load(Issue.project)  # an async lazy load, typed Project | None
```

## Values the model maintains

Values the model maintains itself are declared on the column. `default=` runs
when the row is inserted, and `onupdate=` runs on every `UPDATE` of the row,
including bulk updates. Both accept any function, and its return type is checked
against the annotation:

```python
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime

from alchemic import Mapped, Model, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Comment(Model):
    __tablename__ = "comments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, readonly=True)
    body: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, readonly=True
    )
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    edited_on: Mapped[date] = mapped_column(Date, default=date.today, onupdate=date.today)
    version: Mapped[int] = mapped_column(version=True, init=False)  # optimistic locking
```

A `readonly=True` field is set by the constructor and cannot be changed
afterwards: assigning a different value, directly or through `update()`, raises
a `ValidationError`. Writing back the current value is allowed, as is filling in
a field that is `None`. Read-only fields are marked `readOnly` in the JSON
schema.

A `version=True` column is SQLAlchemy's `version_id_col`, starting at 1. Writing
an instance that was read before someone else's change raises `StaleDataError`.
The column is read-only, unless the application manages the version itself
(`version_id_generator=False` in `__mapper_args__`).

To reuse a column definition, write a plain function. It stays fully typed:

```python
def edited_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
```

## Hooks

For logic that spans several fields, override `before_insert`, `before_update`
or `before_delete`. Hooks run inside the flush, under an `AsyncSession` as well,
so they must be plain methods; an `async def` hook is rejected. `has_changed`
tells you what the current flush changes. Here an issue records when it was
closed:

```python
class Issue(Model):
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    status: Mapped[str] = mapped_column(String(20), default="open")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def before_update(self) -> None:
        if self.has_changed(Issue.status):  # a misspelled attribute is a type error
            self.update(closed_at=utcnow() if self.status == "closed" else None)

    def before_delete(self) -> None:
        if self.status != "closed":
            raise PermissionError("close an issue before deleting it")
```

A bulk `UPDATE` does not run per-object code, so `Issue.assign(...)` is refused
for a model that has a `before_update` hook or `on_commit` receivers for
updates. `sqlalchemy.update(Issue)` remains available as an escape hatch, and
bypasses both.

## Running code after commit

Code that should run only once data is committed, such as sending a
notification or invalidating a cache, belongs in an `on_commit` receiver.
Receivers run after the outermost transaction commits, and never after a
rollback or a rolled-back savepoint. `await session.commit()` waits for async
receivers to finish:

```python
from alchemic import Change, Deleted, Inserted, Updated, on_commit


async def notify(message: str) -> None: ...


@on_commit(Issue)
async def announce(issue: Issue, change: Change) -> None:
    match change:  # Change = Inserted | Updated | Deleted
        case Inserted():
            await notify(f"issue {issue.id} opened")
        case Updated() if change.has_changed(Issue.status):
            await notify(f"issue {issue.id} is now {issue.status}")
        case Updated() | Deleted():
            pass


@on_commit(Issue, Deleted)  # this receiver only hears about deletions
def forget(issue: Issue, change: Deleted) -> None: ...
```

Receivers are type-checked against the model and the kinds of change they
register for. A receiver that takes a `Project` but is registered for `Issue`,
or one that expects `Updated` when only `Deleted` is registered, is a type
error, and a `match` that misses a kind fails `assert_never`. A receiver
registered on a mixin hears about every model that inherits from it.

Receivers run for any SQLAlchemy session, not only alchemic's. If some of them
raise, the rest still run, and `commit()` then raises an `ExceptionGroup`; the
data has been committed by that point. Receivers are not durable, so work that
must not be lost belongs in a job queue.

## Sessions

`alchemic.Session` and `alchemic.AsyncSession` are SQLAlchemy's sessions with
one addition, `exec`, which types the result according to what the statement
selects. It works for any SQLAlchemy statement; SQLModel's `exec` does this only
for its own `select()`, and types a plain `sqlalchemy.select` as tuples even
though it returns rows. The async session also provides `stream_exec` for large
results, and does not expire objects on commit. Create it with
`alchemic.async_sessionmaker`, which keeps that default.

## Field metadata

You can attach metadata for forms, admin screens or API schemas to a field
through its `Annotated` type, and read it back with `get_field_metadata`:

```python
from typing import Annotated

from alchemic import Extension, FieldExtension, get_field_metadata


class Attachment(Model):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    file: Mapped[Annotated[str, Extension(file_field=True, allow_upload=True)]] = mapped_column()
    uploaded_by: Mapped[Annotated[str, FieldExtension(widget="user-picker")]] = mapped_column()


get_field_metadata(Attachment, "file")  # {'file_field': True, 'allow_upload': True}
```

`Extension` has typed keys (`editable`, `file_field`, `allow_upload`, plus
`extra` for anything else), while `FieldExtension` accepts any keyword
arguments. `get_field_metadata` merges the column's `info`, the `Annotated`
metadata and `json_schema_extra`, in that order; when two sources share a key,
the later one wins.

## Base classes and SQLModel

Every model inherits from `Model`, which has its own registry and `MetaData`. To
use a different registry, for example to share one with SQLModel during a
migration, declare an abstract base:

```python
from sqlalchemy.orm import registry

shared_registry = registry()


class Base(Model):
    __abstract__ = True
    registry = shared_registry  # or metadata = MetaData() for separate metadata
```

`create_model_base(registry=..., metadata=..., name=...)` builds the same class
at runtime. mypy does not accept a variable as a base class, though, so
declaring the base is the portable option.

`references()` also accepts a column from a plain SQLAlchemy model. SQLModel
declares its columns as plain values (`User.id: UUID`), so a SQLModel column
only type-checks there if you have stubs that type it as a column. Without such
stubs, use `ForeignKey("users.id")`.

## What importing alchemic changes

Importing alchemic changes two things outside its own classes:

- SQLAlchemy's `Mapped` gains a `__get_pydantic_core_schema__`, so that Pydantic
  validates `Mapped[T]` as `T`. This is skipped if SQLAlchemy already defines
  one.
- SQLAlchemy's `Session` gets `after_commit`, `after_soft_rollback` and
  `after_transaction_end` listeners, which is how every session runs `on_commit`
  receivers. When no receivers are pending, they cost a dictionary lookup.

## Status

alchemic is alpha software. The core is implemented and tested, but the API may
still change, and a 0.x minor release may contain breaking changes. See
[CHANGELOG.md](https://github.com/barrenore/alchemic/blob/main/CHANGELOG.md) for
release notes. Please report bugs at
[github.com/barrenore/alchemic/issues](https://github.com/barrenore/alchemic/issues).

## Acknowledgements

alchemic builds on the idea [SQLModel](https://github.com/fastapi/sqlmodel)
introduced: one class that serves as both the database model and the validated
data model. Thanks to Sebastián Ramírez and SQLModel's contributors for showing
how much that saves. alchemic takes a different path, keeping SQLAlchemy's own
`Mapped[T]` annotations so type checkers can follow along, but it would not
exist without SQLModel's example.

## License

MIT
