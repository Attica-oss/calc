"""The operator dispatch tables: what binary/unary ops mean per type.

Each category pairing (op, left category, right category) or
(op, category) maps to a (result category, implementation) pair. An
unregistered combination is automatically a type error — no
special-casing required at the call site.
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import (
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)

from .calendar_utils import (
    add_duration_to_date,
    add_duration_to_datetime,
    add_duration_to_time,
    time_to_seconds,
    timedelta_to_duration,
)
from .values import (
    Array,
    Column,
    Complex,
    Duration,
    ExpressionError,
    Matrix,
    Number,
    Quantity,
    Type,
    Unit,
    Value,
    negate_duration,
    to_decimal,
)

type _BinaryKey = tuple[str, str, str]
type _UnaryKey = tuple[str, str]

# (result category, implementation) — result may be a plain string or,
# for a rule whose result category depends on the operand categories
# (e.g. int + int -> int but int + decimal -> decimal), a callable
# (left_category, right_category) -> category.
type _BinaryRule = tuple[
    str | Callable[[str, str], str], Callable[[Value, Value], Value]
]
type _UnaryRule = tuple[str, Callable[[Value], Value]]

BINARY_RULES: dict[_BinaryKey, _BinaryRule] = {}
UNARY_RULES: dict[_UnaryKey, _UnaryRule] = {}

NUMERIC = ("int", "decimal")


def _spread(category):
    """Expand the "number" registration shorthand to both numeric
    categories; pass any other category through unchanged.
    """

    return NUMERIC if category == "number" else (category,)


def register_binary(op, left, right, result, impl, symmetric=False):
    """Register (op, left, right) -> (result, impl) in BINARY_RULES.

    "number" in `left`/`right` expands to both int and decimal (see
    _spread). If `symmetric`, also registers the (right, left) pairing
    with `impl`'s arguments swapped, so e.g. `date + duration` and
    `duration + date` share one implementation without the caller
    having to write both directions by hand.
    """

    for left_cat in _spread(left):
        for right_cat in _spread(right):
            BINARY_RULES[(op, left_cat, right_cat)] = (result, impl)

            if symmetric:
                BINARY_RULES[(op, right_cat, left_cat)] = (
                    result,
                    lambda a, b, _impl=impl: _impl(b, a),
                )


def register_unary(op, category, result, impl):
    """Register (op, category) -> (result, impl) in UNARY_RULES."""

    UNARY_RULES[(op, category)] = (result, impl)


# ---- Numbers -----------------------------------------------------


def numeric_result(left_cat, right_cat):
    """int only stays int with another int; any decimal operand widens
    the result to decimal (registered as the result-category callable
    for +, -, *, //, % over "number").
    """

    return "int" if left_cat == right_cat == "int" else "decimal"


def _nonzero(value):
    """Raise if `value` is zero; guards every divide/modulo below."""

    if value == 0:
        raise ExpressionError("Cannot divide by zero.")


def _guard_indeterminate(compute):
    """Run a numeric operator, turning an indeterminate form (inf - inf,
    inf / inf, 0 * inf, ...) into a clear ExpressionError instead of
    letting Python's decimal.InvalidOperation leak out raw.

    Deliberately an error rather than a silently-returned Blank: the
    type checker already promised a definite numeric category for
    this expression before evaluation ran, and honoring that promise
    — never handing back a value whose actual category disagrees with
    what was statically declared — is the whole point of "type safe"
    here. Blank only ever appears where you explicitly asked for it,
    via blank().
    """

    try:
        return compute()
    except InvalidOperation as error:
        raise ExpressionError(
            "This is an indeterminate form involving infinity "
            "(∞ − ∞, ∞ / ∞, or 0 × ∞) "
            "and has no defined result."
        ) from error


def numeric_add(a, b):
    return _guard_indeterminate(lambda: a + b)


def numeric_subtract(a, b):
    return _guard_indeterminate(lambda: a - b)


def numeric_multiply(a, b):
    return _guard_indeterminate(lambda: a * b)


def numeric_divide(a, b):
    _nonzero(b)
    # int / int must not fall into binary floats.
    # return to_decimal(a) / to_decimal(b)

    left = to_decimal(a)
    right = to_decimal(b)

    _nonzero(right)

    if left.is_infinite() and right.is_infinite():
        raise ExpressionError("infinity divided by infinity is indeterminate")

    try:
        return left / right
    except InvalidOperation as error:
        raise ExpressionError(
            "The division produced an indeterminate result."
        ) from error


def numeric_floordiv(a, b):
    _nonzero(b)
    return a // b


def numeric_modulo(a, b):
    _nonzero(b)
    return a % b


def numeric_power(base, exponent):
    if abs(exponent) > 100:
        raise ExpressionError("The exponent is too large.")

    try:
        return to_decimal(base) ** to_decimal(exponent)
    except (InvalidOperation, Overflow, DivisionByZero) as error:
        raise ExpressionError("The exponentiation could not be calculated.") from error


register_binary("+", "number", "number", numeric_result, numeric_add)
register_binary("-", "number", "number", numeric_result, numeric_subtract)
register_binary("*", "number", "number", numeric_result, numeric_multiply)
register_binary("/", "number", "number", "decimal", numeric_divide)
register_binary("//", "number", "number", numeric_result, numeric_floordiv)
register_binary("%", "number", "number", numeric_result, numeric_modulo)
# ** always returns decimal: the sign of the exponent is a runtime
# value, so int ** int -> int would not be statically sound.
register_binary("**", "number", "number", "decimal", numeric_power)

# ---- Durations and temporals -------------------------------------


def duration_add(a, b) -> Duration:
    """Summing durations"""
    return Duration(
        months=a.months + b.months,
        days=a.days + b.days,
        seconds=a.seconds + b.seconds,
    )


def duration_subtract(a, b) -> Duration:
    """Subtracting durations"""
    return duration_add(a, negate_duration(b))


def duration_scale(value, multiplier: Number) -> Duration:
    """Scaling durations"""
    return Duration(
        months=value.months * multiplier,
        days=value.days * multiplier,
        seconds=value.seconds * multiplier,
    )


def duration_divide(value, divisor) -> Duration:
    """Dividing durations"""
    _nonzero(divisor)

    if value.months % divisor or value.days % divisor or value.seconds % divisor:
        raise ExpressionError("The duration cannot be divided into an exact duration.")

    return Duration(
        months=value.months // divisor,
        days=value.days // divisor,
        seconds=value.seconds // divisor,
    )


register_binary("+", "duration", "duration", "duration", duration_add)
register_binary("-", "duration", "duration", "duration", duration_subtract)

register_binary("+", "date", "duration", "date", add_duration_to_date, symmetric=True)
register_binary(
    "+",
    "datetime",
    "duration",
    "datetime",
    add_duration_to_datetime,
    symmetric=True,
)
register_binary("+", "time", "duration", "time", add_duration_to_time, symmetric=True)

register_binary(
    "-",
    "date",
    "duration",
    "date",
    lambda value, dur: add_duration_to_date(value, negate_duration(dur)),
)
register_binary(
    "-",
    "datetime",
    "duration",
    "datetime",
    lambda value, dur: add_duration_to_datetime(value, negate_duration(dur)),
)
register_binary(
    "-",
    "time",
    "duration",
    "time",
    lambda value, dur: add_duration_to_time(value, negate_duration(dur)),
)

# Consistency fix: every temporal difference is now a duration.
# (date - date used to return a bare int; days_between() covers
# the "give me a number" case.)
register_binary(
    "-",
    "date",
    "date",
    "duration",
    lambda a, b: duration_add(Duration(days=(a - b).days), Duration(days=1)),
)
register_binary(
    "-",
    "datetime",
    "datetime",
    "duration",
    lambda a, b: timedelta_to_duration(a - b),
)
register_binary(
    "-",
    "time",
    "time",
    "duration",
    lambda a, b: Duration(seconds=time_to_seconds(a) - time_to_seconds(b)),
)

# Durations scale only by whole numbers; "duration * decimal" is
# simply not in the table, so the checker rejects it with a clear
# message before evaluation.
register_binary("*", "duration", "int", "duration", duration_scale, symmetric=True)
register_binary("/", "duration", "int", "duration", duration_divide)


# ---- Collections: element-wise lifting ---------------------------
#
# Arrays and matrices deliberately have *no* registered rules of their
# own. Their category is a compound Type (e.g. array{currency}), which
# by construction never equals a flat dispatch key like "array" (see
# values.Type), so a row registered under "array" could never match
# anything and would silently do nothing.
#
# Instead, an operator on a collection is *lifted* from the rule
# already registered for its element type: array{X} + array{X}
# resolves ("+", X, X), applies that scalar impl element-wise, and
# takes its result category as the new element type. So
# array{currency} + array{currency} works because currency + currency
# does; array{int} / array{int} yields array{decimal} because int /
# int yields decimal; array{text} + array{text} stays a type error
# because text + text is one. Every element type is covered for free,
# and "no silent coercion" is preserved by construction.
#
# A scalar operand broadcasts: array{currency} * 2 resolves
# ("*", "currency", "int") and scales every element.

# Arithmetic only. Comparisons are excluded on purpose: everywhere
# else in the language `a = b` is a boolean, and lifting would make
# array = array an array{boolean} that and()/if() can't consume.
ELEMENTWISE_BINARY_OPS = frozenset({"+", "-", "*", "/", "//", "%", "**"})
ELEMENTWISE_UNARY_OPS = frozenset({"+", "-"})

COLLECTION_KINDS = ("array", "matrix", "column")


def _as_type(category) -> Type:
    """Normalize a result category (which a flat registration writes as
    a plain string) into a Type, preserving an already-compound one.
    """

    return category if isinstance(category, Type) else Type(category)


def _element_type(category, kind: str) -> Type | None:
    """The element type of an array/matrix category, or None if
    `category` isn't a collection of that kind.
    """

    if not isinstance(category, Type) or not str.__eq__(category, kind):
        return None

    fields = category.fields

    if not fields or len(fields) != 1 or fields[0][0] is not None:
        return None

    return _as_type(fields[0][1])


def _column_type(category) -> tuple[str, Type] | None:
    if not isinstance(category, Type) or not str.__eq__(category, "column"):
        return None

    fields = category.fields

    if not fields or len(fields) != 1:
        return None

    name, element_type = fields[0]
    if name is None:
        return None

    return name, _as_type(element_type)


def _collection(category) -> tuple[str, Type] | None:
    """(kind, element type) if `category` is an array or matrix."""

    for kind in COLLECTION_KINDS:
        element = _element_type(category, kind)

        if element is not None:
            return kind, element

    return None


def _scalar_rule(op, left, right):
    """The rule for two element categories, with a callable result
    category already resolved. Recurses through resolve_binary() so a
    nested array{array{int}} lifts one layer at a time; each step
    strips a collection layer, so this terminates.
    """

    rule = resolve_binary(op, left, right)

    if rule is None:
        return None

    result, impl = rule
    return _as_type(result if isinstance(result, str) else result(left, right)), impl


def _pairs(left, right, describe, describe_right=None):
    """Zip two operands element-wise, broadcasting whichever side is a
    scalar. Length/shape agreement is a *runtime* check: a collection's
    length isn't part of its type, so the checker can't see it.

    describe_right defaults to describe, for the common case where both
    sides are rendered the same way; pass it separately when the two
    sides need different context (e.g. each matrix's own column count).
    """

    describe_right = describe if describe_right is None else describe_right
    left_items = left if isinstance(left, tuple) else None
    right_items = right if isinstance(right, tuple) else None

    if left_items is not None and right_items is not None:
        if len(left_items) != len(right_items):
            raise ExpressionError(
                f"Cannot combine {describe(len(left_items))} "
                f"with {describe_right(len(right_items))}."
            )

        return zip(left_items, right_items)

    if left_items is not None:
        return ((item, right) for item in left_items)

    return ((left, item) for item in right_items)


def _lift_array(impl, element_type):
    def apply(left, right):
        left_values = left.values if isinstance(left, Array) else left
        right_values = right.values if isinstance(right, Array) else right

        return Array(
            values=tuple(
                impl(a, b)
                for a, b in _pairs(
                    left_values,
                    right_values,
                    lambda size: f"an array of length {size}",
                )
            ),
            element_type=element_type,
        )

    return apply


def _lift_binary_column(
    op,
    left,
    right,
    left_column,
    right_column,
):
    left_element = left_column[1] if left_column else left
    right_element = right_column[1] if right_column else right

    element = _scalar_rule(
        op,
        left_element,
        right_element,
    )

    if element is None:
        return None

    element_type, impl = element

    def apply(left_value, right_value):
        left_values = (
            left_value.values if isinstance(left_value, Column) else left_value
        )
        right_values = (
            right_value.values if isinstance(right_value, Column) else right_value
        )

        values = tuple(
            impl(a, b)
            for a, b in _pairs(
                left_values,
                right_values,
                lambda size: f"a column of length {size}",
            )
        )

        return Column(
            # choose your naming semantics
            name=None,
            values=values,
            element_type=element_type,
        )

    return (
        Type("column", fields=((None, element_type),)),
        apply,
    )


def _lift_matrix(impl, element_type):
    def apply(left, right):
        left_rows = left.rows if isinstance(left, Matrix) else left
        right_rows = right.rows if isinstance(right, Matrix) else right

        def left_shape(size):
            columns = left.shape[1] if isinstance(left, Matrix) else right.shape[1]
            return f"a {size}x{columns} matrix"

        def right_shape(size):
            columns = right.shape[1] if isinstance(right, Matrix) else left.shape[1]
            return f"a {size}x{columns} matrix"

        rows = tuple(
            tuple(
                impl(a, b)
                for a, b in _pairs(
                    left_row,
                    right_row,
                    lambda size: f"a matrix row of {size} columns",
                )
            )
            for left_row, right_row in _pairs(
                left_rows, right_rows, left_shape, right_shape
            )
        )

        return Matrix(element_type=element_type, rows=rows)

    return apply


_LIFTS = {"array": _lift_array, "matrix": _lift_matrix}


def _lift_binary(op, left, right):
    if op not in ELEMENTWISE_BINARY_OPS:
        return None

    left_column = _column_type(left)
    right_column = _column_type(right)

    if left_column is not None or right_column is not None:
        return _lift_binary_column(
            op,
            left,
            right,
            left_column,
            right_column,
        )

    left_collection = _collection(left)
    right_collection = _collection(right)

    if left_collection is None and right_collection is None:
        return None

    # array + matrix has no defined meaning; a mixed pairing stays a
    # type error rather than guessing a broadcast rule.
    if (
        left_collection is not None
        and right_collection is not None
        and left_collection[0] != right_collection[0]
    ):
        return None

    kind = (left_collection or right_collection)[0]
    element = _scalar_rule(
        op,
        left_collection[1] if left_collection else left,
        right_collection[1] if right_collection else right,
    )

    if element is None:
        return None

    element_type, impl = element

    return (
        Type(kind, fields=((None, element_type),)),
        _LIFTS[kind](impl, element_type),
    )


def resolve_binary(op, left, right):
    """The rule for (op, left, right): a directly registered one, or an
    element-wise lift over arrays/matrices. None if undefined.

    Called by both the type checker and the evaluator. Since it depends
    only on categories, both walks resolve the same rule.
    """

    return BINARY_RULES.get((op, left, right)) or _lift_binary(op, left, right)


def resolve_unary(op, category):
    """The unary counterpart of resolve_binary(): -array(1, 2) negates
    every element, if the element type has a unary rule.
    """

    rule = UNARY_RULES.get((op, category))

    if rule is not None:
        return rule

    if op not in ELEMENTWISE_UNARY_OPS:
        return None

    collection = _collection(category)

    if collection is None:
        return None

    kind, element = collection
    element_rule = UNARY_RULES.get((op, element))

    if element_rule is None:
        return None

    element_type, impl = _as_type(element_rule[0]), element_rule[1]

    def apply(value):
        if kind == "array":
            return Array(
                values=tuple(impl(item) for item in value.values),
                element_type=element_type,
            )

        return Matrix(
            element_type=element_type,
            rows=tuple(tuple(impl(item) for item in row) for row in value.rows),
        )

    return Type(kind, fields=((None, element_type),)), apply


# ---- Quantities (currency, tonnage, and future units) ------------


def quantity_add(a, b):
    return Quantity(a.value + b.value, a.unit)


def quantity_subtract(a, b):
    return Quantity(a.value - b.value, a.unit)


def quantity_scale(quantity, number):
    return Quantity(quantity.value * to_decimal(number), quantity.unit)


def quantity_divide(quantity, number):
    _nonzero(number)
    return Quantity(quantity.value / to_decimal(number), quantity.unit)


def quantity_ratio(a, b):
    _nonzero(b.value)
    # unit / same unit cancels out into a plain number.
    return a.value / b.value


for _unit_category in ("currency", "tonnage"):
    register_binary("+", _unit_category, _unit_category, _unit_category, quantity_add)
    register_binary(
        "-",
        _unit_category,
        _unit_category,
        _unit_category,
        quantity_subtract,
    )
    register_binary(
        "*",
        _unit_category,
        "number",
        _unit_category,
        quantity_scale,
        symmetric=True,
    )
    register_binary("/", _unit_category, "number", _unit_category, quantity_divide)
    register_binary("/", _unit_category, _unit_category, "decimal", quantity_ratio)

# ---- Currency x tonnage ------------------------------------------
#
# Convenience rule: currency acts as an implicit per-tonne rate, so
# $450 * 2.4t -> $1,080.00 (and 2.4t * $450 the same). Dimensionally
# this is a cheat — the honest type of $450 here is a $/t rate — so
# when rate units land, this row should move to (currency_per_tonne,
# tonnage) and plain currency * tonnage should go back to being a
# type error.


def currency_times_tonnage(money, tons):
    return Quantity(money.value * tons.value, Unit.CURRENCY)


register_binary(
    "*",
    "currency",
    "tonnage",
    "currency",
    currency_times_tonnage,
    symmetric=True,
)

# Other cross-unit arithmetic ($10 / 2t, etc.) stays unregistered
# until rate units exist, so it fails type checking instead of
# guessing.

# ---- Percentages -------------------------------------------------
#
# Percent stays out of the generic unit loop because its `*` means
# "apply" (Excel-style: 200 * 10% -> 20, $5.20 * 1.5% -> $0.08),
# not "scale" like currency/tonnage. Its own algebra:


def percent_apply_quantity(quantity, percent):
    return Quantity(quantity.value * percent.value, quantity.unit)


def percent_apply_number(number, percent):
    return to_decimal(number) * percent.value


register_binary("+", "percent", "percent", "percent", quantity_add)
register_binary("-", "percent", "percent", "percent", quantity_subtract)

# 50% * 10% -> 5% (a fraction of a fraction is a fraction).
register_binary(
    "*",
    "percent",
    "percent",
    "percent",
    lambda a, b: Quantity(a.value * b.value, a.unit),
)

# Applying a percentage preserves the target's type.
for _target in ("currency", "tonnage"):
    register_binary(
        "*",
        _target,
        "percent",
        _target,
        percent_apply_quantity,
        symmetric=True,
    )

register_binary(
    "*",
    "number",
    "percent",
    "decimal",
    percent_apply_number,
    symmetric=True,
)

# 3% / 2 -> 1.5%; 10% / 5% -> 2 (the ratio cancels the unit).
register_binary("/", "percent", "number", "percent", quantity_divide)
register_binary("/", "percent", "percent", "decimal", quantity_ratio)

# $100 + 5% (grow-by) is deliberately a type error: it reads two
# ways, so we make the user write $100 * 5% + $100 or similar.


# ---- Complex numbers -----------------------------------------------
#
# A plain number embeds into the complex plane as itself + 0i. Each
# rule below converts both operands first, then does the standard
# complex-number formula — the conversion is what lets `3 + 4i` and
# `4i + 3` and `(1+2i) * 3` all resolve through the same handful of
# functions instead of a combinatorial number of cross-type rules.


def as_complex(value) -> Complex:
    if isinstance(value, Complex):
        return value

    return Complex(to_decimal(value), Decimal(0))


def complex_add(a, b):
    a, b = as_complex(a), as_complex(b)
    return Complex(a.real + b.real, a.imag + b.imag)


def complex_subtract(a, b):
    a, b = as_complex(a), as_complex(b)
    return Complex(a.real - b.real, a.imag - b.imag)


def complex_multiply(a, b):
    a, b = as_complex(a), as_complex(b)
    return Complex(
        a.real * b.real - a.imag * b.imag,
        a.real * b.imag + a.imag * b.real,
    )


def complex_divide(a, b):
    a, b = as_complex(a), as_complex(b)
    denominator = b.real * b.real + b.imag * b.imag
    _nonzero(denominator)

    return Complex(
        (a.real * b.real + a.imag * b.imag) / denominator,
        (a.imag * b.real - a.real * b.imag) / denominator,
    )


register_binary("+", "complex", "complex", "complex", complex_add)
register_binary("+", "complex", "number", "complex", complex_add, symmetric=True)

# Subtraction and division aren't commutative, so both directions are
# registered explicitly rather than via symmetric=True — but the
# formula functions themselves don't need a swapped variant, because
# as_complex() converts each operand in place and the formula already
# respects argument order (a - b, not b - a).
register_binary("-", "complex", "complex", "complex", complex_subtract)
register_binary("-", "complex", "number", "complex", complex_subtract)
register_binary("-", "number", "complex", "complex", complex_subtract)

register_binary("*", "complex", "complex", "complex", complex_multiply)
register_binary("*", "complex", "number", "complex", complex_multiply, symmetric=True)

register_binary("/", "complex", "complex", "complex", complex_divide)
register_binary("/", "complex", "number", "complex", complex_divide)
register_binary("/", "number", "complex", "complex", complex_divide)

# Complex numbers have no total order, same restriction as Duration:
# only = and <> are registered below, in the comparisons section.
# There's also no complex == number rule, matching how currency and
# tonnage never compare against a bare number either — 4+0i and 4
# share a value but not a type, and the checker treats that as two
# different kinds of thing, consistently with every other unit type.


# ---- Text ----------------------------------------------------------
#
# Concatenation only — text doesn't join the generic unit loop or the
# numeric one, and unary +/-text is deliberately unregistered (a clean
# type error), same treatment as e.g. unary minus on a boolean.

# register_binary("+", "text", "text", "text", lambda a, b: a + b)

# ---- Comparisons -------------------------------------------------


def compare_key(value):
    """Unwrap a Quantity to its bare Decimal for comparison, so e.g.
    $5 < $10 compares 5 < 10 rather than the Quantity objects
    themselves. Non-Quantity values pass through unchanged.
    """

    return value.value if isinstance(value, Quantity) else value


_COMPARATORS = {
    "=": lambda a, b: compare_key(a) == compare_key(b),
    "<>": lambda a, b: compare_key(a) != compare_key(b),
    "<": lambda a, b: compare_key(a) < compare_key(b),
    "<=": lambda a, b: compare_key(a) <= compare_key(b),
    ">": lambda a, b: compare_key(a) > compare_key(b),
    ">=": lambda a, b: compare_key(a) >= compare_key(b),
}

for _category in (
    "number",
    "date",
    "datetime",
    "time",
    "currency",
    "tonnage",
    "percent",
    "text",
    "char",
):
    for _op, _impl in _COMPARATORS.items():
        register_binary(_op, _category, _category, "boolean", _impl)

# Durations have no canonical total order (is 1mo more than 30d?),
# so they support equality only. Booleans and complex numbers
# likewise (complex numbers have no order compatible with the field
# operations at all). Blank compares equal only to itself — blank()
# vs. any other category is a type error, same as every other
# cross-category comparison; isblank() is the intended way to test a
# value of unknown/generic type for blankness.
for _category in ("duration", "boolean", "complex", "blank"):
    register_binary("=", _category, _category, "boolean", _COMPARATORS["="])
    register_binary("<>", _category, _category, "boolean", _COMPARATORS["<>"])

# ---- Unary operators ---------------------------------------------

for _numeric_category in NUMERIC:
    register_unary("-", _numeric_category, _numeric_category, lambda v: -v)
    register_unary("+", _numeric_category, _numeric_category, lambda v: v)

register_unary("-", "duration", "duration", negate_duration)
register_unary("+", "duration", "duration", lambda v: v)

register_unary("-", "complex", "complex", lambda c: Complex(-c.real, -c.imag))
register_unary("+", "complex", "complex", lambda c: c)


for _unit_category in ("currency", "tonnage", "percent"):
    register_unary(
        "-",
        _unit_category,
        _unit_category,
        lambda q: Quantity(-q.value, q.unit),
    )
    register_unary("+", _unit_category, _unit_category, lambda q: q)
