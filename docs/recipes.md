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
