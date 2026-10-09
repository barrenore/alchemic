"""A column type's limits are validated: ``String(n)``'s length, ``Numeric(p, s)``'s digits."""

import enum
from decimal import Decimal
from typing import Annotated, Literal, NewType

import pytest
from pydantic import Field, ValidationError
from sqlalchemy import Enum, Numeric, String, Text

from alchemic import Mapped, Model, mapped_column

Slug = NewType("Slug", str)


class Color(enum.StrEnum):
    RED = "red"


class Limited(Model):
    __tablename__ = "column_limits"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(5), default="a")
    note: Mapped[str | None] = mapped_column(String(3), default=None)
    slug: Mapped[Slug] = mapped_column(String(4), default=Slug("a"))
    tighter: Mapped[Annotated[str, Field(max_length=2)]] = mapped_column(String(10), default="a")
    looser: Mapped[Annotated[str, Field(max_length=99)]] = mapped_column(String(3), default="a")
    color: Mapped[Color] = mapped_column(Enum(Color), default=Color.RED)
    kind: Mapped[Literal["a", "bb"]] = mapped_column(String(2), default="a")
    body: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal(0))
    ratio: Mapped[float] = mapped_column(Numeric(5, 2, asdecimal=False), default=0.0)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "sixsix", "at most 5 characters"),
        ("note", "four", "at most 3 characters"),
        ("slug", Slug("fivee"), "at most 4 characters"),
        ("tighter", "abc", "at most 2 characters"),
        ("looser", "abcd", "at most 3 characters"),
        ("price", Decimal("1234.5"), "no more than 3 digits before the decimal point"),
        ("price", Decimal("1.234"), "no more than 2 decimal places"),
    ],
)
def test_values_beyond_the_column_type_are_rejected(field, value, message):
    with pytest.raises(ValidationError, match=message):
        Limited(**{"id": 1, field: value})
    limited = Limited(id=1)
    with pytest.raises(ValidationError, match=message):
        limited.update({field: value})


def test_values_within_the_column_type_pass():
    limited = Limited(id=1, name="five5", note=None, price=Decimal("123.45"), body="x" * 1000, ratio=12345.6)
    assert (limited.name, limited.price) == ("five5", Decimal("123.45"))


def test_the_limit_shows_in_the_json_schema():
    properties = Limited.model_json_schema()["properties"]
    assert properties["name"]["maxLength"] == 5
    assert properties["tighter"]["maxLength"] == 2
