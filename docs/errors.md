# Errors

The errors alchemic raises itself, with the message each prints, its cause and its fix. Messages are quoted exactly; the names inside them are those of the class in your code. Errors that come from SQLAlchemy or Pydantic are marked as such.

Some errors surface at a different time from the call that caused them:

- **At class definition:** the class is mapped as soon as it is created.
- **At the first query:** mappers are configured then. Relationship checks run here, not at class creation.
- **At the call:** the method or function raises directly.

---

## Class definition

**`ArgumentError`** (SQLAlchemy): `Mapper ... could not assemble any primary key columns for mapped table 'books'`

The class has no primary key. A bare `id: Mapped[int]` is a column, but not a primary key. Declare it with `id: Mapped[int] = mapped_column(primary_key=True, init=False)`.

**`InvalidRequestError`** (SQLAlchemy): `Class ... does not have a __table__ or __tablename__ specified and does not inherit from an existing table-mapped class.`

The class is a `Model` subclass with no table. Add `__tablename__ = "..."`, or set `__abstract__ = True` on a base that only shares fields.

**`TypeError`**: `mapped_column() takes a Column's arguments, not a Column: pass its type, constraints and keywords to mapped_column() directly`

A `sqlalchemy.Column` was passed to `mapped_column()`. Pass its type and options directly: `mapped_column(String(80), unique=True)`, not `mapped_column(Column(String(80), unique=True))`.

**`TypeError`**: `Book.before_insert is async, but it runs inside the flush, which cannot await. Make it a plain method, or move the async work to an on_commit receiver.`

