# Guides & Recipes

This document covers common design patterns, advanced features, and best practices when building applications with `alchemic`.

---

## 1. Inheritance

### Abstract Base Classes
To share common columns (like `id`, `created_at`, `updated_at`) across multiple models without mapping them to a table themselves, use `__abstract__ = True`:

```python
import uuid
from datetime import datetime, UTC
from sqlalchemy import DateTime
from alchemic import Mapped, Model, mapped_column

class TimestampedModel(Model):
    __abstract__ = True
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, readonly=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), readonly=True
    )

class User(TimestampedModel):
    __tablename__ = "users"
    name: Mapped[str]
```

### Joined Table Inheritance
Inheriting from a mapped model maps subclasses to their own tables joined to the parent table:

```python
from alchemic import Mapped, Model, mapped_column, references

class Content(Model):
    __tablename__ = "contents"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    title: Mapped[str]

class Article(Content):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(references(Content.id, ondelete="CASCADE"), primary_key=True, init=False)
    body: Mapped[str]
```

The subclass key is filled from the parent row on insert, so `init=False` keeps it out of the constructor.

---

## 2. Read-Only Fields & Optimistic Locking

### Read-Only Fields
Marking a column with `readonly=True` lets the constructor set it. Once it holds a value, it cannot be changed by direct assignment or `update()`, and `assign()` refuses it. It can still be set while it is `None`:

```python
from alchemic import Mapped, Model, mapped_column

class AuditLog(Model):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    action: Mapped[str] = mapped_column(readonly=True)
    details: Mapped[str]
```

### Optimistic Locking
Use `version=True` to enable SQLAlchemy's optimistic locking. An automatic version counter starts at 1, and saving stale instances raises `StaleDataError`:

```python
from alchemic import Mapped, Model, mapped_column

class Document(Model):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    title: Mapped[str]
    version: Mapped[int] = mapped_column(version=True, init=False)
```

---

## 3. Lifecycle Hooks

For logic spanning multiple fields, override `before_insert`, `before_update`, or `before_delete`.

```python
from datetime import UTC, datetime
from sqlalchemy import DateTime
from alchemic import Mapped, Model, mapped_column

class Task(Model):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    status: Mapped[str] = mapped_column(default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def before_update(self) -> None:
        if self.has_changed(Task.status):
            if self.status == "completed":
                self.completed_at = datetime.now(UTC)
            else:
                self.completed_at = None

    def before_delete(self) -> None:
        if self.status == "in_progress":
            raise PermissionError("Cannot delete an in-progress task.")
```
*Note: Hooks run synchronously inside flushes; `async def` hooks are rejected at class creation time.*

---

## 4. Reusing Column Definitions

To share a column definition across models with its static typing intact, write a helper function that returns `Mapped[T]`. The type checker treats the helper's call as a default value, so a helper that returns a column with `default=` makes the constructor argument optional, as it is at runtime. A helper that returns a required column is typed as optional, yet the runtime requires the argument. Give such a column a default, or write `mapped_column(...)` on the field. `Annotated` aliases have the same limit (see [Declaring columns](interfaces.md#declaring-columns)).

```python
from datetime import UTC, datetime
from sqlalchemy import DateTime
from alchemic import Mapped, Model, mapped_column

def utcnow() -> datetime:
    return datetime.now(UTC)

def timestamp_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, readonly=True)

class Comment(Model):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    body: Mapped[str]
    created_at: Mapped[datetime] = timestamp_column()
```

---

## 5. Async Workflows & `on_commit` Receivers

With `AsyncSession`:

- Use `async_sessionmaker`, which creates sessions with `expire_on_commit=False`.
- Use `await instance.load(Model.relationship)` to load a relationship without a sync lazy load.
- Register `async def` `@on_commit` receivers for side effects, such as notifications or cache invalidation. They run after a successful commit.

The example below uses `Task` from section 3. `send_notification` stands for your own notifier.

```python
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from alchemic import Change, Inserted, Model, Updated, async_sessionmaker, on_commit

async def send_notification(message: str) -> None:
    ...

@on_commit(Task)
async def notify_task_update(task: Task, change: Change) -> None:
    match change:
        case Inserted():
            await send_notification(f"New task created: {task.id}")
        case Updated() if change.has_changed(Task.status):
            await send_notification(f"Task {task.id} status changed to {task.status}")
        case _:
            pass

async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///tasks.db")
    async with engine.begin() as connection:
        await connection.run_sync(Model.metadata.create_all)
    maker = async_sessionmaker(engine)
    async with maker() as session:
        task = Task()
        session.add(task)
        await session.commit()
        task.status = "completed"
        await session.commit()

asyncio.run(main())
```

---

## 6. PostgreSQL Types & Existing Tables

