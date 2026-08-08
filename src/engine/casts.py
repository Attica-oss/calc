"""Casts: value::target.

A second dispatch table, deliberately separate from BINARY_RULES —
the key shape is different (source category, target keyword) rather
than (op, left category, right category), and unlike an operator, a
target keyword isn't itself a typed value with its own category, so
it can't be run through check_types the way an operand can. Same
spirit as the operator table, though: an unregistered combination is
automatically a clean type error, no special-casing required.
"""

from datetime import date, datetime, time
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from .formatting import format_result
from .values import ExpressionError, Quantity, Unit, to_decimal

CAST_RULES: dict = {}


def register_cast(source_category, target, result_category, impl):
    CAST_RULES[(source_category, target)] = (result_category, impl)


# ---- Field extraction (-> int) ------------------------------------
#
# Both singular and plural read naturally here (::day and ::days both
# make sense on a date), so both are registered rather than forcing
# one spelling — this is the one place in the cast vocabulary where
# that ambiguity has no real cost.


def register_field(source_category, singular, plural, impl):
    register_cast(source_category, singular, "int", impl)
    register_cast(source_category, plural, "int", impl)


register_field("date", "year", "years", lambda v: v.year)
register_field("date", "month", "months", lambda v: v.month)
register_field("date", "day", "days", lambda v: v.day)

register_field("datetime", "year", "years", lambda v: v.year)
register_field("datetime", "month", "months", lambda v: v.month)
register_field("datetime", "day", "days", lambda v: v.day)
register_field("datetime", "hour", "hours", lambda v: v.hour)
register_field("datetime", "minute", "minutes", lambda v: v.minute)
register_field("datetime", "second", "seconds", lambda v: v.second)

register_field("time", "hour", "hours", lambda v: v.hour)
register_field("time", "minute", "minutes", lambda v: v.minute)
register_field("time", "second", "seconds", lambda v: v.second)

# ---- Temporal conversions ------------------------------------------

register_cast("datetime", "date", "date", lambda v: v.date())
register_cast("datetime", "time", "time", lambda v: v.time())
register_cast("date", "datetime", "datetime", lambda v: datetime.combine(v, time()))

# Identity casts: harmless, and useful in a formula that doesn't want
# to care whether its input already happens to be the target type.
register_cast("date", "date", "date", lambda v: v)
register_cast("time", "time", "time", lambda v: v)
register_cast("datetime", "datetime", "datetime", lambda v: v)

# ---- Numeric <-> quantity conversions -------------------------------
#
# ::decimal always exposes the *raw stored* Decimal — for percent
# that's the ratio (5%::decimal is 0.05, not 5), consistent with how
# percent is represented everywhere else in the engine (e.g. $100 *
# 5% needs that same ratio). Going the other way, a plain number cast
# to percent is read the way the % literal reads it (5::percent means
# "5 percent", i.e. a ratio of 0.05) rather than as the raw ratio —
# so the two casts are actual inverses of each other, round-tripping
# 5% -> 0.05 -> back to 5%, not to a startling 500%.

for _unit_category in ("currency", "tonnage"):
    register_cast(_unit_category, "decimal", "decimal", lambda v: v.value)
    register_cast(
        "int",
        _unit_category,
        _unit_category,
        lambda v, _u=_unit_category: Quantity(to_decimal(v), Unit(_u)),
    )
    register_cast(
        "decimal",
        _unit_category,
        _unit_category,
        lambda v, _u=_unit_category: Quantity(v, Unit(_u)),
    )

register_cast("percent", "decimal", "decimal", lambda v: v.value)
register_cast("int", "percent", "percent", lambda v: Quantity(to_decimal(v) / 100, Unit.PERCENT))
register_cast("decimal", "percent", "percent", lambda v: Quantity(v / 100, Unit.PERCENT))

# decimal <-> int: widening is exact and free; narrowing truncates
# toward zero (a genuine cast, deliberately different from round(),
# which rounds half-up instead of chopping the fractional part).
register_cast("int", "decimal", "decimal", lambda v: to_decimal(v))
register_cast("decimal", "int", "int", lambda v: int(v.to_integral_value(rounding=ROUND_DOWN)))
register_cast("int", "int", "int", lambda v: v)
register_cast("decimal", "decimal", "decimal", lambda v: v)

# ---- Text conversions ----------------------------------------------
#
# ::text always succeeds and always means "however format_result would
# display it" — the same rendering the REPL and formulas already use,
# so `"total: " + $5::text` reads the way you'd expect it to.

for _category in (
    "int",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "time",
    "duration",
    "currency",
    "tonnage",
    "percent",
    "complex",
    "blank",
):
    register_cast(_category, "text", "text", format_result)

register_cast("text", "text", "text", lambda v: v)

# The other direction has to parse, so it can fail — a loud, clear
# ExpressionError rather than a silent garbage value, same spirit as
# every other cast in this file. Built once on top of _text_to_decimal
# and then composed with the *existing* decimal-based casts above
# (int, currency, tonnage, percent) rather than re-deriving their
# rounding/ratio semantics a second time — e.g. "5"::percent means "5
# percent" (a ratio of 0.05), the same as 5::percent already means,
# because it's literally the same registered implementation.


def _text_to_decimal(text: str) -> Decimal:
    try:
        return Decimal(text.strip())
    except InvalidOperation as error:
        raise ExpressionError(f"{text!r} is not a valid decimal number.") from error


def _compose_from_decimal(target: str):
    _, decimal_impl = CAST_RULES[("decimal", target)]
    return lambda text: decimal_impl(_text_to_decimal(text))


register_cast("text", "decimal", "decimal", _text_to_decimal)

for _target in ("int", "currency", "tonnage", "percent"):
    register_cast("text", _target, _target, _compose_from_decimal(_target))

_BOOLEAN_TEXT = {"true": True, "false": False}


def _text_to_boolean(text: str) -> bool:
    normalized = text.strip().lower()

    if normalized not in _BOOLEAN_TEXT:
        raise ExpressionError(f"{text!r} is not a valid boolean (use 'true' or 'false').")

    return _BOOLEAN_TEXT[normalized]


register_cast("text", "boolean", "boolean", _text_to_boolean)


def _text_to_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError as error:
        raise ExpressionError(f"{text!r} is not a valid date.") from error


def _text_to_datetime(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError as error:
        raise ExpressionError(f"{text!r} is not a valid datetime.") from error


def _text_to_time(text: str) -> time:
    try:
        return time.fromisoformat(text.strip())
    except ValueError as error:
        raise ExpressionError(f"{text!r} is not a valid time.") from error


register_cast("text", "date", "date", _text_to_date)
register_cast("text", "datetime", "datetime", _text_to_datetime)
register_cast("text", "time", "time", _text_to_time)
