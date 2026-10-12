# Typing

This page lists what Pyright and Mypy check on alchemic models, with the types they report. Every example was run through pyright 1.1.414 and mypy 2.3.1, which gave the same result for each one. The examples share the models below, which combine the ones in [Relationships](relationships.md).

No stubs or plugins are needed. Import `Mapped`, `Related`, `mapped_column`, `relationship`, `link`, `path`, `prefetch` and `references` from `alchemic`, not from `sqlalchemy`. The checkers read alchemic's declarations; at runtime, `Mapped` is SQLAlchemy's class.

## Models

```python
from alchemic import Mapped, Model, Related, link, mapped_column, references, relationship

class Author(Model):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    books: Related[list["Book"]] = relationship()

class Book(Model):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    title: Mapped[str]
    author_id: Mapped[int | None] = mapped_column(references(Author.id, ondelete="SET NULL"), default=None)
    author: Related[Author | None] = relationship()
    tags: Related[list["Tag"]] = relationship()

class Tag(Model):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    books: Related[list[Book]] = relationship()

link(Book.tags, Tag.books, name="book_tags")
```

---

## Columns and values

At class level an attribute is a column or a relationship. On an instance it is a plain value:

| Expression | Type |
|---|---|
| `Book.title` | `TypedAttribute[str]` |
| `book.title` | `str` |
| `Book.author` | `RelatedAttribute[Book, Author, Author \| None]` |
| `book.author` | `Author \| None` |

`RelatedAttribute` keeps both classes, which is how `path()` checks that each step continues from the previous one.

## Query results

`Model.where(...)` and `select(...)` are shaped by `Session.exec`:

| Expression | Type |
|---|---|
| `session.exec(Book.where(Book.title == "x")).all()` | `Sequence[Book]` |
| `session.exec(select(Book.title)).all()` | `Sequence[str]` |
| `session.exec(select(Book.title)).one()` | `str` |
| `session.exec(select(Book, Author)).all()` | `Sequence[tuple[Book, Author]]` |
| `session.exec(select(Book.title, Book.id)).all()` | `Sequence[tuple[str, int]]` |
| `await book.load(Book.author)` | `Author \| None` |

## Comparisons

A comparison is typed by what it compares.

- `Book.id == 1` is `ColumnElement[bool]`, which `where()` accepts.
- `Book.id == "1"` is a plain `bool`, which `where()` rejects. The same holds for `Book.title == None` and `Book.id == True`. Use `.is_(None)` for NULL checks.
- `Book.title.in_(titles)` and `Book.author_id.is_(None)` are accepted.
- `Book.id > "a"` is an operator error.

## Rejected code

Both checkers reject each line below. Their messages differ in wording, not in what they flag.

| Code | What is wrong |
|---|---|
| `Book.where(Book.id == "1")` | Comparison is `bool`, not `ColumnElement[bool]` |
| `Book.where(Book.title == None)` | Same: use `Book.title.is_(None)` |
| `select(Book).where(Book.id == "1")` | Same, in `select()` |
| `Book.id > "a"` | Operator `>` between `int` and `str` |
| `Book(title="x", author_id="bad")` | `author_id` must be `int \| None` |
| `Book()` | Missing `title` |
| `book.title = 5` | `int` assigned to a `str` column |
| `book.author.name` | `author` may be `None`; check it first |
| `book.has_changed("title")` | Takes a column, such as `Book.title` |
| `book.update(title=5)` | Value does not match the field type |
| `Book.assign(nope=1)` | Unknown field |
| `Author.where(prefetch=[Book.author])` | A path must start at `Author` |
| `Author.where(prefetch=[Author.name])` | Not a relationship |
| `path(Author.books, Author.books)` | `books` starts at `Book`, not `Author` |
| `path(Author.books)` | One step: pass `Author.books` to `prefetch` directly |
| `path(Author.books, Book.author, Author.books, Book.author, Author.books)` | Five steps; type checkers accept two to four |
| `link(Book.author, Author.books)` | `link()` takes collections on both sides |
| `link(Book.tags, Tag.name)` | `Tag.name` is not a relationship |

A single relationship is accepted wherever a path is expected: `prefetch(Author, Author.books)` and `Author.where(prefetch=[Author.books])` both type-check.

## Known gaps

Each item is a place where the checks disagree with the runtime.

- `Book.assign(author=None)` type-checks, because `assign()` accepts constructor fields. At runtime it raises `TypeError: Book.assign() sets columns, not author`.
- At runtime `path()` checks only that there are two or more steps. Type checkers check the chain, so a broken chain is a type error, but at runtime it fails only when the query runs. See [Relationships](relationships.md#loading).
- `link()` raises at runtime if it runs after mapper configuration or if a side already names `back_populates`. The checkers cannot see either condition.

For the messages behind each runtime error, see [Errors](errors.md).
