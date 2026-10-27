"""``ScaledDecimal`` stores a money value as an exact integer and refuses what it cannot hold."""

from decimal import Decimal
from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy import func, insert, literal, select, update
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from alchemic import Mapped, Model, ScaledDecimal, mapped_column


class Entry(Model):
    __tablename__ = "scaled_entries"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    amount: Mapped[Decimal] = mapped_column(ScaledDecimal(18, 4), default=Decimal(0))


class Line(Model):
    __tablename__ = "scaled_lines"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    amount: Mapped[Decimal] = mapped_column(ScaledDecimal(18, 4))
    other: Mapped[Decimal] = mapped_column(ScaledDecimal(18, 4))
    cents: Mapped[Decimal] = mapped_column(ScaledDecimal(18, 2))
    quantity: Mapped[int] = mapped_column(sa.Integer)


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


def test_a_decimal_from_the_driver_is_scaled_back(dialect):
    assert ScaledDecimal(18, 4).process_result_value(Decimal(123400), dialect) == Decimal("12.3400")


def test_bind_accepts_the_most_negative_value_the_precision_holds(dialect):
    smallest = Decimal("-99999999999999.9999")
    assert ScaledDecimal(18, 4).process_bind_param(smallest, dialect) == -999999999999999999
    with pytest.raises(ValueError, match="needs more than 18 digits"):
        ScaledDecimal(18, 4).process_bind_param(Decimal(-100000000000000), dialect)


def test_zero_round_trips_when_precision_equals_scale(engine):
    table = sa.Table("rates", sa.MetaData(), sa.Column("rate", ScaledDecimal(4, 4)))
    table.create(engine)
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"rate": Decimal(0)}, {"rate": Decimal("0.2500")}])
        assert conn.scalars(select(table.c.rate)).all() == [Decimal("0.0000"), Decimal("0.2500")]


def test_repr_names_precision_and_scale():
    column_type = ScaledDecimal(18, 4)
    assert repr(column_type) == "ScaledDecimal(precision=18, scale=4)"
    rebuilt = eval(repr(column_type), {"ScaledDecimal": ScaledDecimal})  # pylint: disable=eval-used
    assert (rebuilt.precision, rebuilt.scale) == (18, 4)


def test_statements_differing_only_in_scale_do_not_share_a_cache_entry(session):
    for scale, expected in [(2, "1.50"), (4, "1.5000"), (2, "1.50")]:
        value = session.scalars(select(literal(Decimal("1.5"), ScaledDecimal(18, scale)))).one()
        assert str(value) == expected


@pytest.fixture
def line(session):
    session.add(Line(amount=Decimal("0.1"), other=Decimal("0.03"), cents=Decimal(1), quantity=3))
    session.commit()
    return session


@pytest.fixture(params=["orm", "core"])
def columns(request):
    return Line if request.param == "orm" else cast(sa.Table, Line.__table__).c


EXACT_EXPRESSIONS = {
    "amount + other": (lambda c: c.amount + c.other, "0.1300"),
    "amount - other": (lambda c: c.amount - c.other, "0.0700"),
    "amount + Decimal": (lambda c: c.amount + Decimal("1.5"), "1.6000"),
    "amount - int": (lambda c: c.amount - 1, "-0.9000"),
    "Decimal - amount": (lambda c: Decimal(1) - c.amount, "0.9000"),
    "int - amount": (lambda c: 5 - c.amount, "4.9000"),
    "Decimal + amount": (lambda c: Decimal("0.25") + c.amount, "0.3500"),
    "amount * int": (lambda c: c.amount * 2, "0.2000"),
    "int * amount": (lambda c: 2 * c.amount, "0.2000"),
    "amount * integer column": (lambda c: c.amount * c.quantity, "0.3000"),
    "integer column * amount": (lambda c: c.quantity * c.amount, "0.3000"),
    "-amount": (lambda c: -c.amount, "-0.1000"),
    "(amount + other) * int": (lambda c: (c.amount + c.other) * 2, "0.2600"),
    "amount - other - Decimal": (lambda c: c.amount - c.other - Decimal("0.07"), "0.0000"),
    "sum(amount - other)": (lambda c: func.sum(c.amount - c.other), "0.0700"),
    "min(amount)": (lambda c: func.min(c.amount), "0.1000"),
    "max(amount * quantity)": (lambda c: func.max(c.amount * c.quantity), "0.3000"),
}