A flush hook (`before_insert`, `before_update` or `before_delete`) is `async def`. Make it a plain method. For async work, use an [`on_commit` receiver](interfaces.md#7-on_commit-receivers).

---

## Relationships

Most of these are raised at the first query. An entry says so when the error is raised at the call instead. See [Relationships](relationships.md).

**`TypeError`**: `Book.author can be None, since author_id is nullable, but its annotation does not allow None. Annotate it Related[Author | None].`

The foreign key is nullable, so the single-object side can be `None`. Annotate it `Related[Author | None]`.

**`TypeError`**: `User.profile can be None, since there may be no related row, but its annotation does not allow None. Annotate it Related[Profile | None].`

The parent side of a one-to-one may have no child row. Annotate it `Related[Profile | None]`.

**`TypeError`**: `Tag.books could pair with Book.favorite_tags, Book.tags. Name each pair with back_populates=... on both sides.`

Two relationships on the other class point back at this class through the same association table: here `Book.tags` and `Book.favorite_tags` both use `secondary=` that table, and `Tag.books` points back through it.

**`TypeError`**: `Author.books could pair with Book.author, and so could Author.drafts. Name each pair with back_populates=... on both sides.`

A second relationship on the same class already points at the partner. Here `Author.drafts` is declared as a second `Related[list["Book"]]` relationship alongside `Author.books`.

In both cases, set `back_populates="..."` on each pair, which names the partner on both sides.

**`AmbiguousForeignKeysError`** (SQLAlchemy): `Could not determine join condition between parent/child tables on relationship ... there are multiple foreign key paths linking the tables. Specify the 'foreign_keys' argument`

Two foreign keys link the same tables, and the relationship does not say which one it uses. Add `foreign_keys=[...]`, such as `relationship(foreign_keys=[author_id])`.

**`NoForeignKeysError`** (SQLAlchemy): `Could not determine join condition between parent/child tables on relationship ... there are no foreign keys linking these tables.`

The relationship has no foreign key to follow. Declare the column with `references(...)`, or add `primaryjoin` and `foreign_keys` if the join is not a plain foreign key.

**`ArgumentError`** (SQLAlchemy), raised by a query: `ORM mapped entity or attribute "Author.books" does not link from relationship "Author.books".`

A `path(...)` does not chain: the step after the first one does not start where the previous step ended. `path()` does not check this, and type checkers reject it. See [Typing](typing.md#rejected-code).

**`TypeError`**: `path() takes two or more relationships. Prefetch a single one as it is.`

Raised at the call. `path()` was given one step. Pass the relationship attribute itself to `prefetch(...)` or `Model.where(prefetch=...)`.

**`TypeError`**: `Book.author does not start at Author`

Raised at the call. A prefetch path does not start at the model it is prefetched from. The first step must be a relationship of the root class, here `Author`.

**`TypeError`** from `link()`, at the call: `link() must run before mappers are configured, which is at the first query`

`link()` was called after the first query. Move it to import time, before any query runs.

**`TypeError`** from `link()`, at the call: `link() takes relationships, and Author.name is not one`

One of the arguments is a column, not a relationship.

**`TypeError`** from `link()`, at the call: `Book.tags already names its secondary or back_populates. link() sets both.`

The relationship already sets `secondary=` or `back_populates=`. Remove it, and let `link()` set both.

**`TypeError`** from `link()`, at the call: `link() between two relationships of Person needs prefixes=(...), one per side, such as prefixes=('follower', 'followed')`

Both sides belong to one class, so the association table's columns need distinct names. Pass `prefixes=`.

**`TypeError`** from `link()`, at the call: `link() needs both classes mapped to a table`

One of the classes is mapped to something other than a `Table`, such as a join. Map both classes to tables.

---

## Construction and updates

These come from Pydantic. Their type is `pydantic_core.ValidationError`, and the message includes the field name.

**`Field required`**: a column without a default was not passed to the constructor. A nullable column without `default=` is still required; see [Declaring columns](interfaces.md#declaring-columns).

**`Input should be a valid string`** (or `valid integer` and so on): the value does not match the annotation. Pydantic does not convert `5` to `"5"`.

**`Field is frozen`** (`frozen_field`): a `readonly=True` field that holds a value was changed by assignment or by `update()`. Setting it while it is `None` is allowed.

**`Object has no attribute 'nope'`** (`no_such_attribute`): `update(nope=1)` names a field the model does not have. `update()` raises `ValidationError` here, not `TypeError`.

**`TypeError`**: `update() takes either one patch or keyword arguments`

`update()` was called with a patch and with keyword arguments. Use one form.

**`TypeError`**: `Book.assign() takes keyword arguments only`

`assign()` was called with a positional argument.

**`TypeError`**: `Book.assign() sets columns, not author`

`assign()` was given a relationship name. Relationships are not columns. Update the loaded objects, or use `sqlalchemy.update(Book)` with the columns you need.

**`TypeError`**: `Book.assign() cannot set published: declared readonly=True`

`assign()` was given a `readonly=True` field. Leave the field out. To change it on a loaded object, `update()` allows it only while the field is `None`.

**`TypeError`**: `Book.assign() would skip Book.before_update(): a bulk UPDATE runs no per-object code. Update the loaded objects, or use sqlalchemy.update(Book) to bypass it deliberately.`

The model has a `before_update` hook, which a bulk UPDATE would skip. Update loaded objects so the hook runs, or call `sqlalchemy.update(...)` if skipping it is intended.

**`TypeError`**: `Worker.assign() would skip its on_commit receivers: a bulk UPDATE runs no per-object code. ...`

The same rule applies to `on_commit` receivers for updates.

**`TypeError`**: `a Book cannot be copied: the copy would share or break its SQLAlchemy instance state. Construct a new Book(...), or change this one with update().`

Raised by `model_copy()`, `copy.copy()`, `copy.deepcopy()` and `copy.replace()`. Construct a new instance, or change the existing one with `update()`.

**`TypeError`**: `Book.model_construct() would build an instance without SQLAlchemy's instance state. Construct it with Book(...).`

`model_construct()` skips the constructor, which SQLAlchemy needs. Use `Book(...)`.

**`ValidationError`**: `Declared init=False: the database sets it, not the caller` (`init_false`)

A field declared with `init=False` was passed to the constructor or to `update()`. Leave it out; the database sets it.

**`TypeError`**: `Author.name is not an attribute of Book`

`has_changed()` or `load()` was given an attribute of a different model. Pass an attribute of the instance's own class.

---

## `on_commit` receivers

See [`on_commit`](interfaces.md#7-on_commit-receivers).

**`TypeError`** at decoration: `on_commit() takes Inserted, Updated or Deleted, not ...`

A kind other than the three change types was passed.

**`ExceptionGroup`**: `on_commit receivers failed`, with the receivers' exceptions as sub-exceptions.

A receiver raised after the transaction committed. The data is saved; the group reports what failed. Catch the group where you call `commit()`, and check `group.exceptions`.

**`TypeError`** as a sub-exception: `on_commit receiver ... is async; commit through an AsyncSession`

An `async def` receiver ran under a sync `Session`. Commit through an `AsyncSession`, or make the receiver synchronous.

---

## Concurrency

**`StaleDataError`** (SQLAlchemy): `UPDATE statement on table 'documents' expected to update 1 row(s); 0 were matched.`

A row with `version=True` changed after this instance loaded it. Reload the row and apply the change again. See [`mapped_column`](interfaces.md#mapped_column-and-col).

---

## Silent and unclear cases

These do not raise an alchemic error, or raise something that does not name the cause:

- **Explicit join arguments turn off pairing without a message.** A relationship with `foreign_keys=`, `primaryjoin=`, `secondaryjoin=`, `remote_side=`, `viewonly=` or `backref=` is not paired. Queries still work, and `back_populates` stays unset. Set `back_populates` on both sides when you want the in-memory objects kept in sync.
- **`has_changed()` with a string** raises `AttributeError: 'str' object has no attribute 'parent'`. Pass the column attribute instead: `book.has_changed(Book.title)`.
- **Relationship paths are not checked at runtime.** A broken `path()` fails when the query is built, with the SQLAlchemy message above.
