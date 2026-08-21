"""Function registry.
Each function declares its arity, whether it evaluates its own
arguments (lazy), its static result type, and its implementation.
UDFs later become entries added to a copy of this registry.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final, Never

from .calendar_utils import (
    end_of_month,
    end_of_quarter,
    end_of_year,
    is_public_holiday,
    start_of_month,
    start_of_quarter,
    start_of_year,
    timedelta_to_duration,
)
from .operators import _guard_indeterminate, compare_key
from .parser import Call, Literal
from .rate_bucket_utils import (
    _RATE_BUCKET_REQUIRED_FIELDS,
    _RATE_BUCKET_SCHEMA,
    _rate_bucket_values,
)
from .values import (
    INFINITY,
    Array,
    Blank,
    Column,
    Complex,
    Duration,
    ExpressionError,
    Matrix,
    Quantity,
    Table,
    Type,
    Value,
    category_of,
    negate_duration,
    to_decimal,
)

# Mirrors evaluator.Category: functions.py can't import it directly,
# since evaluator.py imports FUNCTIONS/FunctionSpec from here.
type Category = str | Type
type Categories = Sequence[Category]
type Values = Sequence[Value]

type ResultTypeFn = Callable[[Categories, Call], Category]
type EagerImpl = Callable[[Values], Value]
type LazyImpl = Callable[..., Value]


@dataclass(frozen=True)
class FunctionSpec:
    """One entry in the FUNCTIONS registry: a function's arity,
    evaluation strategy, static result type, and implementation.
    """

    name: str
    min_args: int
    max_args: int | None  # None means unbounded
    lazy: bool
    # (argument categories, call node) -> result category
    result_type: ResultTypeFn
    # eager: impl(values) -> value
    # lazy:  impl(argument nodes, environment, evaluate, row_scope) -> value
    impl: EagerImpl | LazyImpl
    # Index (>= 1) of the one argument that's a row expression, type-
    # checked under a row scope derived from argument 0's (the table's)
    # schema instead of the ambient scope. None for every function that
    # isn't a row-scoped table verb (filter/extend/sort).
    row_scope_arg: int | None = None


def _fail(node: Call, message: str) -> Never:
    """Raise an ExpressionError positioned at `node`'s call site."""

    raise ExpressionError(message, node.position)


def _fixed(category):
    """A result_type for a function whose result category never
    depends on its arguments (today(), pi(), blank(), ...).
    """

    return lambda categories, node: category


NUMERIC_CATEGORIES: Final = frozenset({"int", "decimal"})


def _is_type(category: Category, name: str) -> bool:
    # Bare-name check, deliberately ignoring .fields: a compound Type's
    # `==` is schema-sensitive (see values.Type), but here we only care
    # whether this is "a column" / "a table" at all, any schema.
    return isinstance(category, Type) and str.__eq__(category, name)


def _is_sequence_type(category: Category) -> bool:
    # column() and array() are the same idea minus a name — anywhere
    # sum/avg/min/max accept a lone Column, they accept a lone Array
    # too, unwrapped exactly the same way.
    return _is_type(category, "column") or _is_type(category, "array")


# Hardcoded to 50 significant digits — comfortably past Decimal's
# default 28-digit context precision, so results built from these
# constants are limited by the *arithmetic's* precision, not the
# constant's. Not computed at runtime: there's no benefit to deriving
# pi via a series each call when the digits never change.
PI: Decimal = Decimal(value="3.14159265358979323846264338327950288419716939937511")
E: Decimal = Decimal(value="2.71828182845904523536028747135266249775724709369996")

# ---- today / now / time ------------------------------------------


def _time_result(categories: Categories, node: Call) -> str:
    if any(category != "int" for category in categories):
        _fail(node, "time() arguments must be whole numbers.")

    return "time"


def _time_impl(values) -> time:

    hour = values[0]
    minute = values[1]
    second = values[2] if len(values) == 3 else 0

    try:
        return time(hour, minute, second)
    except ValueError as error:
        raise ExpressionError(str(error)) from error


# --- Is public holiday -------------------------------------------


def _public_holiday_result(categories, node):
    if categories[0] not in {"date", "datetime"}:
        _fail(
            node,
            "is_public_holiday() requires a date or datetime.",
        )

    return "boolean"


def _public_holiday_impl(values):
    value = values[0]

    if isinstance(value, datetime):
        value = value.date()

    return is_public_holiday(value)


# ---- abs ---------------------------------------- -----------------


def _abs_result(categories: Sequence[Category], node: Call):
    category = categories[0]

    # The modulus of a complex number is a plain decimal, not another
    # complex number — the one case where abs()'s result category
    # differs from its argument's.
    #
    if category not in NUMERIC_CATEGORIES | {
        "duration",
        "currency",
        "tonnage",
        "percent",
        "complex",
    }:
        _fail(
            node,
            "abs() accepts numbers, duration, currency, tonnage, percent, or complex.",
        )

    if category == "complex":
        return "decimal"
    return category


def _abs_impl(values):
    value = values[0]

    if isinstance(value, Complex):
        # Decimal.sqrt() is correctly rounded to the current context
        # precision (28 significant digits by default) — deterministic,
        # no binary-float error, same precision model as division.
        magnitude_squared = value.real * value.real + value.imag * value.imag
        return magnitude_squared.sqrt()

    if isinstance(value, Quantity):
        return Quantity(abs(value.value), value.unit)

    if isinstance(value, Duration):
        if value.months < 0 or value.days < 0 or value.seconds < 0:
            if value.months <= 0 and value.days <= 0 and value.seconds <= 0:
                return negate_duration(value)

            raise ExpressionError(
                "abs() cannot normalize a mixed positive and negative duration."
            )

        return value

    return abs(value)


# ---- round -------------------------------------------------------


def _round_result(categories: Categories, node: Call) -> Category:
    if categories[0] not in NUMERIC_CATEGORIES:
        _fail(node, "round() accepts a number.")

    if len(categories) == 1:
        return "int"

    if categories[1] != "int":
        _fail(
            node,
            "The second argument to round() must be a whole number.",
        )

    return categories[0]


def reject_nonfinite(
    value: Decimal,
    operation: str,
) -> None:
    """Raise if `value` is NaN or infinite, naming `operation` in the
    error (e.g. "round() cannot operate on NaN.").
    """

    if value.is_nan():
        raise ExpressionError(f"{operation} cannot operate on NaN.")

    if value.is_infinite():
        raise ExpressionError(f"{operation} cannot operate on an infinite value.")


def _round_impl(values) -> Value:
    value = values[0]

    # Decimal infinities and NaN are not meaningful here.
    if isinstance(value, Decimal) and not value.is_finite():
        raise ExpressionError("Cannot round an infinite value.")

    try:
        if len(values) == 1:
            return round(value)

        digits = values[1]

        if abs(digits) > 100:
            raise ExpressionError("The number of decimal places is too large.")

        return round(value, digits)

    except (InvalidOperation, OverflowError) as error:
        raise ExpressionError("The value cannot be rounded.") from error


# ---- min / max ---------------------------------------------------

_ORDERABLE: Final = frozenset(
    {"date", "datetime", "time", "currency", "tonnage", "percent", "text"}
)


def _min_max_result(name):
    """Build min()/max()'s result_type: variadic numeric/orderable
    arguments of one common type, or a single column()/array().
    """

    def result_type(categories, node):
        if len(categories) == 1 and _is_sequence_type(categories[0]):
            _, element_type = categories[0].fields[0]

            if element_type in NUMERIC_CATEGORIES or element_type in _ORDERABLE:
                return element_type

            _fail(
                node,
                f"{name}() over a column requires a number, date, datetime, "
                "time, currency, tonnage, or percent column.",
            )

        if all(category in NUMERIC_CATEGORIES for category in categories):
            return (
                "int"
                if all(category == "int" for category in categories)
                else "decimal"
            )

        first = categories[0]

        if first in _ORDERABLE and all(category == first for category in categories):
            return first

        if "duration" in categories:
            _fail(
                node,
                f"{name}() does not compare calendar durations: "
                "they have no single ordering (is 1mo more than 30d?).",
            )

        _fail(node, f"{name}() arguments must all have the same type.")

    return result_type


