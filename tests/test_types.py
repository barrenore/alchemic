"""``ScaledDecimal`` stores a money value as an exact integer and refuses what it cannot hold."""

from decimal import Decimal
from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy import func, insert, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from alchemic import Mapped, Model, ScaledDecimal, mapped_column


class Entry(Model):
    __tablename__ = "scaled_entries"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    amount: Mapped[Decimal] = mapped_column(ScaledDecimal(18, 4), default=Decimal(0))


@pytest.fixture
def engine():
    engine = sa.create_engine("sqlite://")
    Model.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def dialect(engine):
    return engine.dialect


def test_round_trip_keeps_the_scale(session):
    session.add(Entry(amount=Decimal("12.34")))
    session.commit()
    stored = session.scalars(select(Entry.amount)).one()
    assert stored == Decimal("12.3400")
    assert str(stored) == "12.3400"


def test_the_column_holds_an_integer_count_of_units(session):
    session.add(Entry(amount=Decimal("12.34")))
    session.commit()
    (raw,) = session.connection().exec_driver_sql("SELECT amount FROM scaled_entries").one()
    assert raw == 123400
    assert type(raw) is int


def test_sql_sum_is_exact(session):
    session.add_all([Entry(amount=Decimal("0.1")) for _ in range(3)])
    session.commit()
    assert session.scalars(select(func.sum(Entry.amount))).one() == Decimal("0.3000")
    raw = session.connection().exec_driver_sql("SELECT SUM(amount) FROM scaled_entries").scalar_one()
    assert raw == 3000
    assert type(raw) is int


def test_a_value_with_too_many_decimal_places_fails_the_statement(session):
    with pytest.raises(StatementError, match="more than 4 decimal places") as info:
        session.execute(insert(Entry).values(amount=Decimal("1.00001")))
    assert isinstance(info.value.orig, ValueError)


def test_a_float_fails_the_statement(session):
    with pytest.raises(StatementError) as info:
        session.execute(insert(Entry).values(amount=1.5))
    assert isinstance(info.value.orig, TypeError)


@pytest.mark.parametrize("value", [1.5, True, "1.5"])
def test_bind_rejects_anything_but_a_decimal_or_an_int(dialect, value):
    with pytest.raises(TypeError):
        ScaledDecimal(18, 4).process_bind_param(value, dialect)


@pytest.mark.parametrize("value", [Decimal("1.00001"), Decimal("0.00001"), Decimal("-2.12345")])
def test_bind_never_rounds(dialect, value):
    with pytest.raises(ValueError, match="more than 4 decimal places"):
        ScaledDecimal(18, 4).process_bind_param(value, dialect)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_bind_rejects_non_finite_values(dialect, value):
    with pytest.raises(ValueError, match="finite"):
        ScaledDecimal(18, 4).process_bind_param(value, dialect)


def test_bind_rejects_a_value_needing_more_digits_than_precision(dialect):
    with pytest.raises(ValueError, match="needs more than 4 digits"):
        ScaledDecimal(4, 2).process_bind_param(Decimal("100.00"), dialect)


def test_bind_accepts_the_largest_value_the_precision_holds(dialect):
    largest = Decimal("99999999999999.9999")
    stored = ScaledDecimal(18, 4).process_bind_param(largest, dialect)
    assert stored == 999999999999999999
    assert ScaledDecimal(18, 4).process_result_value(stored, dialect) == largest


@pytest.mark.parametrize(
    ("precision", "scale", "value"),
    [(4, 4, Decimal(0)), (4, 4, 0), (8, 4, Decimal("0E+5")), (3, 0, Decimal("1E+2"))],
)
def test_bind_accepts_values_within_precision_whatever_their_exponent(dialect, precision, scale, value):
    assert ScaledDecimal(precision, scale).process_bind_param(value, dialect) == int(value * 10**scale)


@pytest.mark.parametrize(("value", "stored"), [(Decimal("12.34"), 123400), (Decimal("-0.5"), -5000)])
def test_bind_stores_the_scaled_integer(dialect, value, stored):
    assert ScaledDecimal(18, 4).process_bind_param(value, dialect) == stored


def test_bind_stores_an_int_by_value(dialect):
    assert ScaledDecimal(18, 4).process_bind_param(5, dialect) == 50000


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(123400, "12.3400"), (0, "0.0000"), (-5, "-0.0005"), (7, "0.0007")],
)
def test_result_has_exactly_scale_places(dialect, stored, expected):
    result = ScaledDecimal(18, 4).process_result_value(stored, dialect)
    assert str(result) == expected


def test_none_passes_through(dialect):
    column_type = ScaledDecimal(18, 4)
    assert column_type.process_bind_param(None, dialect) is None
    assert column_type.process_result_value(None, dialect) is None


@pytest.mark.parametrize(("precision", "scale"), [(19, 2), (25, 0)])
def test_precision_above_18_is_rejected(precision, scale):
    with pytest.raises(ValueError, match="above 18"):
        ScaledDecimal(precision, scale)


@pytest.mark.parametrize(("precision", "scale"), [(4, -1), (4, 5), (0, 1)])
def test_scale_outside_zero_to_precision_is_rejected(precision, scale):
    with pytest.raises(ValueError, match="scale must be between 0 and precision"):
        ScaledDecimal(precision, scale)


def test_the_limits_are_kept_on_the_type():
    column_type = ScaledDecimal(18, 4)
    assert (column_type.precision, column_type.scale) == (18, 4)


def test_python_type_is_decimal():
    assert ScaledDecimal(18, 4).python_type is Decimal


def test_the_column_is_a_bigint():
    column = Entry.__table__.c.amount
    assert isinstance(column.type, ScaledDecimal)
    assert isinstance(column.type.impl, sa.BigInteger)


@pytest.mark.postgres
def test_sum_is_exact_on_postgres(pg_engine):
    cast(sa.Table, Entry.__table__).create(pg_engine)
    with Session(pg_engine) as session:
        session.add_all([Entry(amount=Decimal("0.1")) for _ in range(3)])
        session.commit()
        assert session.scalars(select(func.sum(Entry.amount))).one() == Decimal("0.3000")
        assert session.scalars(select(Entry.amount)).first() == Decimal("0.1000")
