# Relationships

This page covers how a foreign key, the relationship on each side of it, and a many-to-many table are declared, and what alchemic checks about them. The signatures are in [Interfaces](interfaces.md#3-relationships--navigation).

The examples share these two models:

```python
from alchemic import Mapped, Model, Related, mapped_column, references, relationship

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
```

The foreign key is a column on `Book`. `book.author` is the forward side, and `author.books` is the reverse side. Each side is declared once, in its own class, and alchemic pairs them.

---

## Foreign keys

`references(Author.id)` declares the foreign key inside `mapped_column(...)`. It takes the referenced column, so the foreign key's annotation is checked against that column's type: `Book(title="x", author_id="bad")` is a type error. Its keyword arguments are SQLAlchemy's `ForeignKey` options (`ondelete`, `onupdate`, `deferrable`, `name`, and so on).

Whether the column is optional follows its annotation and default, as for any column:

| Declaration | Constructor argument | Column |
|---|---|---|
| `author_id: Mapped[int] = mapped_column(references(Author.id))` | required | `NOT NULL` |
| `author_id: Mapped[int \| None] = mapped_column(references(Author.id), default=None)` | optional | nullable |
| `author_id: Mapped[int \| None] = mapped_column(references(Author.id))` | required (may be `None`) | nullable |

`references(Node.id)` cannot be written inside `class Node`, because the class does not exist yet. For a self-referential key, use SQLAlchemy's `ForeignKey("nodes.id")` with the table name. See [Self-referential trees](#self-referential-trees).

---

## Forward and reverse sides

- The forward side points to one object: `author: Related[Author | None] = relationship()`.
- The reverse side is a collection: `books: Related[list["Book"]] = relationship()`.

`relationship()` is typed as returning `Any`, so the `Related[...]` annotation supplies the type checker's view. A relationship is not a column, so it is not a Pydantic field. It can still be passed to the constructor, using the models above:

```python
tolkien = Author(name="Tolkien")
hobbit = Book(title="The Hobbit", author=tolkien)
```

### Pairing

alchemic sets `back_populates` on both sides when exactly one relationship on the other class points back at this class. Pairing keeps the in-memory objects consistent: after `hobbit.author = tolkien`, `tolkien.books` contains `hobbit` without a reload.

Pairing is skipped for any relationship that sets `foreign_keys`, `primaryjoin`, `secondaryjoin`, `remote_side`, `viewonly` or `backref`. Skipping is silent, and the relationship still works in queries. Name the pair yourself on both sides:

```python
from alchemic import Mapped, Model, Related, mapped_column, references, relationship

class Author(Model):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    books: Related[list["Book"]] = relationship(foreign_keys="Book.author_id", back_populates="author")

class Book(Model):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    author_id: Mapped[int | None] = mapped_column(references(Author.id), default=None)
    author: Related[Author | None] = relationship(foreign_keys=[author_id], back_populates="books")
```

When more than one relationship could pair, alchemic raises `TypeError` at the first query instead of guessing. There are two ways to get there.

First, two relationships on the other class point back at this class through the same association table. For example, `Book.tags` and `Book.favorite_tags` both declared with `secondary=` that table, and `Tag.books` pointing back through it:

```
TypeError: Tag.books could pair with Book.favorite_tags, Book.tags. Name each pair with back_populates=... on both sides.
```

Second, a second relationship on the same class already points at the partner. If `Author` also declared `drafts: Related[list["Book"]] = relationship()`, then `Author.books` and `Author.drafts` would both want `Book.author`:

```
TypeError: Author.books could pair with Book.author, and so could Author.drafts. Name each pair with back_populates=... on both sides.
```

Set `back_populates` on each pair to resolve it.

---

## Nullability

A single-object relationship over a nullable foreign key can be `None`, so its annotation must say so. Otherwise the mappers fail to configure at the first query:

```
TypeError: Book.author can be None, since author_id is nullable, but its annotation does not allow None. Annotate it Related[Author | None].
```

The parent side of a one-to-one can be `None` too, because the parent may have no child row. Collections are never `None`, so `Related[list[Book]]` takes no `| None`.

---

## One-to-one

Put the foreign key on the child, with `unique=True`. The parent holds a single object with `uselist=False`:

```python
from alchemic import Mapped, Model, Related, mapped_column, references, relationship

class User(Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    profile: Related["Profile | None"] = relationship(uselist=False)

class Profile(Model):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(references(User.id), unique=True, init=False)
    bio: Mapped[str]
    user: Related[User] = relationship()
```

`Related["Profile | None"]` is quoted because `Profile` is defined below `User`. `user_id` is `init=False` because SQLAlchemy sets it from `user` on flush.

---

## Many-to-many

`link()` creates the association table and wires both sides. Declare each side as a collection, without `secondary=` or `back_populates=`, then call `link()` once at import time:

```python
from alchemic import Mapped, Model, Related, link, mapped_column, relationship

class Book(Model):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    title: Mapped[str]
    tags: Related[list["Tag"]] = relationship()

class Tag(Model):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    books: Related[list[Book]] = relationship()

book_tags = link(Book.tags, Tag.books, name="book_tags")
```

- The table's columns are `{prefix}_{column}` for each primary-key column of each side, where the prefix defaults to the lowercase class name: `book_id` and `tag_id` here. Both columns form the primary key, and both foreign keys use `ON DELETE CASCADE`.
- Without `name=`, the table is named `{left table}_{right table}`, here `books_tags`.
- `link()` returns the `Table`. It is registered on the metadata of the left-hand model. With the default `Model`, that is `Model.metadata`, so `Model.metadata.create_all(engine)` creates it.
- `link()` must run before the first query. Calling it after mappers are configured raises `TypeError: link() must run before mappers are configured, which is at the first query`.
- Neither side may already name `secondary=` or `back_populates=`. Otherwise `TypeError: Book.tags already names its secondary or back_populates. link() sets both.`

### Self-referential many-to-many

When both sides belong to one class, `link()` needs `prefixes=`, one per side. The first prefix names the column for the owner of `left`, and the second for the owner of `right`:

```python
from alchemic import Mapped, Model, Related, link, mapped_column, relationship

class Person(Model):
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    follows: Related[list["Person"]] = relationship()
    followers: Related[list["Person"]] = relationship()

link(Person.follows, Person.followers, name="follows", prefixes=("follower", "followed"))
```

The table has `follower_id` and `followed_id`. Without `prefixes=`, `link()` raises `TypeError: link() between two relationships of Person needs prefixes=(...), one per side, such as prefixes=('follower', 'followed')`.

---

## Self-referential trees

A foreign key to the same table uses `ForeignKey` with the table name. The `parent` attribute needs `remote_side`, which turns off automatic pairing, so both sides name each other:

```python
from sqlalchemy import ForeignKey

from alchemic import Mapped, Model, Related, mapped_column, relationship

class Node(Model):
    __tablename__ = "nodes"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    label: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"), default=None)
    parent: Related["Node | None"] = relationship(remote_side="Node.id", back_populates="children")
    children: Related[list["Node"]] = relationship(back_populates="parent")
```

---

## Loading

- **`Session`:** an unloaded relationship loads when you access it.
- **`AsyncSession`:** accessing an unloaded relationship raises `sqlalchemy.exc.MissingGreenlet`. Load it with `await book.load(Book.author)`, or prefetch it in the query.
- **Prefetch:** pass relationships to `Model.where(prefetch=...)` or to `prefetch(...)`. Collections load with `selectinload`, single objects with `joinedload`.

These examples use the `session` from your application and the models above.

```python
from alchemic import path, prefetch

authors = session.exec(Author.where(Author.name == "Tolkien", prefetch=[Author.books])).all()
authors = session.exec(Author.where(prefetch=[path(Author.books, Book.author)])).all()
tolkien = session.get(Author, 1, options=prefetch(Author, Author.books))
```

A path's steps must chain: each step starts at the class the previous step ended at. Type checkers accept paths of two to four steps and reject a path that does not chain. At runtime, `path()` accepts two or more steps and does not check the chain. A broken chain fails when the query runs. For example, `Author.where(prefetch=[path(Author.books, Author.books)])` fails with:

```
sqlalchemy.exc.ArgumentError: ORM mapped entity or attribute "Author.books" does not link from relationship "Author.books".
```

---

For every error on this page, with its cause and fix, see [Errors](errors.md#relationships).