def _min_max_impl(chooser):
    """Build min()/max()'s impl from Python's `min`/`max` builtin as
    `chooser`, unwrapping a single column()/array() argument first.
    """

    def impl(values):
        if len(values) == 1 and isinstance(values[0], (Column, Array)):
            values = values[0].values

        result = chooser(values, key=compare_key)

        # Static type says decimal whenever the arguments mix int
        # and decimal, so keep the runtime value consistent.
        if isinstance(result, int) and any(
            isinstance(value, Decimal) for value in values
        ):
            return Decimal(result)

        return result

    return impl


# ---- sum / avg ---------------------------------------------------

_SUMMABLE: Final = frozenset({"currency", "tonnage", "percent", "duration", "complex"})


def _sum_result(categories, node):
    if len(categories) == 1 and _is_sequence_type(categories[0]):
        _, element_type = categories[0].fields[0]

        if element_type in NUMERIC_CATEGORIES:
            return "int" if element_type == "int" else "decimal"

        if element_type in _SUMMABLE:
            return element_type

        _fail(
            node,
            "sum() over a column requires a numeric, quantity, duration, or complex column.",
        )

    if all(category in NUMERIC_CATEGORIES for category in categories):
        return "int" if all(category == "int" for category in categories) else "decimal"

    first = categories[0]

    if first in _SUMMABLE and all(category == first for category in categories):
        return first

    _fail(
        node,
        "sum() arguments must all be numbers, or all the same "
        "quantity , duration, or complex type.",
    )


def _sum_impl(values):
    if len(values) == 1 and isinstance(values[0], (Column, Array)):
        values = values[0].values

    first = values[0]

    if isinstance(first, Quantity):
        total = sum((value.value for value in values), Decimal(0))
        return Quantity(total, first.unit)

    if isinstance(first, Duration):
        return Duration(
            months=sum(value.months for value in values),
            days=sum(value.days for value in values),
            seconds=sum(value.seconds for value in values),
        )

    if isinstance(first, Complex):
        return Complex(
            sum((value.real for value in values), Decimal(0)),
            sum((value.imag for value in values), Decimal(0)),
        )

    return sum(values)


def _avg_result(categories, node):
    if len(categories) == 1 and _is_sequence_type(categories[0]):
        _, element_type = categories[0].fields[0]

        if element_type in NUMERIC_CATEGORIES:
            return "decimal"

        if element_type in {"currency", "tonnage", "percent", "complex"}:
            return element_type

        _fail(
            node,
            "avg() over a column requires a numeric, quantity, or complex column.",
        )

    if all(category in NUMERIC_CATEGORIES for category in categories):
        return "decimal"

    first = categories[0]

    if first in {"currency", "tonnage", "percent", "complex"} and all(
        category == first for category in categories
    ):
        return first

    _fail(
        node,
        "avg() arguments must all be numbers, or all the same quantity type.",
    )


def _avg_impl(values):
    if len(values) == 1 and isinstance(values[0], (Column, Array)):
        values = values[0].values

    count = len(values)
    first = values[0]

    if isinstance(first, Quantity):
        total = sum((value.value for value in values), Decimal(0))
        return Quantity(total / count, first.unit)

    if isinstance(first, Complex):
        real_total = sum((value.real for value in values), Decimal(0))
        imag_total = sum((value.imag for value in values), Decimal(0))
        return Complex(real_total / count, imag_total / count)

    total = sum((to_decimal(value) for value in values), Decimal(0))
    return total / count


# ---- re / im / conj -----------------------------------------------


def _complex_only_result(name):
    """Build re()/im()/conj()'s result_type: one complex argument,
    result decimal for re()/im() or complex for conj().
    """

    def result_type(categories, node):
        if categories[0] != "complex":
            _fail(node, f"{name}() requires a complex number.")

        return "decimal" if name in {"re", "im"} else "complex"

    return result_type


def _re_impl(values: Values) -> Decimal:
    """Return the real part of the first complex value."""
    value = values[0]
    assert isinstance(value, Complex)
    return value.real


def _im_impl(values: Values) -> Decimal:
    """Return the imaginary part of the first complex value."""
    value = values[0]
    assert isinstance(value, Complex)
    return value.imag


def _conj_impl(values: Values) -> Complex:
    """Return the conjugate of the first complex value."""
    value = values[0]
    assert isinstance(value, Complex)
    return Complex(value.real, -value.imag)


# ---- ceil -----------------------------------------------------------
#
# Excel's CEILING, not a plain math ceiling: it needs a second
# argument to round up *to*, since "round up" alone is meaningless for
# a duration or a currency amount (round up to what — the nearest
# cent? the nearest dollar?). x and multiple must be the same kind of
# value; the result keeps that type.


def _ceil_result(categories, node):
    x_category, multiple_category = categories

    if x_category != multiple_category:
        _fail(node, "ceil() requires x and multiple to be the same kind of value.")

    if x_category in NUMERIC_CATEGORIES:
        return "int" if x_category == "int" else "decimal"

    if x_category in {"currency", "tonnage", "percent", "duration"}:
        return x_category

    _fail(node, "ceil() accepts numbers, quantities, or durations.")