Any SQLAlchemy column type works as the argument to `mapped_column`. Give the field the Python type the column returns; the type checker and Pydantic validation both read the annotation.

### JSONB

```python
from typing import Any
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from alchemic import Mapped, Model, mapped_column

class Document(Model):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    body: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSONB), default=dict)
```

Assigning a new value (`doc.body = {...}`) is always tracked. Changing a value in place (`doc.body["k"] = 1`) is tracked only when the column is wrapped in `MutableDict.as_mutable` (or `MutableList` for lists); otherwise the change is not flushed.

### pgvector and PostGIS

Install `pgvector` or `geoalchemy2` and use their column types directly. The database needs `CREATE EXTENSION vector` / `CREATE EXTENSION postgis`.

```python
from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector

class Place(Model):
    __tablename__ = "places"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(3))
    location: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))

nearest = session.exec(Place.where().order_by(Place.embedding.l2_distance([1, 2, 3]))).first()
```

`Geometry` values load as `geoalchemy2.WKBElement`; annotate the field `Any`. A string such as `"SRID=4326;POINT(1 2)"` is accepted on construction.

### Mapping an existing table

Set `__table__` to a `Table` (reflected with `autoload_with=engine`, or declared on the model base's `metadata`) and annotate each column you want as a field. Annotations carry the types; columns without an annotation are mapped by SQLAlchemy but are not model fields. Do not repeat `mapped_column` for columns the table already defines.

```python
table = Table("legacy_orders", Model.metadata, autoload_with=engine)

class LegacyOrder(Model):
    __table__ = table
    id: Mapped[int]
    label: Mapped[str]
    extra: Mapped[dict[str, Any] | None]
```

A model over a reflected table must be declared after reflection, so it needs a live connection at import time.

---

## 7. SQLite: Money, Locks and Transactions

One SQLite file per company works well with `configure_sqlite`, and two things need care. A `Numeric` column is stored as a float, so sums drift. SQLite also has no row locks, and its default transaction mode can fail with `database is locked` where a wait would succeed.

`ScaledDecimal(precision, scale)` stores the value as an integer count of `10 ** -scale` units, so `SUM` and comparisons are exact. Reads return a `Decimal` with exactly `scale` places. A value with more decimal places than `scale` raises `ValueError`; it is never rounded.

Arithmetic in SQL stays exact or is refused. `Entry.debit - Entry.credit`, `Entry.amount + Decimal("1.50")` and `Entry.amount * Entry.quantity` (an integer column) return `ScaledDecimal` values; `/`, `%` and `*` by a `Decimal` raise `TypeError` when the expression is built, so do that maths in Python. Put the `ScaledDecimal` column on the left of an operator, and compute an average as `func.sum` divided by `func.count`, since `func.avg` returns raw units.

`configure_sqlite` sets foreign keys on, a 5-second busy timeout and WAL journaling on each connection, and takes over transaction control so that each transaction's `BEGIN` mode can be chosen. Call it before the engine's first connection.

```python
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from alchemic import Mapped, Model, ScaledDecimal, Session, configure_sqlite, mapped_column


class LedgerEntry(Model):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    amount: Mapped[Decimal] = mapped_column(ScaledDecimal(18, 4))


engine = create_engine("sqlite:///company-42.db")
configure_sqlite(engine)
Model.metadata.create_all(engine)

writer = engine.execution_options(alchemic_sqlite_begin="IMMEDIATE")
WriterSession = sessionmaker(bind=writer, class_=Session)
with WriterSession() as session:
    session.add_all([LedgerEntry(amount=Decimal("0.1")) for _ in range(3)])
    session.commit()

with Session(engine) as session:
    session.scalars(select(func.sum(LedgerEntry.amount))).one()  # Decimal('0.3000')
```

The `alchemic_sqlite_begin` execution option chooses the `BEGIN` mode of a transaction:

- `"DEFERRED"` (the default) takes its locks at the first read or write. A transaction that reads and then writes can fail at that write with `database is locked`, without waiting.
- `"IMMEDIATE"` takes the write lock at `BEGIN`, so the busy timeout applies before any work is done. Use it for transactions that will write.
- `"EXCLUSIVE"` also blocks readers in rollback-journal mode. With WAL it behaves like `IMMEDIATE`.

Set the option on an engine, on a connection (`engine.connect().execution_options(...)`), or on a session as the first call in its transaction: `await session.connection(execution_options={"alchemic_sqlite_begin": "IMMEDIATE"})`.

`with_for_update()`, including `with_for_update(read=True)` for `FOR SHARE`, raises `CompileError` on these connections. That holds for `session.execute`, `scalars`, `exec`, `get`, `refresh`, `stream_exec` and `connection.execute` alike. To serialize a read-then-write transaction, run it with `"IMMEDIATE"` instead. That takes the database's write lock before its first statement.
