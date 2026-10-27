"""Column types for values that must be stored and summed exactly."""

from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Context, Decimal
from typing import Any, Final

from sqlalchemy import BigInteger
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

__all__ = ["ScaledDecimal"]

_MAX_PRECISION: Final = 18
_EXACT: Final = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)


class ScaledDecimal(TypeDecorator[Decimal]):
    """A ``Decimal`` stored as a ``BIGINT`` count of ``10 ** -scale`` units.

    The column holds an integer on every dialect, so ``SUM`` and comparisons in SQL are exact.
    Values are never rounded: a value with more decimal places than ``scale`` is rejected.
    """

    impl: TypeEngine[Any] | type[TypeEngine[Any]] = BigInteger
    cache_ok: bool | None = True

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
    def process_result_value(self, value: int | None, dialect: Dialect) -> Decimal | None:
        """Return the stored integer as a ``Decimal`` with exactly ``scale`` decimal places.

        ``None`` passes through.
        """
        if value is None:
            return None
        return Decimal(value).scaleb(-self.scale, _EXACT)
