"""The value domain: what kinds of things an expression can produce.

Knows nothing about syntax, operators, or functions.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

# Type Aliases
type Number = int | Decimal
type Temporal = date | datetime | time


class ExpressionError(ValueError):
    """An error caused by an invalid expression.

    ``position`` is a zero-based index into the source text, when
    known, so the UI can point at the offending token.
    """

    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.message = message
        self.position = position


def is_datetime(value) -> bool:
    return isinstance(value, datetime)


def is_date_only(value) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def is_time_only(value) -> bool:
    return isinstance(value, time)


class Unit(Enum):
    """Units for quantities.

    Adding a new unit type (e.g. percentage) means: add a member
    here, add its quantum below, and register its algebra rows in
    the dispatch table. Nothing else changes.
    """

    CURRENCY = "currency"
    TONNAGE = "tonnage"
    PERCENT = "percent"


UNIT_QUANTA = {
    Unit.CURRENCY: Decimal("0.01"),
    Unit.TONNAGE: Decimal("0.001"),
    Unit.PERCENT: Decimal("0.000001"),
}


@dataclass(frozen=True)
class Duration:
    """A calendar-aware duration.

    Pure data: all arithmetic lives in helper functions and the
    dispatch table so the rules stay in one place.
    """

    months: int = 0
    days: int = 0
    seconds: int = 0


def negate_duration(value: Duration) -> Duration:
    return Duration(
        months=-value.months,
        days=-value.days,
        seconds=-value.seconds,
    )


@dataclass(frozen=True)
class Quantity:
    """A number carrying a unit.

    The algebra (unit + unit, unit * number, unit / unit -> ratio)
    lives in the dispatch table, not here, so the rules for every
    unit are visible in one place.
    """

    value: Decimal
    unit: Unit

    def __post_init__(self):
        try:
            quantized = self.value.quantize(UNIT_QUANTA[self.unit], ROUND_HALF_UP)
        except InvalidOperation as error:
            # Decimal.quantize() rejects Infinity outright (there's no
            # finite number of decimal places to round it to). Caught
            # here rather than left to leak as a raw traceback — e.g.
            # $5 * infinity() should fail with a clear message, not an
            # uncaught InvalidOperation three layers down.
            raise ExpressionError(
                f"{label(self.unit.value)} can't be infinite."
            ) from error

        object.__setattr__(self, "value", quantized)


@dataclass(frozen=True)
class Complex:
    """A complex number a + bi.

    Unlike Quantity, this is not quantized to a fixed number of
    decimal places: real and imaginary parts behave like plain
    decimals, at whatever precision arithmetic produced. Complex
    numbers have no total order, so (unlike numbers, currency, or
    tonnage) they support equality only, the same restriction already
    applied to Duration.
    """

    real: Decimal
    imag: Decimal


@dataclass(frozen=True)
class Blank:
    """The single 'missing value' marker.

    Plays the role SQL's NULL, a blank spreadsheet cell, and IEEE
    NaN would separately play in other systems — one sentinel instead
    of three, since this engine doesn't have a binary-float NaN to
    keep distinct from "missing" in the first place (division by
    zero and other invalid numeric ops already raise ExpressionError
    rather than silently producing a special value; see numeric_divide
    and friends in operators.py).

    Blank is deliberately unregistered in the dispatch table: no
    arithmetic, no cross-category comparison. That's the "type safe"
    part — `blank() + 5` is a compile-time error, not a silent 0 or a
    propagated blank, so you can't accidentally let a missing value
    poison a calculation. isblank() and coalesce() are the two
    sanctioned ways to interact with it (see the function registry).
    A frozen dataclass with no fields gives correct equality (every
    Blank() equals every other Blank()) with no extra code.
    """


# ---- The type system's vocabulary --------------------------------


def category_of(value) -> str:
    """Map a runtime value to its static type category."""

    # bool is a subclass of int, so it must be checked first.
    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, int):
        return "int"

    if isinstance(value, Decimal):
        return "decimal"

    if is_datetime(value):
        return "datetime"

    if is_date_only(value):
        return "date"

    if is_time_only(value):
        return "time"

    if isinstance(value, Duration):
        return "duration"

    if isinstance(value, Complex):
        return "complex"

    if isinstance(value, Blank):
        return "blank"

    if isinstance(value, Quantity):
        return value.unit.value

    raise ExpressionError(f"Unsupported value type: {type(value).__name__}.")


CATEGORY_LABELS = {
    "int": "a whole number",
    "decimal": "a decimal number",
    "boolean": "a boolean",
    "date": "a date",
    "datetime": "a datetime",
    "time": "a time",
    "duration": "a duration",
    "currency": "a currency amount",
    "tonnage": "a tonnage",
    "percent": "a percentage",
    "complex": "a complex number",
    "blank": "a blank value",
}


def label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def to_decimal(number: Number) -> Decimal:
    if isinstance(number, Decimal):
        return number

    return Decimal(number)


# Python's Decimal has genuine IEEE-854 Infinity built in, so this is
# just a Decimal value — every dispatch rule already registered for
# "decimal" (comparisons, unary minus, min/max, abs, ...) works on it
# for free. Undefined arithmetic on it (inf - inf, inf / inf, 0 * inf)
# is guarded in operators.py via _guard_indeterminate.
INFINITY = Decimal("Infinity")
