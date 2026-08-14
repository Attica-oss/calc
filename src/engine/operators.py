"""The operator dispatch tables: what binary/unary ops mean per type.

Each category pairing (op, left category, right category) or
(op, category) maps to a (result category, implementation) pair. An
unregistered combination is automatically a type error — no
special-casing required at the call site.
"""

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
    Complex,
    Duration,
    ExpressionError,
    Quantity,
    Unit,
    negate_duration,
    to_decimal,
)

BINARY_RULES: dict = {}
UNARY_RULES: dict = {}

NUMERIC = ("int", "decimal")


def _spread(category):
    # "number" is registration shorthand for both numeric categories.
    return NUMERIC if category == "number" else (category,)


def register_binary(op, left, right, result, impl, symmetric=False):
    for left_cat in _spread(left):
        for right_cat in _spread(right):
            BINARY_RULES[(op, left_cat, right_cat)] = (result, impl)

            if symmetric:
                BINARY_RULES[(op, right_cat, left_cat)] = (
                    result,
                    lambda a, b, _impl=impl: _impl(b, a),
                )


def register_unary(op, category, result, impl):
    UNARY_RULES[(op, category)] = (result, impl)


# ---- Numbers -----------------------------------------------------


def numeric_result(left_cat, right_cat):
    return "int" if left_cat == right_cat == "int" else "decimal"


def _nonzero(value):
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


def duration_add(a, b):
    return Duration(
        months=a.months + b.months,
        days=a.days + b.days,
        seconds=a.seconds + b.seconds,
    )


def duration_subtract(a, b):
    return duration_add(a, negate_duration(b))


def duration_scale(value, multiplier):
    return Duration(
        months=value.months * multiplier,
        days=value.days * multiplier,
        seconds=value.seconds * multiplier,
    )


def duration_divide(value, divisor):
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
    "-", "date", "date", "duration", lambda a, b: Duration(days=(a - b).days)
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
