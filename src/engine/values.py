"""
The value domain: what kinds of things an expression can produce.
Knows nothing about syntax, operators, or functions.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import TypeGuard

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


def is_datetime(value) -> TypeGuard[datetime]:
    """Whether `value` is a datetime (as opposed to a bare date or time)."""

    return isinstance(value, datetime)


def is_date_only(value) -> TypeGuard[date]:
    """Whether `value` is a date but *not* a datetime — datetime is a
    date subclass, so a plain isinstance(value, date) would also
    match datetimes; category_of()/format_result() need to tell the
    two apart to pick "date" vs. "datetime" as the category/format.
    """

    return isinstance(value, date) and not isinstance(value, datetime)


def is_time_only(value) -> TypeGuard[time]:
    """Whether `value` is a clock time (no date component)."""

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
            raise ExpressionError(f"{label(self.unit.value)} can't be infinite.") from error

        object.__setattr__(self, "value", quantized)

    # def __add__(self, other):
    #     return Quantity(self.value + other.value, self.unit)

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


class Type(str):
    """A static type: a flat category name, or a compound one carrying
    a schema (table, column).

    Deliberately a *subclass* of str rather than a wrapper around one:
    a flat ``Type("int")`` then hashes and compares exactly like the
    plain string ``"int"`` everywhere the engine already compares
    categories (``category == "int"``, ``category in {"int",
    "decimal"}``, dict keys, f-string interpolation) — so every
    existing dispatch-table registration and category comparison in
    operators.py/functions.py/casts.py keeps working unchanged. Only
    compound types (fields is not None) behave differently: they
    compare and hash structurally, so two tables are equal only if
    they have the same schema, and a compound type never collides
    with a flat dispatch-table key even though its base name might
    (e.g. ``Type("table", fields=...) != "table"``) — which is exactly
    what makes arithmetic on a table an automatic, un-special-cased
    type error: no registered rule's key can ever match it.
    """

    fields: tuple[tuple[str | None, Type], ...] | None

    def __new__(cls, name: str, fields: tuple[tuple[str | None, Type], ...] | None = None):
        self = str.__new__(cls, name)
        self.fields = fields
        return self

    def __eq__(self, other):
        if not isinstance(other, str):
            return NotImplemented

        return str.__eq__(self, other) and self.fields == getattr(other, "fields", None)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        # Always the plain string's hash, even for compound types:
        # hash-equal-for-unequal-objects is fine (just a bucket
        # collision, resolved by __eq__), but flat types *must* hash
        # identically to the bare string for `category in {"int", ...}`
        # and dict-key lookups against existing plain-string tables to
        # keep working.
        return str.__hash__(self)

    def __str__(self):
        if self.fields:
            # A single unnamed field (array/matrix: no header at all,
            # just an element type) renders as e.g. "array{decimal}"
            # rather than "array{None: decimal}".
            if len(self.fields) == 1 and self.fields[0][0] is None:
                return f"{str.__str__(self)}{{{self.fields[0][1]}}}"

            inner = ", ".join(f"{name}: {field_type}" for name, field_type in self.fields)
            return f"{str.__str__(self)}{{{inner}}}"

        return str.__str__(self)

    def __repr__(self):
        return f"Type({str(self)!r})"


@dataclass(frozen=True)
class Column:
    """A single named, homogeneously typed column: the unit `table()`
    is built from, and what `table::colname` field access returns.
    """

    name: str
    values: tuple[Value, ...]
    element_type: Type


@dataclass(frozen=True)
class Table:
    """An immutable columnar store: a schema plus one value-tuple per
    column, all the same length. See `column()`/`table()` in
    functions.py for the only way to construct one from an expression.
    """

    schema: tuple[tuple[str, Type], ...]
    columns: tuple[tuple[Value, ...], ...]

    @property
    def row_count(self) -> int:
        return len(self.columns[0]) if self.columns else 0


@dataclass(frozen=True)
class Array:
    """A headerless Column: an ordered, homogeneously typed sequence of
    values with no name. See `array()` in functions.py.
    """

    values: tuple[Value, ...]
    element_type: Type

    # def __add__(self, other: Array) -> Array:
    #     return Array(values=self.values + other.values, element_type=self.element_type)


@dataclass(frozen=True)
class Matrix:
    """A headerless, homogeneous 2D grid: every cell the same type,
    unlike Table (which is one type per column). See `matrix()` in
    functions.py, built from rows of `array()`.
    """

    element_type: Type
    rows: tuple[tuple[Value, ...], ...]

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.rows), len(self.rows[0]) if self.rows else 0)


@dataclass(frozen=True, order=True)
class Char:
    """A single Unicode character, identified by codepoint. order=True
    gives correct </<=/>/>= for free, since codepoint is the only
    field — see the "char" entry in operators.py's comparison loop.
    """

    codepoint: int


# Every concrete runtime value the engine can produce or hold — the
# result type of evaluate_node()/evaluate_expression() and the
# payload type of a Literal AST node. Deliberately excludes `bool`:
# booleans are represented as plain Python bool (a subclass of int),
# so they're covered by `int` here in the same way category_of()
# checks `isinstance(value, bool)` before `isinstance(value, int)`.
type Value = (
    Number
    | bool
    | str
    | Temporal
    | Duration
    | Quantity
    | Complex
    | Blank
    | Char
    | Column
    | Table
    | Array
    | Matrix
)


def category_of(value) -> Type:
    """Map a runtime value to its static type."""

    # bool is a subclass of int, so it must be checked first.
    if isinstance(value, bool):
        return Type("boolean")

    if isinstance(value, int):
        return Type("int")

    if isinstance(value, Decimal):
        return Type("decimal")

    if isinstance(value, str):
        return Type("text")

    if is_datetime(value):
        return Type("datetime")

    if is_date_only(value):
        return Type("date")

    if is_time_only(value):
        return Type("time")

    if isinstance(value, Duration):
        return Type("duration")

    if isinstance(value, Complex):
        return Type("complex")

    if isinstance(value, Blank):
        return Type("blank")

    if isinstance(value, Quantity):
        return Type(value.unit.value)

    if isinstance(value, Column):
        return Type("column", fields=((value.name, value.element_type),))

    if isinstance(value, Table):
        return Type("table", fields=value.schema)

    if isinstance(value, Array):
        return Type("array", fields=((None, value.element_type),))

    if isinstance(value, Matrix):
        return Type("matrix", fields=((None, value.element_type),))

    if isinstance(value, Char):
        return Type("char")

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
    "text": "a text value",
    "char": "a character",
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
