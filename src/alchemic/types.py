"""Column types for values that must be stored and summed exactly."""

from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Context, Decimal
from typing import Any, Final, cast

from sqlalchemy import BigInteger, ColumnElement, Integer, type_coerce
from sqlalchemy.engine import Dialect
from sqlalchemy.sql import operators
from sqlalchemy.sql.operators import OperatorType
from sqlalchemy.types import TypeDecorator, TypeEngine

from alchemic._sqlalchemy_private import ComparatorFactory

__all__ = ["ScaledDecimal"]

_MAX_PRECISION: Final = 18
_EXACT: Final = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
_ARITHMETIC: Final[frozenset[Any]] = frozenset(
    {operators.add, operators.sub, operators.mul, operators.truediv, operators.floordiv, operators.mod}
)


def _arithmetic_result(scaled: "ScaledDecimal", op: OperatorType, other: TypeEngine[Any]) -> "ScaledDecimal":
    """The type of ``scaled <op> other``, which is ``scaled``; raises ``TypeError`` if inexact."""
    same_scale = isinstance(other, ScaledDecimal) and other.scale == scaled.scale
    if op in (operators.add, operators.sub) and same_scale:
        return scaled
    if op is operators.mul and isinstance(other, Integer):
        return scaled
    raise TypeError(
        f"{scaled!r} supports + and - with a ScaledDecimal of the same scale, a Decimal or an int, "
        f"and * by an integer column or int; not {getattr(op, '__name__', op)} with {other!r}, "
        "where a Python value other than an int multiplier counts as the column's own type"
    )


class ScaledDecimal(TypeDecorator[Decimal]):
    """A ``Decimal`` stored as a ``BIGINT`` count of ``10 ** -scale`` units.

    The column holds an integer on every dialect, so ``SUM`` and comparisons in SQL are exact.
    Values are never rounded: a value with more decimal places than ``scale`` is rejected.

    In SQL expressions, ``+`` and ``-`` with a ``ScaledDecimal`` of the same scale, a ``Decimal``
    or an ``int``, and ``*`` by an integer column or ``int``, give a ``ScaledDecimal``. Any other
    arithmetic (``/``, ``//``, ``%``, ``*`` by a ``Decimal`` or by another ``ScaledDecimal``, mixed
    scales) raises ``TypeError`` when the expression is built.

    ``func.sum``, ``func.min`` and ``func.max`` keep the type; ``func.avg`` does not and returns
    raw units. When the left operand is a column of another type (``quantity + amount``,
    ``rate * amount``), SQLAlchemy applies that column's rules, which can return raw units:
    put the ``ScaledDecimal`` first. A Python value inside a SQL function or ``CASE`` is not
    scaled; pass it as ``literal(value, ScaledDecimal(precision, scale))``.
    """

    impl: TypeEngine[Any] | type[TypeEngine[Any]] = BigInteger
    cache_ok: bool | None = True

    class Comparator(TypeDecorator.Comparator[Decimal]):
        """Applies ``ScaledDecimal``'s arithmetic rules to expressions on the column."""

        __slots__ = ()

        @property
        def _scaled(self) -> "ScaledDecimal":
            return cast("ScaledDecimal", self.expr.type)

        def _adapt_expression(
            self, op: OperatorType, other_comparator: TypeEngine.Comparator[Any]
        ) -> tuple[OperatorType, TypeEngine[Any]]:
            if op in _ARITHMETIC:
                return op, _arithmetic_result(self._scaled, op, other_comparator.type)
            return super()._adapt_expression(op, other_comparator)

        def reverse_operate(self, op: OperatorType, other: Any, **kwargs: Any) -> ColumnElement[Any]:
            """Build ``other <op> column`` for a Python ``other``; raises ``TypeError`` if inexact."""
            result_type = _arithmetic_result(self._scaled, op, self._scaled.coerce_compared_value(op, other))
            return type_coerce(super().reverse_operate(op, other, **kwargs), result_type)

    # Overrides a memoized_property in SQLAlchemy's stubs; pyright names the rule differently per version.
    comparator_factory: "ComparatorFactory[Any]" = Comparator  # pyright: ignore

    def coerce_compared_value(self, op: OperatorType | None, value: Any) -> Any:
        """``Integer`` for an ``int`` multiplier, so it is not scaled; ``self`` for anything else."""
        if op is operators.mul and isinstance(value, int) and not isinstance(value, bool):
            return Integer()
        return self

    def __init__(self, precision: int, scale: int) -> None:
        """Declare a column of at most ``precision`` digits, ``scale`` of them after the point.

        Raises ``ValueError`` if ``precision`` is above 18, the most a signed 64-bit integer
        holds, or if ``scale`` is below 0 or above ``precision``.
        """
        if precision > _MAX_PRECISION:
            raise ValueError(f"precision {precision} is above {_MAX_PRECISION}, the most a BIGINT holds")
        if not 0 <= scale <= precision:
            raise ValueError(f"scale must be between 0 and precision ({precision}), not {scale}")
        super().__init__()
        self.precision = precision
        self.scale = scale

    def __repr__(self) -> str:
        return f"ScaledDecimal(precision={self.precision}, scale={self.scale})"

    @property
    def python_type(self) -> type[Decimal]:
        """``Decimal``, the type of the values this column returns."""
        return Decimal

    # pylint: disable-next=unused-argument
    def process_bind_param(self, value: object, dialect: Dialect) -> int | None:
        """Return ``value`` as the integer count of ``10 ** -scale`` units it stands for.

        ``None`` passes through. Raises ``TypeError`` for anything but a ``Decimal``, an ``int``
        or ``None``, so a ``float`` or ``bool`` is refused. Raises ``ValueError`` for a
        non-finite value, a value with more than ``scale`` decimal places, or one that needs
        more than ``precision`` digits once scaled.
        """
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, Decimal | int):
            raise TypeError(f"ScaledDecimal stores a Decimal or an int, not {type(value).__name__}")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError(f"ScaledDecimal stores finite values, not {value}")
        scaled = Decimal(value).scaleb(self.scale, _EXACT)
        if scaled != scaled.to_integral_value(context=_EXACT):
            raise ValueError(f"{value} has more than {self.scale} decimal places")
        if not scaled.is_zero() and scaled.adjusted() >= self.precision:
            raise ValueError(f"{value} needs more than {self.precision} digits")
        return int(scaled)

    # pylint: disable-next=unused-argument
    def process_result_value(self, value: int | Decimal | None, dialect: Dialect) -> Decimal | None:
        """Return the stored integer as a ``Decimal`` with exactly ``scale`` decimal places.

        A ``Decimal`` holding a whole number, as Postgres returns for ``SUM`` of a ``BIGINT``,
        is taken the same way.

        ``None`` passes through.
        """
        if value is None:
            return None
        return Decimal(value).scaleb(-self.scale, _EXACT)