def _ceil_number(x, multiple):
    multiple_decimal = to_decimal(multiple)

    if multiple_decimal <= 0:
        raise ExpressionError("ceil()'s multiple must be positive.")

    quotient = _guard_indeterminate(
        lambda: (to_decimal(x) / multiple_decimal).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    result = quotient * multiple_decimal

    if isinstance(x, int) and isinstance(multiple, int):
        return int(result)

    return result


def _ceil_quantity(x: Quantity, multiple: Quantity) -> Quantity:
    if multiple.value <= 0:
        raise ExpressionError("ceil()'s multiple must be positive.")

    quotient = (x.value / multiple.value).to_integral_value(rounding=ROUND_CEILING)
    return Quantity(quotient * multiple.value, x.unit)


def _ceil_duration(x: Duration, multiple: Duration) -> Duration:
    if x.months or multiple.months:
        raise ExpressionError(
            "ceil() can't use a duration that includes calendar months or "
            "years against a multiple, since they have no fixed length."
        )

    multiple_seconds = multiple.days * 86_400 + multiple.seconds

    if multiple_seconds <= 0:
        raise ExpressionError("ceil()'s multiple must be positive.")

    total_seconds = x.days * 86_400 + x.seconds
    # Integer ceiling division: -(-a // b) == ceil(a / b) for b > 0,
    # and works correctly for negative a too via Python's floor //.
    quotient = -(-total_seconds // multiple_seconds)

    return timedelta_to_duration(timedelta(seconds=quotient * multiple_seconds))


def _ceil_impl(values):
    x, multiple = values

    if isinstance(x, Duration):
        return _ceil_duration(x, multiple)

    if isinstance(x, Quantity):
        return _ceil_quantity(x, multiple)

    return _ceil_number(x, multiple)


# ---- blank / isblank / coalesce -------------------------------------
#
# isblank() is deliberately the one function in this registry with no
# restriction on its argument's category — it has to accept anything,
# since the whole point is testing a value of unknown type. Every
# other function here validates its argument categories; this is the
# documented exception.


def _isblank_result(categories, node):
    return "boolean"


def _isblank_impl(values):
    return isinstance(values[0], Blank)


def _coalesce_result(categories, node):
    x_category, default_category = categories

    if x_category not in ("blank", default_category):
        _fail(
            node,
            "coalesce()'s first argument must be blank or the same type as the default.",
        )

    return default_category


def _coalesce_impl(values):
    x, default = values
    return default if isinstance(x, Blank) else x


# ---- if (lazy) ---------------------------------------------------


def _if_result(categories, node):
    if categories[0] != "boolean":
        _fail(
            node,
            "The first argument to if() must be a boolean condition, such as a comparison.",
        )

    if categories[1] != categories[2]:
        _fail(
            node,
            "Both branches of if() must have the same type.",
        )

    return categories[1]


def _if_impl(args, environment, evaluate, row_scope):
    condition = evaluate(args[0], environment, row_scope)
    chosen = args[1] if condition else args[2]
    return evaluate(chosen, environment, row_scope)


# ---- and / or / not (lazy) -----------------------------------------
#
# and()/or() are function-call syntax, not infix `and`/`or` keywords —
# this language has no reserved words at all today (even `if`/`sum`/
# `pi` can be used as variable names, since a bare identifier is only
# ever a Call when followed by '(') and infix logical operators would
# be the first exception. Variadic, same reasoning as sum()/min()/
# max() being variadic rather than forcing pairwise nesting. Lazy and
# short-circuiting for the same hazard if() already guards against:
# and(x <> 0, 1/x > 5) must not divide by zero when x = 0.


def _and_or_result(name):
    """Build and()/or()'s result_type: variadic, all-boolean arguments."""

    def result_type(categories, node):
        if any(category != "boolean" for category in categories):
            _fail(node, f"{name}()'s arguments must all be boolean.")

        return "boolean"

    return result_type


def _and_impl(args, environment, evaluate, row_scope):
    for argument in args:
        if evaluate(argument, environment, row_scope) is not True:
            return False

    return True


def _or_impl(args, environment, evaluate, row_scope):
    for argument in args:
        if evaluate(argument, environment, row_scope) is True:
            return True

    return False


def _not_result(categories, node):
    if categories[0] != "boolean":
        _fail(node, "not() requires a boolean.")

    return "boolean"


def _not_impl(values):
    return not values[0]


# --- Hours between ------------------------------------------------


def _hours_between_result(categories, node):
    if categories != ["datetime", "datetime"] and categories != ["time", "time"]:
        _fail(node, "hours_between() requires two datetimes/times.")

    return "decimal"


def _hours_between_impl(values):

    if category_of(values[0]) == "time" and category_of(values[1]) == "time":
        val1 = datetime.combine(date.today(), values[0])
        val2 = datetime.combine(date.today(), values[1])

        return Decimal((val2 - val1).seconds / 3600).quantize(Decimal("0.01"))
    else:
        return Decimal((values[1] - values[0]).total_seconds() / 3600).quantize(
            Decimal("0.01")
        )


# ---- days_between ------------------------------------------------


def _days_between_result(categories, node):
    if categories != ["date", "date"] and categories != ["datetime", "datetime"]:
        _fail(node, "days_between() requires two dates/datetimes.")

    return "int"


# ---- dayname / time-intelligence date boundaries ---------------------
#
#


_DAY_ABBREV = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DAY_FULL = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _dayname_result(categories, node):
    if categories[0] not in ("date", "datetime"):
        _fail(node, "dayname() requires a date or datetime.")

    if len(categories) == 2:
        pattern_node = node.args[1]

        if not isinstance(pattern_node, Literal) or pattern_node.value not in (
            "%a",
            "%A",
        ):
            _fail(node, 'dayname()\'s pattern must be "%a" or "%A".')

    return "text"


def _dayname_impl(values):
    value = values[0]
    pattern = values[1] if len(values) == 2 else "%a"
    names = _DAY_FULL if pattern == "%A" else _DAY_ABBREV

    return names[value.weekday()]


def _date_bound_spec(name, date_fn):
    """A date -> date (or datetime -> datetime, at midnight) building
    block — DAX's STARTOFMONTH/ENDOFMONTH/... vocabulary, reimplemented
    as an ordinary explicit function rather than something relying on
    implicit filter context.
    """

    def result_type(categories, node):
        if categories[0] not in ("date", "datetime"):
            _fail(node, f"{name}() requires a date or datetime.")

        return categories[0]

    def impl(values):
        value = values[0]
        is_datetime_value = isinstance(value, datetime)
        result_date = date_fn(value.date() if is_datetime_value else value)

        if is_datetime_value:
            return datetime.combine(result_date, time())

        return result_date

    return FunctionSpec(name, 1, 1, False, result_type, impl)


# coalesce() is deliberately absent from the "blank" comment above:
# it needs a blank/null value in the type system first, which it now
# has, so it's a lazy-free, ordinary FunctionSpec like the others.

# ---- column / table / rowcount --------------------------------------
#
# No new grammar: table() is built entirely out of the existing
# function-call syntax. A column's name has to be known *statically*
# (it becomes part of the result Type's schema), so — same trick as
# ceil()/if() reading their own `node` for extra validation —
# column()'s result_type inspects node.args[0] directly rather than
# just its category, and rejects anything that isn't a literal string.


def _column_result(categories, node):
    name_node = node.args[0]

    if not isinstance(name_node, Literal) or not isinstance(name_node.value, str):
        _fail(node, "column()'s first argument must be a literal text column name.")

    value_categories = categories[1:]
    first = value_categories[0]

    if any(category != first for category in value_categories):
        _fail(node, "column()'s values must all be the same type.")

    return Type("column", fields=((name_node.value, first),))


def _column_impl(values):
    name = values[0]
    elements = tuple(values[1:])
    return Column(name=name, values=elements, element_type=category_of(elements[0]))


def _table_result(categories, node):
    fields = []
    seen_lower = set()

    for category in categories:
        if not _is_type(category, "column"):
            _fail(node, "table()'s arguments must all be column().")

        name, element_type = category.fields[0]

        # Case-insensitive: field access (t::colname) is
        # case-insensitive too, so "Qty" and "qty" coexisting would
        # make ::qty ambiguous.
        if name.lower() in seen_lower:
            _fail(node, f"table() has a duplicate column name: {name!r}.")

        seen_lower.add(name.lower())
        fields.append((name, element_type))

    return Type("table", fields=tuple(fields))


def _table_impl(values):
    columns = values
    length = len(columns[0].values)

    for column in columns[1:]:
        if len(column.values) != length:
            raise ExpressionError(
                "table()'s columns must all have the same number of rows "
                f"({columns[0].name!r} has {length}, {column.name!r} has "
                f"{len(column.values)})."
            )

    schema = tuple((column.name, column.element_type) for column in columns)
    data = tuple(column.values for column in columns)
    return Table(schema=schema, columns=data)


def _rowcount_result(categories, node):
    if not (_is_type(categories[0], "table") or _is_type(categories[0], "matrix")):
        _fail(node, "rowcount() requires a table or matrix.")

    return "int"


def _rowcount_impl(values):
    value = values[0]

    if isinstance(value, Matrix):
        return value.shape[0]

    return value.row_count


# ---- array / matrix ---------------------------------------------------
#
# array() is column() minus the name argument — same "all values the
# same type" validation, no node-literal check needed since there's no
# name to be a literal. matrix() is table() minus names, plus one
# extra constraint table() doesn't have: every row must be the *same*
# element type, not just the same length, since a matrix (unlike a
# table) is homogeneous end to end.


def _array_result(categories, node):
    first = categories[0]

    if any(category != first for category in categories):
        _fail(node, "array()'s values must all be the same type.")

    return Type("array", fields=((None, first),))


def _array_impl(values):
    return Array(values=tuple(values), element_type=category_of(values[0]))


def _matrix_result(categories, node):
    if not all(_is_type(category, "array") for category in categories):
        _fail(node, "matrix()'s arguments must all be array().")

    element_types = [category.fields[0][1] for category in categories]

    if any(element_type != element_types[0] for element_type in element_types):
        _fail(node, "matrix()'s rows must all have the same element type.")

    return Type("matrix", fields=((None, element_types[0]),))


def _matrix_impl(values):
    rows = values
    width = len(rows[0].values)

    for row in rows[1:]:
        if len(row.values) != width:
            raise ExpressionError(
                "matrix()'s rows must all have the same number of "
                f"elements ({width} vs {len(row.values)})."
            )

    return Matrix(
        element_type=rows[0].element_type,
        rows=tuple(row.values for row in rows),
    )


def _colcount_result(categories, node):
    if not (_is_type(categories[0], "table") or _is_type(categories[0], "matrix")):
        _fail(node, "colcount() requires a table or matrix.")

    return "int"


def _colcount_impl(values):
    value = values[0]

    if isinstance(value, Matrix):
        return value.shape[1]

    return len(value.schema)


def _length_result(categories, node):
    if not _is_type(categories[0], "array") and not _is_type(categories[0], "text"):
        _fail(node, "len() requires an array or text.")
    return "int"


def _length_impl(values):
    if isinstance(values[0], str):
        return len(values[0])
    return len(values[0].values)


# ---- at()-----


def _at_result(categories, node):
    base = categories[0]

    if base == "text":
        if len(categories) != 2:
            _fail(node, "at() on text takes exactly one index.")

        if categories[1] != "int":
            _fail(node, "at()'s index must be a whole number.")

        return "text"

    if _is_type(base, "array"):
        if len(categories) != 2:
            _fail(node, "at() on an array takes exactly one index.")

        if categories[1] != "int":
            _fail(node, "at()'s index must be a whole number.")

        return base.fields[0][1]

    if _is_type(base, "matrix"):
        if len(categories) != 3:
            _fail(node, "at() on a matrix takes exactly two indices (row, column).")

        if categories[1] != "int" or categories[2] != "int":
            _fail(node, "at()'s indices must be whole numbers.")

        return base.fields[0][1]

    _fail(node, "at() requires text, an array, or a matrix.")


def _at_impl(values):
    base = values[0]

    if isinstance(base, str):
        (index,) = values[1:]
        if not 0 <= index < len(base):
            raise ExpressionError(
                f"Index {index} is out of range for text of length "
                f"{len(base)} (at() is 0-indexed)."
            )
        return base[index]

    if isinstance(base, Array):
        (index,) = values[1:]

        if not 0 <= index < len(base.values):
            raise ExpressionError(
                f"Index {index} is out of range for an array of length "
                f"{len(base.values)} (at() is 0-indexed)."
            )

        return base.values[index]

    row, col = values[1:]
    rows, cols = base.shape

    if not 0 <= row < rows or not 0 <= col < cols:
        raise ExpressionError(
            f"Index ({row}, {col}) is out of range for a {rows}x{cols} matrix (at() is 0-indexed)."
        )

    return base.rows[row][col]


# ---- text()-----
#
_TEXT_FORMATTABLE: Final = frozenset(
    {
        "int",
        "decimal",
        "percent",
        "currency",
        "tonnage",
        "date",
        "time",
        "datetime",
        "duration",
    }
)


# ------------------------------------------------------------------------
## FORMATTERS
#### Formatting chrono values (date, time, datetime, duration)
# %Y  4-digit year
# %y  2-digit year
#
# %m  month number
# %B  full month name
# %b  abbreviated month name
#
# %d  day of month
# %A  full weekday
# %a  abbreviated weekday
#
# %H  hour, 24-hour
# %I  hour, 12-hour
# %M  minute
# %S  second
# %f  fractional seconds
# %p  AM/PM
#
def _format_chrono(value: date | datetime | time, format_text: str) -> str:
    try:
        return value.strftime(format_text)
    except (ValueError, TypeError) as exc:
        raise ExpressionError(f"Invalid date/time format {format_text!r}.") from exc


# - Number Formatting-----
# 0    required digit
# #    optional digit
# ,    thousands separator
# .    decimal separator
# %    multiply by 100 and append %
#
#


@dataclass(frozen=True)
class NumberFormat:
    prefix: str
    suffix: str

    min_integer_digits: int
    min_fraction_digits: int
    max_fraction_digits: int

    grouping: bool
    percent: bool


_NUMBER_FORMAT_RE = re.compile(
    r"""
    ^
    (?P<prefix>.*?)
    (?P<number>
        [#,0]+
        (?:\.[#0]+)?
        %?
    )
    (?P<suffix>.*)
    $
    """,
    re.VERBOSE,
)


def _parse_number_format(format_text: str) -> NumberFormat:
    match = _NUMBER_FORMAT_RE.fullmatch(format_text)

    if match is None:
        raise ExpressionError(f"Invalid number format {format_text!r}.")

    number = match.group("number")

    percent = number.endswith("%")
    if percent:
        number = number[:-1]

    if "." in number:
        integer_pattern, fraction_pattern = number.split(".", 1)
    else:
        integer_pattern = number
        fraction_pattern = ""

    grouping = "," in integer_pattern
    integer_pattern = integer_pattern.replace(",", "")

    if not integer_pattern:
        raise ExpressionError(f"Invalid number format {format_text!r}.")

    return NumberFormat(
        prefix=match.group("prefix"),
        suffix=match.group("suffix"),
        min_integer_digits=integer_pattern.count("0"),
        min_fraction_digits=fraction_pattern.count("0"),
        max_fraction_digits=len(fraction_pattern),
        grouping=grouping,
        percent=percent,
    )


def _format_number_text(value, format_text: str) -> str:
    """Format a number according to the given format text."""
    spec = _parse_number_format(format_text)

    number = Decimal(value)

    if spec.percent:
        number *= Decimal(100)

    negative = number < 0
    number = abs(number)

    if spec.max_fraction_digits:
        quantum = Decimal(1).scaleb(-spec.max_fraction_digits)

        number = number.quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    else:
        number = number.quantize(
            Decimal(1),
            rounding=ROUND_HALF_UP,
        )

    raw = format(number, "f")

    if "." in raw:
        integer, fraction = raw.split(".", 1)
    else:
        integer, fraction = raw, ""

    integer = integer.zfill(spec.min_integer_digits)

    if spec.grouping:
        integer = f"{int(integer):,}"

        # Restore leading zeros required by the format.
        digits = integer.replace(",", "")
        if len(digits) < spec.min_integer_digits:
            digits = digits.zfill(spec.min_integer_digits)
            integer = f"{int(digits):,}"

    # # means optional trailing fractional digits.
    while len(fraction) > spec.min_fraction_digits and fraction.endswith("0"):
        fraction = fraction[:-1]

    result = integer

    if fraction:
        result += "." + fraction

    if negative:
        result = "-" + result

    if spec.percent:
        result += "%"

    return spec.prefix + result + spec.suffix


def _format_quantity_text(value: Quantity, format_text: str) -> str:
    return _format_number_text(
        value.value,
        format_text,
    )


# def _format_quantity_text(
#     value,
#     format_text,
#     category,
# ):
#     number = value.value

#     return _format_number_text(
#         number,
#         format_text,
#     )


def _format_duration(value: Duration, format_text: str) -> str:
    """Format a duration according to the given format text."""
    seconds = abs(value.seconds)

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    fields = {
        "months": value.months,
        "days": value.days,
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
    }

    try:
        return format_text.format(**fields)
    except (KeyError, ValueError) as exc:
        raise ExpressionError(f"Invalid duration format {format_text!r}.") from exc


def _text_result(categories, node):
    value_category, format_category = categories

    if format_category != "text":
        _fail(
            node,
            "text()'s format must be text.",
        )

    if value_category not in _TEXT_FORMATTABLE:
        _fail(node, f"text() cannot format {value_category}.")

    return "text"


def _text_impl(values):
    value, format_text = values
    category = category_of(value)

    if category in {"date", "time", "datetime"}:
        return _format_chrono(value, format_text)

    if category == "duration":
        return _format_duration(value, format_text)

    if category in {"currency", "tonnage", "percent"}:
        return _format_quantity_text(value, format_text)

    if category in {"int", "decimal"}:
        return _format_number_text(
            value,
            format_text,
        )

    raise ExpressionError(f"text() cannot format {category}.")


# ----- upper()---------
#
#
#
def _upper_result(categories, node):
    if categories[0] != "text":
        _fail(node, "upper() requires text.")
    return "text"


def _upper_impl(values: Values) -> str:
    """Convert the first value to uppercase."""
    text = values[0]
    assert isinstance(text, str)
    return text.upper()


# --- Lower()---------
#
#
def _lower_result(categories, node):
    if categories[0] != "text":
        _fail(node, "lower() requires text.")
    return "text"


def _lower_impl(values):
    text = values[0]
    return text.lower()


# --- Title()---------
#
#
def _title_result(categories, node):
    if categories[0] != "text":
        _fail(node, "title() requires text.")
    return "text"


def _title_impl(values):
    text = values[0]
    return text.title()


# --- Capitalize()---------
#
#
def _capitalize_result(categories, node):
    if categories[0] != "text":
        _fail(node, "capitalize() requires text.")
    return "text"


def _capitalize_impl(values):
    text = values[0]
    return text.capitalize()


# ----- Left(), right()---------
#
#
def _text_slice_result(name):
    def resolve(categories, node):
        if categories[0] != "text":
            _fail(
                node,
                f"{name}() requires text as its first argument.",
            )

        if len(categories) == 2 and categories[1] != "int":
            _fail(
                node,
                f"{name}()'s character count must be a whole number.",
            )

        return "text"

    return resolve


def _text_slice_args(values):
    text = values[0]
    count = values[1] if len(values) == 2 else 1

    if count < 0:
        raise ExpressionError("Character count cannot be negative.")

    return text, count


def _left_impl(values):
    text, count = _text_slice_args(values)
    return text[:count]


def _right_impl(values):
    text, count = _text_slice_args(values)

    if count == 0:
        return ""

    return text[-count:]


# --- mid()------
#
def _mid_result(categories, node):
    if categories[0] != "text":
        _fail(node, "mid() requires text as its first argument.")

    if categories[1] != "int":
        _fail(node, "mid()'s start index must be a whole number.")

    if categories[2] != "int":
        _fail(node, "mid()'s character count must be a whole number.")

    return "text"


def _mid_impl(values):
    text, start, count = values

    if start < 0:
        raise ExpressionError("mid()'s start index cannot be negative.")

    if count < 0:
        raise ExpressionError("mid()'s character count cannot be negative.")

    return text[start : start + count]


# --- concat()-------------
#
#
#
#
#
def _iter_values(value):
    if isinstance(value, Array):
        for item in value.values:
            yield from _iter_values(item)
        return

    if isinstance(value, Matrix):
        for row in value.rows:
            for item in row:
                yield from _iter_values(item)
        return

    if isinstance(value, Column):
        for item in value.values:
            yield from _iter_values(item)
        return

    yield value


def _is_text_collection(category: Category) -> bool:
    if category in {"text", "blank"}:
        return True

    if not isinstance(category, Type) or not category.fields:
        return False

    if (
        _is_type(category, "array")
        or _is_type(category, "matrix")
        or _is_type(category, "column")
    ):
        return category.fields[0][1] in {"text", "blank"}

    return False


def _concat_result(categories, node):
    if not categories:
        _fail(node, "concat() requires at least one value.")

    # TEXTJOIN mode:
    #
    # concat(delimiter,ignore_empty,value....)

    join_mode = (
        len(categories) >= 3 and categories[0] == "text" and categories[1] == "boolean"
    )

    values = categories[2:] if join_mode else categories

    for category in values:
        if not _is_text_collection(category):
            _fail(
                node,
                "concat() accepts text, blank, or collections of text and not "
                + str(category),
            )

    return "text"


def _concat_impl(values):
    join_mode = (
        len(values) >= 3 and isinstance(values[0], str) and isinstance(values[1], bool)
    )

    if join_mode:
        delimiter = values[0]
        ignore_empty = values[1]
        inputs = values[2:]
    else:
        delimiter = ""
        ignore_empty = False
        inputs = values

    parts = []

    for value in inputs:
        for item in _iter_values(value):
            if isinstance(item, Blank):
                item = ""

            if ignore_empty and item == "":
                continue

            parts.append(item)

    return delimiter.join(parts)


# ---- table verbs: filter / select / extend / sort / groupby ---------
#
# filter/extend/sort take a *row expression* — e.g. filter(t, [qty] >
# 2t) — checked and evaluated once per row, with [colname] resolving
# against that row via row_scope (see check_types/evaluate_node in
# evaluator.py). They're FunctionSpec(..., lazy=True, row_scope_arg=N):
# lazy because the row-expression AST node has to be re-evaluated once
# per row rather than once total, row_scope_arg so the checker knows
# which argument gets checked under the table's schema instead of the
# ambient scope. select/groupby take only compile-time string-literal
# arguments (column names, an aggregate-function name) — same
# node-inspecting trick column()'s name argument already uses — so
# they stay ordinary eager functions with no row scope involved.


def _require_table(node, category, who):
    """Raise unless `category` is a table; `who` names the caller
    (e.g. "filter()'s") for the error message.
    """

    if not _is_type(category, "table"):
        _fail(node, f"{who}'s first argument must be a table.")


def _row_dicts(table: Table) -> Iterator[dict[str, Value]]:
    """Yield each row of `table` as a {lowercased column name: value} dict."""

    names = [name.lower() for name, _ in table.schema]

    for row_index in range(table.row_count):
        yield {name: table.columns[i][row_index] for i, name in enumerate(names)}


def _filter_result(categories, node):
    _require_table(node, categories[0], "filter()")

    if categories[1] != "boolean":
        _fail(node, "filter()'s row expression must be a boolean.")

    return categories[0]


def _filter_impl(args, environment, evaluate, row_scope):
    table = evaluate(args[0], environment, row_scope)
    keep = [
        index
        for index, row in enumerate(_row_dicts(table))
        if evaluate(args[1], environment, row) is True
    ]
    columns = tuple(tuple(column[i] for i in keep) for column in table.columns)
    return Table(schema=table.schema, columns=columns)


def _select_result(categories, node):
    _require_table(node, categories[0], "select()")

    by_lower = {
        name.lower(): (name, field_type) for name, field_type in categories[0].fields
    }
    fields = []
    seen_lower = set()

    for name_node in node.args[1:]:
        if not isinstance(name_node, Literal) or not isinstance(name_node.value, str):
            _fail(node, "select()'s column names must be literal text.")

        key = name_node.value.lower()

        if key not in by_lower:
            _fail(node, f"select(): {name_node.value!r} is not a column of this table.")

        if key in seen_lower:
            _fail(node, f"select() has a duplicate column name: {name_node.value!r}.")

        seen_lower.add(key)
        fields.append(by_lower[key])

    return Type("table", fields=tuple(fields))


def _select_impl(values):
    table, *names_wanted = values
    by_lower = {name.lower(): i for i, (name, _) in enumerate(table.schema)}
    indices = [by_lower[name.lower()] for name in names_wanted]

    return Table(
        schema=tuple(table.schema[i] for i in indices),
        columns=tuple(table.columns[i] for i in indices),
    )


# -- append() works on tables
#
#


def _append_result(categories, node):
    table_type = categories[0]
    _require_table(node, table_type, "append()")

    row_types = categories[1:]
    fields = table_type.fields

    if len(row_types) != len(fields):
        _fail(
            node,
            f"append() expected {len(fields)} row values, got {len(row_types)}.",
        )

    for (name, expected), actual in zip(fields, row_types):
        if actual != expected:
            _fail(
                node,
                f"append() value for column {name!r} must be {expected}, got {actual}.",
            )

    return table_type


def _append_impl(values):
    table = values[0]
    row = values[1:]

    return Table(
        schema=table.schema,
        columns=tuple(column + (value,) for column, value in zip(table.columns, row)),
    )


# ---- extend() works on arrays and tables
#
#
def _extend_result(categories, node):
    base = categories[0]

    # Array form:
    #
    # extend([1, 2], 3)       -> [1, 2, 3]
    # extend([1, 2], [3, 4])  -> [1, 2, 3, 4]
    if _is_type(base, "array"):
        if len(categories) != 2:
            _fail(node, "extend() on an array takes exactly one value or array.")

        element_type = base.fields[0][1]
        extension_type = categories[1]

        if _is_type(extension_type, "array"):
            extension_element_type = extension_type.fields[0][1]

            if extension_element_type != element_type:
                _fail(
                    node,
                    "extend() cannot combine arrays with different element types.",
                )

            return base

        if extension_type != element_type:
            _fail(
                node,
                f"extend() cannot add {extension_type} to an array of {element_type}.",
            )

        return base

    # Existing table form:
    #
    # extend(table, "total", price * quantity)
    if _is_type(base, "table"):
        if len(categories) != 3:
            _fail(
                node,
                "extend() on a table takes a column name and expression.",
            )

        name_node = node.args[1]

        if not isinstance(name_node, Literal) or not isinstance(name_node.value, str):
            _fail(
                node,
                "extend()'s second argument must be a literal text column name.",
            )

        new_name = name_node.value

        if any(name.lower() == new_name.lower() for name, _ in base.fields):
            _fail(
                node,
                f"extend() column name {new_name!r} already exists in this table.",
            )

        return Type(
            "table",
            fields=base.fields + ((new_name, categories[2]),),
        )

    _fail(node, "extend() requires a table or array.")


def _extend_impl(args, environment, evaluate, row_scope):
    base = evaluate(args[0], environment, row_scope)

    if isinstance(base, Array):
        extension = evaluate(args[1], environment, row_scope)

        if isinstance(extension, Array):
            return Array(
                values=base.values + extension.values,
                element_type=base.element_type,
            )

        return Array(
            values=base.values + (extension,),
            element_type=base.element_type,
        )

    if isinstance(base, Table):
        new_name = evaluate(args[1], environment, row_scope)

        if base.row_count == 0:
            raise ExpressionError(
                "extend() cannot determine a new column's type on an empty table.",
                args[2].position,
            )

        new_values = tuple(
            evaluate(args[2], environment, row) for row in _row_dicts(base)
        )

        return Table(
            schema=base.schema + ((new_name, category_of(new_values[0])),),
            columns=base.columns + (new_values,),
        )

    raise ExpressionError(
        "extend() requires a table or array.",
        args[0].position,
    )


# ----  sort() function

_SORTABLE: Final = NUMERIC_CATEGORIES | _ORDERABLE


def _sort_result(categories, node):
    _require_table(node, categories[0], "sort()")

    if categories[1] not in _SORTABLE:
        _fail(
            node,
            "sort()'s row expression must produce a sortable value (a "
            "number, date, datetime, time, currency, tonnage, percent, "
            "or text).",
        )

    if len(categories) == 3:
        direction_node = node.args[2]

        if (
            not isinstance(direction_node, Literal)
            or not isinstance(direction_node.value, str)
            or direction_node.value.lower() not in ("asc", "desc")
        ):
            _fail(node, 'sort()\'s third argument must be "asc" or "desc".')

    return categories[0]


def _sort_impl(args, environment, evaluate, row_scope):
    table = evaluate(args[0], environment, row_scope)
    descending = (
        len(args) == 3 and evaluate(args[2], environment, row_scope).lower() == "desc"
    )

    keys = [
        compare_key(evaluate(args[1], environment, row)) for row in _row_dicts(table)
    ]
    order = sorted(range(table.row_count), key=lambda i: keys[i], reverse=descending)
    columns = tuple(tuple(column[i] for i in order) for column in table.columns)

    return Table(schema=table.schema, columns=columns)


_GROUPBY_AGG_FNS = ("sum", "avg", "min", "max", "count")


def _agg_result_label(agg_fn, element_type):
    # Mirrors _sum_result/_avg_result/_min_max_result's column-branch
    # logic exactly, without needing a `node` (this is also called
    # from _groupby_impl, an eager impl, which never has one) — a
    # duplicated but tiny piece of that same logic, purely to derive a
    # label, never to validate (that already happened in
    # _groupby_result before evaluation started).
    if agg_fn == "avg":
        return "decimal" if element_type in NUMERIC_CATEGORIES else element_type

    if element_type in NUMERIC_CATEGORIES:
        return "int" if element_type == "int" else "decimal"

    return element_type


def _validate_agg(node, agg_fn, element_type):
    """Raise unless groupby()'s aggregate column type is compatible
    with its chosen aggregate function.
    """

    if agg_fn == "avg":
        if element_type in NUMERIC_CATEGORIES or element_type in {
            "currency",
            "tonnage",
            "percent",
            "complex",
        }:
            return

        _fail(
            node, "avg() over a column requires a numeric, quantity, or complex column."
        )

    # sum(): currency/tonnage/percent/duration/complex, same as
    # top-level sum(). min/max: anything orderable (dates, text, ...),
    # same as top-level min()/max().
    compatible = _SUMMABLE if agg_fn == "sum" else _ORDERABLE

    if element_type in NUMERIC_CATEGORIES or element_type in compatible:
        return

    _fail(node, f"{agg_fn}() over a column requires a compatible type.")


def _groupby_result(categories, node):
    _require_table(node, categories[0], "groupby()")

    by_lower = {
        name.lower(): (name, field_type) for name, field_type in categories[0].fields
    }

    def literal_str(index, what):
        arg_node = node.args[index]

        if not isinstance(arg_node, Literal) or not isinstance(arg_node.value, str):
            _fail(node, f"groupby()'s {what} must be literal text.")

        return arg_node.value

    group_col = literal_str(1, "group column")
    agg_col = literal_str(2, "aggregate column")
    agg_fn = literal_str(3, "aggregate function")

    if group_col.lower() not in by_lower:
        _fail(node, f"groupby(): {group_col!r} is not a column of this table.")

    if agg_fn not in _GROUPBY_AGG_FNS:
        _fail(
            node, f"groupby()'s aggregate function must be one of {_GROUPBY_AGG_FNS}."
        )

    group_name, group_type = by_lower[group_col.lower()]

    if agg_fn == "count":
        return Type("table", fields=((group_name, group_type), ("count", Type("int"))))

    if agg_col.lower() not in by_lower:
        _fail(node, f"groupby(): {agg_col!r} is not a column of this table.")

    _, element_type = by_lower[agg_col.lower()]
    _validate_agg(node, agg_fn, element_type)

    return Type(
        "table",
        fields=(
            (group_name, group_type),
            (f"{agg_fn}_{agg_col}", _agg_result_label(agg_fn, element_type)),
        ),
    )


def _groupby_impl(values):
    table, group_col, agg_col, agg_fn = values
    names = [name for name, _ in table.schema]
    group_index = next(
        i for i, name in enumerate(names) if name.lower() == group_col.lower()
    )
    group_field = table.schema[group_index]

    groups: dict[Value, list[int]] = {}

    for row_index in range(table.row_count):
        groups.setdefault(table.columns[group_index][row_index], []).append(row_index)

    if agg_fn == "count":
        result_field = ("count", Type("int"))
        agg_values = [len(rows) for rows in groups.values()]
    else:
        agg_index = next(
            i for i, name in enumerate(names) if name.lower() == agg_col.lower()
        )
        element_type = table.schema[agg_index][1]
        result_field = (f"{agg_fn}_{agg_col}", _agg_result_label(agg_fn, element_type))

        agg_values = [
            FUNCTIONS[agg_fn].impl(
                [
                    Column(
                        name=agg_col,
                        values=tuple(table.columns[agg_index][i] for i in rows),
                        element_type=element_type,
                    )
                ]
            )
            for rows in groups.values()
        ]

    return Table(
        schema=(group_field, result_field),
        columns=(tuple(groups.keys()), tuple(agg_values)),
    )


# ---- type_of()-----------
# Returns the type of a value or expression.
#
def _type_of_result(categories, node):
    return "type"


def _type_of_impl(values):
    [value] = values
    return category_of(value)


# --- dayname_ph()
# Returns the name of the day of the week for a given date.
#
#


def _dayname_with_ph_result(categories, node):
    if categories[0] in ("date", "datetime"):
        return "text"
    else:
        _fail(node, "dayname_ph() requires a date or datetime.")


def _dayname_with_ph_impl(values):
    [value] = values
    if is_public_holiday(value):
        return "PH"
    else:
        return value.strftime("%a")


# === Custom Functions
# Rate bucket functions
#


def _rate_bucket_result(categories, node):
    # ---------------------------------------------------------
    # rate_bucket(table)
    # ---------------------------------------------------------

    if len(categories) == 1:
        table_type = categories[0]

        _require_table(
            node,
            table_type,
            "rate_bucket()",
        )

        fields_by_name = {
            name.lower(): field_type for name, field_type in table_type.fields
        }

        for name, expected in _RATE_BUCKET_REQUIRED_FIELDS.items():
            actual = fields_by_name.get(name)

            if actual is None:
                _fail(
                    node,
                    f"rate_bucket() requires column {name!r}.",
                )

            if actual != expected:
                _fail(
                    node,
                    f"rate_bucket() column {name!r} must be {expected}, got {actual}.",
                )

        existing = {name.lower() for name, _ in table_type.fields}

        for name, _ in _RATE_BUCKET_SCHEMA:
            if name.lower() in existing:
                _fail(
                    node,
                    f"rate_bucket() cannot add {name!r}; "
                    "the table already has that column.",
                )

        return Type(
            "table",
            fields=table_type.fields + _RATE_BUCKET_SCHEMA,
        )

    # ---------------------------------------------------------
    # rate_bucket(date, start_time, end_time, day_name)
    # ---------------------------------------------------------

    if len(categories) == 4:
        expected = (
            "date",
            "time",
            "time",
            "text",
        )

        if tuple(categories) != expected:
            _fail(
                node,
                "rate_bucket() requires (date, time, time, text).",
            )

        return Type(
            "table",
            fields=_RATE_BUCKET_SCHEMA,
        )

    _fail(
        node,
        "rate_bucket() takes either a table or (date, start_time, end_time, day_name).",
    )


def _rate_bucket_impl(values):
    # ---------------------------------------------------------
    # Scalar
    # ---------------------------------------------------------

    if len(values) == 4:
        result = _rate_bucket_values(
            values[0],
            values[1],
            values[2],
            values[3],
        )

        return Table(
            schema=_RATE_BUCKET_SCHEMA,
            columns=tuple((value,) for value in result),
        )

    # ---------------------------------------------------------
    # Table
    # ---------------------------------------------------------

    table = values[0]

    by_name = {name.lower(): index for index, (name, _) in enumerate(table.schema)}

    date_column = table.columns[by_name["date"]]
    start_column = table.columns[by_name["start_time"]]
    end_column = table.columns[by_name["end_time"]]
    day_column = table.columns[by_name["day_name"]]

    results = [
        _rate_bucket_values(
            service_date,
            start_time,
            end_time,
            day_name,
        )
        for (
            service_date,
            start_time,
            end_time,
            day_name,
        ) in zip(
            date_column,
            start_column,
            end_column,
            day_column,
        )
    ]

    calculated_columns = tuple(
        tuple(row[column] for row in results) for column in range(4)
    )

    return Table(
        schema=table.schema + _RATE_BUCKET_SCHEMA,
        columns=table.columns + calculated_columns,
    )


# ---- REGISTER THE FUNCTIONS
#

FUNCTIONS: dict[str, FunctionSpec] = {
    # today() -> the current local date.
    "today": FunctionSpec(
        "today",
        0,
        0,
        False,
        _fixed("date"),
        lambda values: datetime.now().date(),
    ),
    # now() -> the current local datetime, truncated to the second.
    "now": FunctionSpec(
        "now",
        0,
        0,
        False,
        _fixed("datetime"),
        lambda values: datetime.now().replace(microsecond=0),
    ),
    # Zero-arg functions, same shape as today()/now(): this sidesteps
    # the question of whether PI should be a reserved bare identifier
    # (and risk shadowing a variable someone names "pi") by requiring
    # the call syntax, exactly like every other built-in constant-ish
    # value here. Names are lowercased during parsing, so PI(), Pi(),
    # and pi() all resolve to the same entry.
    # pi() -> 3.14159... (50 significant digits).
    "pi": FunctionSpec("pi", 0, 0, False, _fixed("decimal"), lambda values: PI),
    # e() -> 2.71828... (50 significant digits).
    "e": FunctionSpec("e", 0, 0, False, _fixed("decimal"), lambda values: E),
    # infinity() -> Decimal Infinity.
    "infinity": FunctionSpec(
        "infinity", 0, 0, False, _fixed("decimal"), lambda values: INFINITY
    ),
    # time(hour, minute[, second]) -> a clock time.
    "time": FunctionSpec("time", 2, 3, False, _time_result, _time_impl),
    # abs(x) -> the absolute value/magnitude of a number, duration,
    # quantity, or complex number (complex -> a plain decimal modulus).
    "abs": FunctionSpec("abs", 1, 1, False, _abs_result, _abs_impl),
    # round(x[, digits]) -> x rounded half-up to `digits` decimal
    # places (default 0, returning an int).
    "round": FunctionSpec("round", 1, 2, False, _round_result, _round_impl),
    # ceil(x, multiple) -> x rounded up to the nearest multiple of
    # `multiple` (Excel's CEILING, not a plain math ceiling).
    "ceil": FunctionSpec("ceil", 2, 2, False, _ceil_result, _ceil_impl),
    # min(...) / max(...) -> the smallest/largest of the arguments, or
    # of a single column()/array() argument's elements.
    "min": FunctionSpec(
        "min", 1, None, False, _min_max_result("min"), _min_max_impl(min)
    ),
    "max": FunctionSpec(
        "max", 1, None, False, _min_max_result("max"), _min_max_impl(max)
    ),
    # sum(...) / avg(...) -> the total/average of the arguments, or of
    # a single column()/array() argument's elements.
    "sum": FunctionSpec("sum", 1, None, False, _sum_result, _sum_impl),
    "avg": FunctionSpec("avg", 1, None, False, _avg_result, _avg_impl),
    # re(z)/im(z) -> the real/imaginary part of a complex number, as a
    # decimal. conj(z) -> its complex conjugate.
    "re": FunctionSpec("re", 1, 1, False, _complex_only_result("re"), _re_impl),
    "im": FunctionSpec("im", 1, 1, False, _complex_only_result("im"), _im_impl),
    "conj": FunctionSpec("conj", 1, 1, False, _complex_only_result("conj"), _conj_impl),
    # blank() -> the missing-value marker. isblank(x) -> whether x is
    # blank (the one function that accepts any category).
    "blank": FunctionSpec(
        "blank", 0, 0, False, _fixed("blank"), lambda values: Blank()
    ),
    "isblank": FunctionSpec("isblank", 1, 1, False, _isblank_result, _isblank_impl),
    # coalesce(x, default) -> `default` if x is blank(), else x.
    "coalesce": FunctionSpec("coalesce", 2, 2, False, _coalesce_result, _coalesce_impl),
    # if(condition, then, else) -> `then` or `else`, evaluating only
    # the chosen branch (lazy, so the other branch may safely error).
    "if": FunctionSpec("if", 3, 3, True, _if_result, _if_impl),
    # and(...)/or(...) -> variadic, short-circuiting boolean logic
    # (function-call syntax; this language has no infix and/or/not).
    "and": FunctionSpec("and", 2, None, True, _and_or_result("and"), _and_impl),
    "or": FunctionSpec("or", 2, None, True, _and_or_result("or"), _or_impl),
    # not(x) -> the boolean negation of x.
    "not": FunctionSpec("not", 1, 1, False, _not_result, _not_impl),
    # hours_between(a, b) -> b - a in whole hours, as a plain int.
    "hours_between": FunctionSpec(
        "hours_between", 2, 2, False, _hours_between_result, _hours_between_impl
    ),
    # days_between(a, b) -> b - a in whole days, as a plain int.
    "days_between": FunctionSpec(
        "days_between",
        2,
        2,
        False,
        _days_between_result,
        lambda values: (values[1] - values[0]).days,
    ),
    "dayname": FunctionSpec("dayname", 1, 2, False, _dayname_result, _dayname_impl),
    # public_holiday(date) -> whether the date is a public holiday.
    "is_public_holiday": FunctionSpec(
        "is_public_holiday", 1, 1, False, _public_holiday_result, _public_holiday_impl
    ),
    # startof.../endof... (month/quarter/year) -> the first/last day of
    # the period containing a date or datetime (datetime in, midnight
    # datetime out) — DAX's STARTOFMONTH/ENDOFMONTH/... vocabulary.
    "somonth": _date_bound_spec("somonth", start_of_month),
    "eomonth": _date_bound_spec("eomonth", end_of_month),
    "soquarter": _date_bound_spec("soquarter", start_of_quarter),
    "eoquarter": _date_bound_spec("eoquarter", end_of_quarter),
    "soyear": _date_bound_spec("soyear", start_of_year),
    "eoyear": _date_bound_spec("eoyear", end_of_year),
    # column(name, v1, v2, ...) -> a named, homogeneously typed column.
    # table(col1, col2, ...) -> a table built from same-length columns.
    "column": FunctionSpec("column", 2, None, False, _column_result, _column_impl),
    "table": FunctionSpec("table", 1, None, False, _table_result, _table_impl),
    # rowcount(t)/colcount(t) -> the row/column count of a table or matrix.
    "rowcount": FunctionSpec("rowcount", 1, 1, False, _rowcount_result, _rowcount_impl),
    "colcount": FunctionSpec("colcount", 1, 1, False, _colcount_result, _colcount_impl),
    # array(v1, v2, ...) -> a headerless, homogeneously typed sequence.
    # matrix(row1, row2, ...) -> a 2D grid built from same-length,
    # same-element-type array() rows.
    "array": FunctionSpec("array", 1, None, False, _array_result, _array_impl),
    "matrix": FunctionSpec("matrix", 1, None, False, _matrix_result, _matrix_impl),
    # len(a) -> an array's element count.
    "len": FunctionSpec("len", 1, 1, False, _length_result, _length_impl),
    # at(a, i) / at(m, row, col) -> the 1-indexed element of an array,
    # or the (row, col) element of a matrix.
    # Now supports text slicing: at(text, i) -> the i-th character of 'text'.
    "at": FunctionSpec("at", 2, 3, False, _at_result, _at_impl),
    # left(text, count) -> the first 'count' characters of 'text'.
    "left": FunctionSpec("left", 1, 2, False, _text_slice_result("left"), _left_impl),
    # right(text, count) -> the last 'count' characters of 'text'.
    "right": FunctionSpec(
        "right", 1, 2, False, _text_slice_result("right"), _right_impl
    ),
    # mid(text, start, count) -> the 'count' characters of 'text' starting at 'start'.
    "mid": FunctionSpec("mid", 3, 3, False, _mid_result, _mid_impl),
    # format(number, format) -> the number formatted according to the given format string.
    "format": FunctionSpec("format", 2, 2, False, _text_result, _text_impl),
    # concat(delimiter, ignore_empty, value...) -> the values concatenated with the delimiter,
    # with empty values ignored if ignore_empty is true.
    "concat": FunctionSpec("concat", 1, None, False, _concat_result, _concat_impl),
    # filter(t, [row expr]) -> the rows of t where the row expression
    # is true.
    "filter": FunctionSpec(
        "filter", 2, 2, True, _filter_result, _filter_impl, row_scope_arg=1
    ),
    # select(t, "col1", "col2", ...) -> t narrowed to just those columns.
    "select": FunctionSpec("select", 2, None, False, _select_result, _select_impl),
    # append(t, [row expr...]) -> t with a new row appended.
    "append": FunctionSpec("append", 1, None, False, _append_result, _append_impl),
    # extend(t, "new_col", [row expr]) -> t with an extra computed column.
    "extend": FunctionSpec(
        "extend", 2, 3, True, _extend_result, _extend_impl, row_scope_arg=2
    ),
    # sort(t, [row expr][, "asc"|"desc"]) -> t's rows reordered by the
    # row expression's value (ascending by default).
    "sort": FunctionSpec("sort", 2, 3, True, _sort_result, _sort_impl, row_scope_arg=1),
    # groupby(t, "group_col", "agg_col", "sum"|"avg"|"min"|"max"|"count")
    # -> one row per distinct group_col value, with the aggregate applied.
    "groupby": FunctionSpec("groupby", 4, 4, False, _groupby_result, _groupby_impl),
    # type_of(v) -> the type of v as a string.
    "type_of": FunctionSpec("type_of", 1, 1, False, _type_of_result, _type_of_impl),
    # upper(text) -> the text converted to uppercase.
    "upper": FunctionSpec("upper", 1, 1, False, _upper_result, _upper_impl),
    # lower(text) -> the text converted to lowercase.
    "lower": FunctionSpec("lower", 1, 1, False, _lower_result, _lower_impl),
    # title(text) -> the text with the first letter of each word capitalized.
    "title": FunctionSpec("title", 1, 1, False, _title_result, _title_impl),
    # capitalize(text) -> the text with the first letter capitalized.
    "capitalize": FunctionSpec(
        "capitalize", 1, 1, False, _capitalize_result, _capitalize_impl
    ),
    # dayname(date|datetime) -> the name of the day of the week.
    "dayname_ph": FunctionSpec(
        "dayname_ph", 1, 1, False, _dayname_with_ph_result, _dayname_with_ph_impl
    ),
    # rate_bucket(table) -> a table with rate bucket columns added.
    # rate_bucket(date, start_time, end_time, day_name) ->
    "rate_bucket": FunctionSpec(
        "rate_bucket", 1, 4, False, _rate_bucket_result, _rate_bucket_impl
    ),
}
