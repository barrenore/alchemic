"""A constructor argument is optional exactly when its declaration gives a default.

That is ``mapped_column(default=...)``, ``default=None`` included, and nothing
about the column itself. It is the rule the type checker applies through
``dataclass_transform``, so runtime and static checking agree on which calls are
valid. ``test_typing.py`` covers the static half.
"""

import uuid

import pytest
from pydantic import ValidationError

from alchemic import Mapped, Model, mapped_column


class _Base(Model):
    __abstract__ = True
    id: Mapped[int] = mapped_column(primary_key=True)
    tag: Mapped[str] = mapped_column(default="base")


class _Stamp:
    """A plain mixin, not a ``Model``. SQLAlchemy maps its columns into subclasses."""

    stamp: Mapped[str] = mapped_column(default="stamped")


class Item(_Base, _Stamp):
    __tablename__ = "declared_default_items"
    note: Mapped[str | None]
    memo: Mapped[str | None] = mapped_column(default=None)
    token: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)


class Retagged(_Base):
    """Redeclares ``tag`` without a default, which makes it required again."""

    __tablename__ = "declared_default_retagged"
    tag: Mapped[str] = mapped_column()


def test_nullable_without_default_is_required():
    with pytest.raises(ValidationError, match=r"note\n\s+Field required"):
        Item(id=1)  # type: ignore[call-arg]


def test_declared_defaults_are_optional():
    item = Item(id=1, note=None)
    assert (item.memo, item.tag, item.stamp) == (None, "base", "stamped")
    assert isinstance(item.token, uuid.UUID)


def test_callable_default_is_called_per_instance():
    assert Item(id=1, note=None).token != Item(id=2, note=None).token


def test_redeclaration_without_default_overrides_the_inherited_one():
    with pytest.raises(ValidationError, match=r"tag\n\s+Field required"):
        Retagged(id=1)  # type: ignore[call-arg]
    assert Retagged(id=1, tag="x").tag == "x"
