"""Function registry.

Each function declares its arity, whether it evaluates its own
arguments (lazy), its static result type, and its implementation.
UDFs later become entries added to a copy of this registry.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from .calendar_utils import (
    end_of_month,
    end_of_quarter,
    end_of_year,
    start_of_month,
    start_of_quarter,
    start_of_year,
    timedelta_to_duration,
)
from .operators import _guard_indeterminate, compare_key
from .parser import Call, Literal
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
    result_type: Callable[[list[Category], Call], Category]
    # eager: impl(values) -> value
    # lazy:  impl(argument nodes, environment, evaluate, row_scope) -> value
    impl: Callable[..., Value]
    # Index (>= 1) of the one argument that's a row expression, type-
    # checked under a row scope derived from argument 0's (the table's)
    # schema instead of the ambient scope. None for every function that
    # isn't a row-scoped table verb (filter/extend/sort).
    row_scope_arg: int | None = None


def _fail(node, message):
    """Raise an ExpressionError positioned at `node`'s call site."""

    raise ExpressionError(message, node.position)


def _fixed(category):
    """A result_type for a function whose result category never
    depends on its arguments (today(), pi(), blank(), ...).
    """

    return lambda categories, node: category


NUMERIC_CATEGORIES = {"int", "decimal"}


def _is_type(category, name: str) -> bool:
    # Bare-name check, deliberately ignoring .fields: a compound Type's
    # `==` is schema-sensitive (see values.Type), but here we only care
    # whether this is "a column" / "a table" at all, any schema.
    return isinstance(category, Type) and str.__eq__(category, name)


def _is_sequence_type(category) -> bool:
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
    """Raise if `value` is NaN or infinite, naming `operation` in the
    error (e.g. "round() cannot operate on NaN.").
    """

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

_ORDERABLE = {"date", "datetime", "time", "currency", "tonnage", "percent", "text"}


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

_SUMMABLE = {"currency", "tonnage", "percent", "duration", "complex"}


def _sum_result(categories, node):
    if len(categories) == 1 and _is_sequence_type(categories[0]):
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


# ---- days_between ------------------------------------------------


def _days_between_result(categories, node):
    if categories != ["date", "date"]:
        _fail(node, "days_between() requires two dates.")

    return "int"


# # ---- dayname / time-intelligence date boundaries ---------------------

# _DAY_ABBREV = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# _DAY_FULL = (
#     "Monday",
#     "Tuesday",
#     "Wednesday",
#     "Thursday",
#     "Friday",
#     "Saturday",
#     "Sunday",
# )


# def _dayname_result(categories, node):
#     if categories[0] not in ("date", "datetime"):
#         _fail(node, "dayname() requires a date or datetime.")

#     if len(categories) == 2:
#         pattern_node = node.args[1]

#         if not isinstance(pattern_node, Literal) or pattern_node.value not in (
#             "%a",
#             "%A",
#         ):
#             _fail(node, 'dayname()\'s pattern must be "%a" or "%A".')

#     return "text"


# def _dayname_impl(values):
#     value = values[0]
#     pattern = values[1] if len(values) == 2 else "%a"
#     names = _DAY_FULL if pattern == "%A" else _DAY_ABBREV

#     return names[value.weekday()]


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
    if not _is_type(categories[0], "array"):
        _fail(node, "length() requires an array.")

    return "int"


def _length_impl(values):
    return len(values[0].values)


def _at_result(categories, node):
    base = categories[0]

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

    _fail(node, "at() requires an array or matrix.")


def _at_impl(values):
    base = values[0]

    if isinstance(base, Array):
        (index,) = values[1:]

        if not 1 <= index <= len(base.values):
            raise ExpressionError(
                f"Index {index} is out of range for an array of length "
                f"{len(base.values)} (at() is 1-indexed)."
            )

        return base.values[index - 1]

    row, col = values[1:]
    rows, cols = base.shape

    if not 1 <= row <= rows or not 1 <= col <= cols:
        raise ExpressionError(
            f"Index ({row}, {col}) is out of range for a {rows}x{cols} "
            "matrix (at() is 1-indexed)."
        )

    return base.rows[row - 1][col - 1]


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