@pytest.mark.parametrize("name", list(EXACT_EXPRESSIONS))
def test_exact_arithmetic_keeps_the_type_and_the_value(line, columns, name):
    build, expected = EXACT_EXPRESSIONS[name]
    expression = build(columns)
    assert isinstance(expression.type, ScaledDecimal)
    assert str(line.scalars(select(expression)).one()) == expected


INEXACT_EXPRESSIONS = {
    "amount * amount": lambda c: c.amount * c.other,
    "amount * Decimal": lambda c: c.amount * Decimal("1.5"),
    "Decimal * amount": lambda c: Decimal("1.5") * c.amount,
    "amount * bool": lambda c: c.amount * True,
    "amount / int": lambda c: c.amount / 2,
    "int / amount": lambda c: 2 / c.amount,
    "amount / integer column": lambda c: c.amount / c.quantity,
    "amount // int": lambda c: c.amount // 2,
    "amount % int": lambda c: c.amount % 2,
    "int % amount": lambda c: 7 % c.amount,
    "amount + integer column": lambda c: c.amount + c.quantity,
    "amount - integer column": lambda c: c.amount - c.quantity,
    "amount + other scale": lambda c: c.amount + c.cents,
    "other scale - amount": lambda c: c.cents - c.amount,
}


@pytest.mark.parametrize("name", list(INEXACT_EXPRESSIONS))
def test_inexact_arithmetic_is_refused_when_built(columns, name):
    with pytest.raises(TypeError, match=r"supports \+ and - with a ScaledDecimal of the same scale"):
        INEXACT_EXPRESSIONS[name](columns)


def test_the_refusal_names_the_types():
    with pytest.raises(
        TypeError,
        match=r"ScaledDecimal\(precision=18, scale=2\) .* not add with ScaledDecimal\(precision=18, scale=4\)",
    ):
        _ = Line.cents + Line.amount


def test_a_custom_operator_keeps_the_column_type():
    assert isinstance(Line.amount.op("|")(Decimal(1)).type, ScaledDecimal)


def test_a_float_in_arithmetic_fails_the_statement(line):
    with pytest.raises(StatementError) as info:
        line.scalars(select(Line.amount + 1.5)).one()
    assert isinstance(info.value.orig, TypeError)


@pytest.mark.parametrize(
    ("condition", "count"),
    [
        (lambda: Line.amount == Decimal("0.1"), 1),
        (lambda: Line.amount > Decimal("0.0999"), 1),
        (lambda: Line.amount < Decimal(1), 1),
        (lambda: Line.amount.between(Decimal("0.1"), Decimal("0.1")), 1),
        (lambda: Line.amount.in_([Decimal("0.2"), Decimal("0.1")]), 1),
        (lambda: Line.amount != Decimal("0.1"), 0),
        (lambda: Line.amount > Line.other, 1),
    ],
)
def test_comparisons_scale_the_value_compared(line, condition, count):
    assert line.scalars(select(func.count()).select_from(Line).where(condition())).one() == count


def test_literal_binds_render_the_scaled_integer(engine):
    sql = str(select(Line.amount + Decimal("1.5")).compile(engine, compile_kwargs={"literal_binds": True}))
    assert "scaled_lines.amount + 15000" in sql


def test_an_update_by_expression_stays_exact(line):
    line.execute(update(Line).values(amount=Line.amount + Decimal("0.5")))
    line.commit()
    assert line.scalars(select(Line.amount)).one() == Decimal("0.6000")


def test_coalesce_of_an_empty_sum_with_zero_is_zero(session):
    assert str(session.scalars(select(func.coalesce(func.sum(Line.amount), 0))).one()) == "0.0000"


def test_a_typed_literal_inside_a_function_is_scaled(session):
    fallback = literal(Decimal("1.5"), ScaledDecimal(18, 4))
    assert session.scalars(select(func.coalesce(func.sum(Line.amount), fallback))).one() == Decimal("1.5000")


@pytest.mark.postgres
def test_sum_is_exact_on_postgres(pg_engine):
    cast(sa.Table, Entry.__table__).create(pg_engine)
    with Session(pg_engine) as session:
        session.add_all([Entry(amount=Decimal("0.1")) for _ in range(3)])
        session.commit()
        assert session.scalars(select(func.sum(Entry.amount))).one() == Decimal("0.3000")
        assert session.scalars(select(Entry.amount)).first() == Decimal("0.1000")
