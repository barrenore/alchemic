# alchemic

**alchemic** merges SQLAlchemy 2.0 declarative models with Pydantic v2 validation in one class. Each field is declared once with `Mapped[T]`. Pyright and Mypy tell a column (`Author.name`) from a value (`author.name`) without generated stubs.

Requires Python 3.13+, SQLAlchemy 2.0.31 to 2.1 (exclusive), and Pydantic 2.11+.

The [README](../README.md) is the tour and the reasoning behind the design. These pages are the reference.

---

## Pages

| Page | What it covers |
|---|---|
| [Interfaces](interfaces.md) | Every public class, function, decorator and type, with its rules. |
| [Relationships](relationships.md) | Foreign keys, reverse sides, pairing, one-to-one, many-to-many, self-referential trees, loading. |
| [Typing](typing.md) | What Pyright and Mypy check, the types they report, and the known gaps. |
| [Errors](errors.md) | Every error alchemic raises, with its message, cause and fix. |
| [Recipes](recipes.md) | Inheritance, read-only fields, lifecycle hooks, reusable columns, async receivers, JSONB, pgvector, PostGIS, existing tables. |
| [Codebase](codebase.md) | How the SQLAlchemy and Pydantic pieces are joined. |

---

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
    session.add(Project(name="Billing"))  # Validated on construction by Pydantic
    session.commit()
    billing = session.exec(Project.where(Project.name == "Billing")).one()
```

---

## Declaring columns

A bare annotation is a column: `name: Mapped[str]`. Add `mapped_column(...)` only for options such as a primary key, a default or a foreign key. The rules are in [Declaring columns](interfaces.md#declaring-columns).

---

## Coming from SQLModel

The same ideas, in alchemic's syntax. Validation is covered in the [README](../README.md#construction-and-validation), and typing in [Why alchemic](../README.md#why-alchemic).

| SQLModel | alchemic |
|---|---|
| `class Author(SQLModel, table=True)` | `class Author(Model)`, with `__tablename__` |
| `id: int \| None = Field(default=None, primary_key=True)` | `id: Mapped[int] = mapped_column(primary_key=True, init=False)` |
| `name: str` | `name: Mapped[str]` |
| `author_id: int \| None = Field(default=None, foreign_key="authors.id")` | `author_id: Mapped[int \| None] = mapped_column(references(Author.id), default=None)` |
| `books: list["Book"] = Relationship(back_populates="author")` | `books: Related[list["Book"]] = relationship()`, paired automatically when unambiguous |
| `Relationship(link_model=BookTag)` | `link(Book.tags, Tag.books)`, which creates the association table |
| `select(Book).where(Book.title == "x")` | `Book.where(Book.title == "x")` |
| `session.exec(...)` | `session.exec(...)` |