def _row_dicts(table):
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


def _extend_result(categories, node):
    _require_table(node, categories[0], "extend()")

    name_node = node.args[1]

    if not isinstance(name_node, Literal) or not isinstance(name_node.value, str):
        _fail(node, "extend()'s second argument must be a literal text column name.")

    new_name = name_node.value

    if any(name.lower() == new_name.lower() for name, _ in categories[0].fields):
        _fail(node, f"extend() column name {new_name!r} already exists in this table.")

    return Type("table", fields=categories[0].fields + ((new_name, categories[2]),))


def _extend_impl(args, environment, evaluate, row_scope):
    table = evaluate(args[0], environment, row_scope)
    new_name = evaluate(args[1], environment, row_scope)

    if table.row_count == 0:
        # The checker already knows the new column's type from the row
        # expression's structure; the evaluator, like every other node
        # here, only ever re-derives categories from real values
        # (category_of), and there are none to derive from.
        raise ExpressionError(
            "extend() cannot determine a new column's type on an empty table.",
            args[2].position,
        )

    new_values = tuple(evaluate(args[2], environment, row) for row in _row_dicts(table))

    return Table(
        schema=table.schema + ((new_name, category_of(new_values[0])),),
        columns=table.columns + (new_values,),
    )


_SORTABLE: set[str] = NUMERIC_CATEGORIES | _ORDERABLE


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
    # days_between(a, b) -> b - a in whole days, as a plain int.
    "days_between": FunctionSpec(
        "days_between",
        2,
        2,
        False,
        _days_between_result,
        lambda values: (values[1] - values[0]).days,
    ),
    # "dayname": FunctionSpec("dayname", 1, 2, False, _dayname_result, _dayname_impl),
    # startof.../endof... (month/quarter/year) -> the first/last day of
    # the period containing a date or datetime (datetime in, midnight
    # datetime out) — DAX's STARTOFMONTH/ENDOFMONTH/... vocabulary.
    "startofmonth": _date_bound_spec("startofmonth", start_of_month),
    "endofmonth": _date_bound_spec("endofmonth", end_of_month),
    "startofquarter": _date_bound_spec("startofquarter", start_of_quarter),
    "endofquarter": _date_bound_spec("endofquarter", end_of_quarter),
    "startofyear": _date_bound_spec("startofyear", start_of_year),
    "endofyear": _date_bound_spec("endofyear", end_of_year),
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
    # length(a) -> an array's element count.
    "length": FunctionSpec("length", 1, 1, False, _length_result, _length_impl),
    # at(a, i) / at(m, row, col) -> the 1-indexed element of an array,
    # or the (row, col) element of a matrix.
    "at": FunctionSpec("at", 2, 3, False, _at_result, _at_impl),
    # filter(t, [row expr]) -> the rows of t where the row expression
    # is true.
    "filter": FunctionSpec(
        "filter", 2, 2, True, _filter_result, _filter_impl, row_scope_arg=1
    ),
    # select(t, "col1", "col2", ...) -> t narrowed to just those columns.
    "select": FunctionSpec("select", 2, None, False, _select_result, _select_impl),
    # extend(t, "new_col", [row expr]) -> t with an extra computed column.
    "extend": FunctionSpec(
        "extend", 3, 3, True, _extend_result, _extend_impl, row_scope_arg=2
    ),
    # sort(t, [row expr][, "asc"|"desc"]) -> t's rows reordered by the
    # row expression's value (ascending by default).
    "sort": FunctionSpec("sort", 2, 3, True, _sort_result, _sort_impl, row_scope_arg=1),
    # groupby(t, "group_col", "agg_col", "sum"|"avg"|"min"|"max"|"count")
    # -> one row per distinct group_col value, with the aggregate applied.
    "groupby": FunctionSpec("groupby", 4, 4, False, _groupby_result, _groupby_impl),
}
