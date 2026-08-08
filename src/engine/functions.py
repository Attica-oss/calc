"""Function registry.

Each function declares its arity, whether it evaluates its own
arguments (lazy), its static result type, and its implementation.
UDFs later become entries added to a copy of this registry.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from .calendar_utils import timedelta_to_duration
from .operators import _guard_indeterminate, compare_key
from .parser import Literal
from .values import (
    INFINITY,
    Blank,
    Column,
    Complex,
    Duration,
    ExpressionError,
    Quantity,
    Table,
    Type,
    category_of,
    negate_duration,
    to_decimal,
)


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    min_args: int
    max_args: int | None  # None means unbounded
    lazy: bool
    # (argument categories, call node) -> result category
    result_type: Callable
    # eager: impl(values) -> value
    # lazy:  impl(argument nodes, environment, evaluate) -> value
    impl: Callable


def _fail(node, message):
    raise ExpressionError(message, node.position)


def _fixed(category):
    return lambda categories, node: category


NUMERIC_CATEGORIES = {"int", "decimal"}


def _is_type(category, name: str) -> bool:
    # Bare-name check, deliberately ignoring .fields: a compound Type's
    # `==` is schema-sensitive (see values.Type), but here we only care
    # whether this is "a column" / "a table" at all, any schema.
    return isinstance(category, Type) and str.__eq__(category, name)


# Hardcoded to 50 significant digits — comfortably past Decimal's
# default 28-digit context precision, so results built from these
# constants are limited by the *arithmetic's* precision, not the
# constant's. Not computed at runtime: there's no benefit to deriving
# pi via a series each call when the digits never change.
PI = Decimal("3.14159265358979323846264338327950288419716939937511")
E = Decimal("2.71828182845904523536028747135266249775724709369996")

# ---- today / now / time ------------------------------------------


def _time_result(categories, node):
    if any(category != "int" for category in categories):
        _fail(node, "time() arguments must be whole numbers.")

    return "time"


def _time_impl(values):
    hour = values[0]
    minute = values[1]
    second = values[2] if len(values) == 3 else 0

    try:
        return time(hour, minute, second)
    except ValueError as error:
        raise ExpressionError(str(error)) from error


# ---- abs ---------------------------------------------------------


def _abs_result(categories, node):
    category = categories[0]

    # The modulus of a complex number is a plain decimal, not another
    # complex number — the one case where abs()'s result category
    # differs from its argument's.
    if category == "complex":
        return "decimal"

    if category in NUMERIC_CATEGORIES | {
        "duration",
        "currency",
        "tonnage",
        "percent",
    }:
        return category

    _fail(node, "abs() accepts a number, duration,  quantity, or complex.")


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


def _round_result(categories, node):
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
    if value.is_nan():
        raise ExpressionError(f"{operation} cannot operate on NaN.")

    if value.is_infinite():
        raise ExpressionError(f"{operation} cannot operate on an infinite value.")


def _round_impl(values):
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

_ORDERABLE = {"date", "datetime", "time", "currency", "tonnage", "percent"}


def _min_max_result(name):
    def result_type(categories, node):
        if len(categories) == 1 and _is_type(categories[0], "column"):
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
    def impl(values):
        if len(values) == 1 and isinstance(values[0], Column):
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

_SUMMABLE = {"currency", "tonnage", "percent", "duration", "complex"}


def _sum_result(categories, node):
    if len(categories) == 1 and _is_type(categories[0], "column"):
        _, element_type = categories[0].fields[0]

        if element_type in NUMERIC_CATEGORIES:
            return "int" if element_type == "int" else "decimal"

        if element_type in _SUMMABLE:
            return element_type

        _fail(
            node,
            "sum() over a column requires a numeric, quantity, duration, "
            "or complex column.",
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
    if len(values) == 1 and isinstance(values[0], Column):
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
    if len(categories) == 1 and _is_type(categories[0], "column"):
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
    if len(values) == 1 and isinstance(values[0], Column):
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
    def result_type(categories, node):
        if categories[0] != "complex":
            _fail(node, f"{name}() requires a complex number.")

        return "decimal" if name in {"re", "im"} else "complex"

    return result_type


def _re_impl(values):
    return values[0].real


def _im_impl(values):
    return values[0].imag


def _conj_impl(values):
    value = values[0]
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


def _ceil_quantity(x: Quantity, multiple: Quantity):
    if multiple.value <= 0:
        raise ExpressionError("ceil()'s multiple must be positive.")

    quotient = (x.value / multiple.value).to_integral_value(rounding=ROUND_CEILING)
    return Quantity(quotient * multiple.value, x.unit)


def _ceil_duration(x: Duration, multiple: Duration):
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
            "coalesce()'s first argument must be blank or the same "
            "type as the default.",
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
            "The first argument to if() must be a boolean condition, "
            "such as a comparison.",
        )

    if categories[1] != categories[2]:
        _fail(
            node,
            "Both branches of if() must have the same type.",
        )

    return categories[1]


def _if_impl(args, environment, evaluate):
    condition = evaluate(args[0], environment)
    chosen = args[1] if condition else args[2]
    return evaluate(chosen, environment)


# ---- days_between ------------------------------------------------


def _days_between_result(categories, node):
    if categories != ["date", "date"]:
        _fail(node, "days_between() requires two dates.")

    return "int"


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
    if not _is_type(categories[0], "table"):
        _fail(node, "rowcount() requires a table.")

    return "int"


def _rowcount_impl(values):
    return values[0].row_count


FUNCTIONS = {
    "today": FunctionSpec(
        "today",
        0,
        0,
        False,
        _fixed("date"),
        lambda values: datetime.now().date(),
    ),
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
    "pi": FunctionSpec("pi", 0, 0, False, _fixed("decimal"), lambda values: PI),
    "e": FunctionSpec("e", 0, 0, False, _fixed("decimal"), lambda values: E),
    "infinity": FunctionSpec(
        "infinity", 0, 0, False, _fixed("decimal"), lambda values: INFINITY
    ),
    "time": FunctionSpec("time", 2, 3, False, _time_result, _time_impl),
    "abs": FunctionSpec("abs", 1, 1, False, _abs_result, _abs_impl),
    "round": FunctionSpec("round", 1, 2, False, _round_result, _round_impl),
    "ceil": FunctionSpec("ceil", 2, 2, False, _ceil_result, _ceil_impl),
    "min": FunctionSpec(
        "min", 1, None, False, _min_max_result("min"), _min_max_impl(min)
    ),
    "max": FunctionSpec(
        "max", 1, None, False, _min_max_result("max"), _min_max_impl(max)
    ),
    "sum": FunctionSpec("sum", 1, None, False, _sum_result, _sum_impl),
    "avg": FunctionSpec("avg", 1, None, False, _avg_result, _avg_impl),
    "re": FunctionSpec("re", 1, 1, False, _complex_only_result("re"), _re_impl),
    "im": FunctionSpec("im", 1, 1, False, _complex_only_result("im"), _im_impl),
    "conj": FunctionSpec("conj", 1, 1, False, _complex_only_result("conj"), _conj_impl),
    "blank": FunctionSpec(
        "blank", 0, 0, False, _fixed("blank"), lambda values: Blank()
    ),
    "isblank": FunctionSpec("isblank", 1, 1, False, _isblank_result, _isblank_impl),
    "coalesce": FunctionSpec("coalesce", 2, 2, False, _coalesce_result, _coalesce_impl),
    "if": FunctionSpec("if", 3, 3, True, _if_result, _if_impl),
    "days_between": FunctionSpec(
        "days_between",
        2,
        2,
        False,
        _days_between_result,
        lambda values: (values[1] - values[0]).days,
    ),
    "column": FunctionSpec("column", 2, None, False, _column_result, _column_impl),
    "table": FunctionSpec("table", 1, None, False, _table_result, _table_impl),
    "rowcount": FunctionSpec("rowcount", 1, 1, False, _rowcount_result, _rowcount_impl),
}
