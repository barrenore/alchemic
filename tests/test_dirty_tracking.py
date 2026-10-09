import uuid

import pytest
import sqlalchemy as sa
from pydantic import ConfigDict, ValidationError
from sqlalchemy.orm import Session

from alchemic import Mapped, Model, mapped_column


class Item(Model):
    __tablename__ = "items_dirty"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(50))
    count: Mapped[int] = mapped_column(default=0)


class ValidatedItem(Model):
    __tablename__ = "validated_items_dirty"
    model_config = ConfigDict(validate_assignment=True)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(50))
    count: Mapped[int] = mapped_column(default=0)


def test_dirty_tracking_default_mode(session: Session):
    item = Item(name="Widget", count=10)
    session.add(item)
    session.commit()

    assert item not in session.dirty

    item.name = "Gadget"
    assert item in session.dirty

    session.commit()

    reloaded = session.execute(sa.select(Item).where(Item.id == item.id)).scalar_one()
    assert reloaded.name == "Gadget"


def test_dirty_tracking_with_validate_assignment(session: Session):
    item = ValidatedItem(name="Widget", count=10)
    session.add(item)
    session.commit()

    assert item not in session.dirty

    item.count = 20
    assert item in session.dirty
    session.commit()

    reloaded = session.execute(sa.select(ValidatedItem).where(ValidatedItem.id == item.id)).scalar_one()
    assert reloaded.count == 20

    # Invalid mutation should raise ValidationError and not corrupt state
    with pytest.raises(ValidationError):
        item.count = "not-a-number"  # type: ignore[assignment]

    assert reloaded.count == 20


def test_attribute_deletion(session: Session):
    item = Item(name="Widget", count=10)
    session.add(item)
    session.commit()

    del item.count
    assert "count" not in item.__dict__
