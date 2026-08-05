"""calc expression engine: tokenize -> parse -> type check -> evaluate.

Plain importable module — no marimo dependency. The notebook and the
CLI both import from here, so this file is the single source of truth.
"""

import calendar
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import (
    ROUND_HALF_UP,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from enum import Enum
from typing import TypeAlias
from collections.abc import Callable

# Type Aliases
Number: TypeAlias = int | Decimal
Temporal: TypeAlias = date | datetime | time


# ------------------------------------------------------------------
# Values and calendar arithmetic
#
# This section defines the *value domain*: what kinds of things an
# expression can produce. It knows nothing about syntax, operators,
# or functions.
# ------------------------------------------------------------------


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
    Unit.PERCENT: Decimal("0.000001"),  # 0.0001% precision
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
        object.__setattr__(
            self,
            "value",
            self.value.quantize(UNIT_QUANTA[self.unit], ROUND_HALF_UP),
        )


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
}


def label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def to_decimal(number: Number) -> Decimal:
    if isinstance(number, Decimal):
        return number

    return Decimal(number)


# ---- Calendar helpers --------------------------------------------


def add_months(value: date, months: int) -> date:
    """Add calendar months while keeping the date valid."""

    total_months = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(total_months, 12)
    month = zero_based_month + 1

    if not 1 <= year <= 9999:
        raise ExpressionError("The resulting date is outside the valid range.")

    final_day = min(
        value.day,
        calendar.monthrange(year, month)[1],
    )

    return value.replace(
        year=year,
        month=month,
        day=final_day,
    )


def add_duration_to_date(value: date, duration: Duration) -> date:
    """Add a duration to a date.

    Strict typing decision: a duration with hours/minutes/seconds
    cannot be added to a bare date. (The previous behaviour silently
    promoted the result to a midnight datetime, which would make the
    static result type depend on a runtime value.)
    """

    if duration.seconds:
        raise ExpressionError(
            "Cannot add hours, minutes, or seconds to a date. Use a datetime instead."
        )

    adjusted = add_months(value, duration.months)

    try:
        return adjusted + timedelta(days=duration.days)
    except OverflowError as error:
        raise ExpressionError(
            "The resulting date is outside the valid range."
        ) from error


def add_duration_to_datetime(
    value: datetime,
    duration: Duration,
) -> datetime:
    adjusted_date = add_months(value.date(), duration.months)

    adjusted = value.replace(
        year=adjusted_date.year,
        month=adjusted_date.month,
        day=adjusted_date.day,
    )

    try:
        return adjusted + timedelta(
            days=duration.days,
            seconds=duration.seconds,
        )
    except OverflowError as error:
        raise ExpressionError(
            "The resulting datetime is outside the valid range."
        ) from error


def add_duration_to_time(value: time, duration: Duration) -> time:
    if duration.months:
        raise ExpressionError(
            "Calendar months or years cannot be added to a clock time."
        )

    # A time has no date, so arithmetic wraps around midnight.
    reference = datetime.combine(date(2000, 1, 1), value)

    result = reference + timedelta(
        days=duration.days,
        seconds=duration.seconds,
    )

    return result.time()


def timedelta_to_duration(value: timedelta) -> Duration:
    total_seconds = int(value.total_seconds())

    days, seconds = divmod(abs(total_seconds), 86_400)

    if total_seconds < 0:
        days = -days
        seconds = -seconds

    return Duration(days=days, seconds=seconds)


def time_to_seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    position: int


TOKEN_RE = re.compile(
    r"""
    (?P<SPACE>\s+)
    |
    (?P<DATETIME>
        \d{4}-\d{2}-\d{2}
        [Tt]
        \d{2}:\d{2}
        (?::\d{2})?
    )
    |
    (?P<DATE>\d{4}-\d{2}-\d{2})
    |
    (?P<TIME>
        \d{2}:\d{2}
        (?::\d{2})?
    )
    |
    (?P<DURATION>
        (?:\d+(?:\.\d+)?|\.\d+)
        (?:min|mo|[dhwsy])
    )
    |
    (?P<CURRENCY>
        \$
        (?:\d+(?:\.\d*)?|\.\d+)
    )
    |
    (?P<TONNAGE>
        (?:\d+(?:\.\d*)?|\.\d+)
        t
    )
    |
    (?P<PERCENT>
        # A number immediately followed by '%' is a percent literal;
        # '%' with whitespace before it stays the modulo operator.
        (?:\d+(?:\.\d*)?|\.\d+)
        %
    )
    |
    (?P<NUMBER>
        (?:\d+(?:\.\d*)?|\.\d+)
        (?:[eE][+-]?\d+)?
    )
    |
    (?P<POWER>\*\*)
    |
    (?P<FLOORDIV>//)
    |
    (?P<LE><=)
    |
    (?P<GE>>=)
    |
    (?P<NE><>)
    |
    (?P<LT><)
    |
    (?P<GT>>)
    |
    (?P<EQ>=)
    |
    (?P<PLUS>\+)
    |
    (?P<MINUS>-)
    |
    (?P<MULTIPLY>\*)
    |
    (?P<DIVIDE>/)
    |
    (?P<MODULO>%)
    |
    (?P<LPAREN>\()
    |
    (?P<RPAREN>\))
    |
    (?P<COMMA>,)
    |
    (?P<IDENTIFIER>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


def tokenize(expression: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0

    while position < len(expression):
        match = TOKEN_RE.match(expression, position)

        if match is None:
            character = expression[position]
            raise ExpressionError(
                f"Unexpected character {character!r}.",
                position,
            )

        kind = match.lastgroup
        value = match.group()

        if kind != "SPACE":
            tokens.append(Token(kind=kind, value=value, position=position))

        position = match.end()

    tokens.append(Token("EOF", "", len(expression)))
    return tokens


# tokenize must be exported: the parser cell depends on it.
# ------------------------------------------------------------------
# AST and parser
#
# The parser only builds a tree; it never computes a value. That
# split is what makes variables, lazy functions such as if(), type
# checking, and dependency extraction possible.
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    value: object
    position: int


@dataclass(frozen=True)
class Var:
    name: str
    position: int


@dataclass(frozen=True)
class UnaryOp:
    op: str
    operand: object
    position: int


@dataclass(frozen=True)
class BinOp:
    op: str
    left: object
    right: object
    position: int


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple
    position: int


COMPARISON_TOKENS = {
    "EQ": "=",
    "NE": "<>",
    "LT": "<",
    "LE": "<=",
    "GT": ">",
    "GE": ">=",
}

ADDITIVE_TOKENS = {"PLUS": "+", "MINUS": "-"}

MULTIPLICATIVE_TOKENS = {
    "MULTIPLY": "*",
    "DIVIDE": "/",
    "FLOORDIV": "//",
    "MODULO": "%",
}

MAX_NESTING = 60


def parse_duration_literal(text: str, position: int) -> Duration:
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?|\.\d+)(min|mo|[dhwsy])",
        text,
    )

    if match is None:
        raise ExpressionError(f"Invalid duration: {text!r}.", position)

    amount_text, unit = match.groups()
    amount = float(amount_text)

    if not amount.is_integer():
        raise ExpressionError(
            "Durations must currently use whole numbers, such as "
            "30min, 2h, 3d, 4mo, or 1y.",
            position,
        )

    amount = int(amount)

    scale = {
        "s": Duration(seconds=amount),
        "min": Duration(seconds=amount * 60),
        "h": Duration(seconds=amount * 3600),
        "d": Duration(days=amount),
        "w": Duration(days=amount * 7),
        "mo": Duration(months=amount),
        "y": Duration(months=amount * 12),
    }

    return scale[unit]


# The token maps, MAX_NESTING, and parse_duration_literal must be
# exported too: the Parser cell uses them.
class Parser:
    def __init__(self, expression: str):
        self.expression = expression
        self.tokens = tokenize(expression)
        self.index = 0
        self.depth = 0

    @property
    def current(self):
        return self.tokens[self.index]

    def advance(self):
        token = self.current
        self.index += 1
        return token

    def accept(self, kind: str):
        if self.current.kind == kind:
            return self.advance()

        return None

    def expect(self, kind: str, description: str):
        token = self.accept(kind)

        if token is None:
            raise ExpressionError(
                f"Expected {description}.",
                self.current.position,
            )

        return token

    def parse(self):
        if self.current.kind == "EOF":
            raise ExpressionError("Enter an expression.")

        result = self.parse_comparison()

        if self.current.kind != "EOF":
            raise ExpressionError(
                f"Unexpected {self.current.value!r}.",
                self.current.position,
            )

        return result

    def parse_comparison(self):
        left = self.parse_addition()

        if self.current.kind in COMPARISON_TOKENS:
            token = self.advance()
            right = self.parse_addition()

            node = BinOp(
                op=COMPARISON_TOKENS[token.kind],
                left=left,
                right=right,
                position=token.position,
            )

            if self.current.kind in COMPARISON_TOKENS:
                raise ExpressionError(
                    "Chained comparisons are not supported.",
                    self.current.position,
                )

            return node

        return left

    def parse_addition(self):
        left = self.parse_multiplication()

        while self.current.kind in ADDITIVE_TOKENS:
            token = self.advance()
            right = self.parse_multiplication()

            left = BinOp(
                op=ADDITIVE_TOKENS[token.kind],
                left=left,
                right=right,
                position=token.position,
            )

        return left

    def parse_multiplication(self):
        left = self.parse_unary()

        while self.current.kind in MULTIPLICATIVE_TOKENS:
            token = self.advance()
            right = self.parse_unary()

            left = BinOp(
                op=MULTIPLICATIVE_TOKENS[token.kind],
                left=left,
                right=right,
                position=token.position,
            )

        return left

    def parse_unary(self):
        self.depth += 1

        if self.depth > MAX_NESTING:
            raise ExpressionError(
                "The expression is nested too deeply.",
                self.current.position,
            )

        try:
            token = self.accept("PLUS") or self.accept("MINUS")

            if token is not None:
                operand = self.parse_unary()
                op = "+" if token.kind == "PLUS" else "-"

                return UnaryOp(
                    op=op,
                    operand=operand,
                    position=token.position,
                )

            return self.parse_power()
        finally:
            self.depth -= 1

    def parse_power(self):
        left = self.parse_primary()

        # Recursion makes exponentiation right-associative:
        # 2 ** 3 ** 2 means 2 ** (3 ** 2).
        token = self.accept("POWER")

        if token is not None:
            right = self.parse_unary()

            return BinOp(
                op="**",
                left=left,
                right=right,
                position=token.position,
            )

        return left

    def parse_primary(self):
        token = self.current

        if token.kind == "NUMBER":
            self.advance()

            # Plain digit runs are ints. Everything else (decimal
            # point, scientific notation) becomes Decimal — never a
            # binary float — so money math stays exact.
            if any(character in token.value for character in ".eE"):
                return Literal(
                    value=Decimal(token.value),
                    position=token.position,
                )

            return Literal(
                value=int(token.value),
                position=token.position,
            )

        if token.kind == "CURRENCY":
            self.advance()

            return Literal(
                value=Quantity(Decimal(token.value[1:]), Unit.CURRENCY),
                position=token.position,
            )

        if token.kind == "TONNAGE":
            self.advance()

            return Literal(
                value=Quantity(Decimal(token.value[:-1]), Unit.TONNAGE),
                position=token.position,
            )

        if token.kind == "PERCENT":
            self.advance()

            # Stored as a ratio: 1.5% -> 0.015.
            return Literal(
                value=Quantity(
                    Decimal(token.value[:-1]) / 100,
                    Unit.PERCENT,
                ),
                position=token.position,
            )

        if token.kind == "DATETIME":
            self.advance()

            try:
                value = datetime.fromisoformat(token.value)
            except ValueError as error:
                raise ExpressionError(
                    f"{token.value!r} is not a valid datetime.",
                    token.position,
                ) from error

            return Literal(value=value, position=token.position)

        if token.kind == "DATE":
            self.advance()

            try:
                value = date.fromisoformat(token.value)
            except ValueError as error:
                raise ExpressionError(
                    f"{token.value!r} is not a valid date.",
                    token.position,
                ) from error

            return Literal(value=value, position=token.position)

        if token.kind == "TIME":
            self.advance()

            try:
                value = time.fromisoformat(token.value)
            except ValueError as error:
                raise ExpressionError(
                    f"{token.value!r} is not a valid time.",
                    token.position,
                ) from error

            return Literal(value=value, position=token.position)

        if token.kind == "DURATION":
            self.advance()

            return Literal(
                value=parse_duration_literal(token.value, token.position),
                position=token.position,
            )

        if token.kind == "IDENTIFIER":
            self.advance()

            if self.current.kind == "LPAREN":
                return self.parse_call(token)

            # Bare identifier: a variable reference. Names are
            # case-sensitive; function names are lowercased.
            return Var(name=token.value, position=token.position)

        if token.kind == "LPAREN":
            self.advance()
            self.depth += 1

            if self.depth > MAX_NESTING:
                raise ExpressionError(
                    "The expression is nested too deeply.",
                    token.position,
                )

            try:
                value = self.parse_comparison()
            finally:
                self.depth -= 1

            self.expect("RPAREN", "')'")
            return value

        raise ExpressionError(
            "Expected a number, date, duration, variable, function, or '('.",
            token.position,
        )

    def parse_call(self, name_token):
        self.expect("LPAREN", "'(' after the function name")

        arguments = []

        if self.current.kind != "RPAREN":
            while True:
                arguments.append(self.parse_comparison())

                if not self.accept("COMMA"):
                    break

        self.expect("RPAREN", "')'")

        return Call(
            name=name_token.value.lower(),
            args=tuple(arguments),
            position=name_token.position,
        )


def parse(expression: str):
    """Parse an expression into an AST without evaluating it."""

    return Parser(expression).parse()


def variables_in(node) -> tuple[str, ...]:
    """The set of variable names a formula depends on.

    This is the hook a spreadsheet needs to build its dependency
    graph — and it only exists because parsing is separate from
    evaluation.
    """

    names: set[str] = set()

    def walk(current):
        if isinstance(current, Var):
            names.add(current.name)
        elif isinstance(current, UnaryOp):
            walk(current.operand)
        elif isinstance(current, BinOp):
            walk(current.left)
            walk(current.right)
        elif isinstance(current, Call):
            for argument in current.args:
                walk(argument)

    walk(node)
    return tuple(sorted(names))


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


def numeric_divide(a, b):
    _nonzero(b)
    # int / int must not fall into binary floats.
    return to_decimal(a) / to_decimal(b)


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


register_binary("+", "number", "number", numeric_result, lambda a, b: a + b)
register_binary("-", "number", "number", numeric_result, lambda a, b: a - b)
register_binary("*", "number", "number", numeric_result, lambda a, b: a * b)
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
):
    for _op, _impl in _COMPARATORS.items():
        register_binary(_op, _category, _category, "boolean", _impl)

# Durations have no canonical total order (is 1mo more than 30d?),
# so they support equality only. Booleans likewise.
for _category in ("duration", "boolean"):
    register_binary("=", _category, _category, "boolean", _COMPARATORS["="])
    register_binary("<>", _category, _category, "boolean", _COMPARATORS["<>"])

# ---- Unary operators ---------------------------------------------

for _numeric_category in NUMERIC:
    register_unary("-", _numeric_category, _numeric_category, lambda v: -v)
    register_unary("+", _numeric_category, _numeric_category, lambda v: v)

register_unary("-", "duration", "duration", negate_duration)
register_unary("+", "duration", "duration", lambda v: v)

for _unit_category in ("currency", "tonnage", "percent"):
    register_unary(
        "-",
        _unit_category,
        _unit_category,
        lambda q: Quantity(-q.value, q.unit),
    )
    register_unary("+", _unit_category, _unit_category, lambda q: q)
# ------------------------------------------------------------------
# Function registry
#
# Each function declares its arity, whether it evaluates its own
# arguments (lazy), its static result type, and its implementation.
# UDFs later become entries added to a copy of this registry.
# ------------------------------------------------------------------


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

    if category in NUMERIC_CATEGORIES | {
        "duration",
        "currency",
        "tonnage",
        "percent",
    }:
        return category

    _fail(node, "abs() accepts a number, duration, or quantity.")


def _abs_impl(values):
    value = values[0]

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


def _round_impl(values):
    if len(values) == 1:
        return round(values[0])

    digits = values[1]

    if abs(digits) > 100:
        raise ExpressionError("The number of decimal places is too large.")

    return round(values[0], digits)


# ---- min / max ---------------------------------------------------

_ORDERABLE = {"date", "datetime", "time", "currency", "tonnage", "percent"}


def _min_max_result(name):
    def result_type(categories, node):
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

_SUMMABLE = {"currency", "tonnage", "percent", "duration"}


def _sum_result(categories, node):
    if all(category in NUMERIC_CATEGORIES for category in categories):
        return "int" if all(category == "int" for category in categories) else "decimal"

    first = categories[0]

    if first in _SUMMABLE and all(category == first for category in categories):
        return first

    _fail(
        node,
        "sum() arguments must all be numbers, or all the same "
        "quantity or duration type.",
    )


def _sum_impl(values):
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

    return sum(values)


def _avg_result(categories, node):
    if all(category in NUMERIC_CATEGORIES for category in categories):
        return "decimal"

    first = categories[0]

    if first in {"currency", "tonnage", "percent"} and all(
        category == first for category in categories
    ):
        return first

    _fail(
        node,
        "avg() arguments must all be numbers, or all the same quantity type.",
    )


def _avg_impl(values):
    count = len(values)
    first = values[0]

    if isinstance(first, Quantity):
        total = sum((value.value for value in values), Decimal(0))
        return Quantity(total / count, first.unit)

    total = sum((to_decimal(value) for value in values), Decimal(0))
    return total / count


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


# coalesce() is deliberately absent: it needs a blank/null value in
# the type system first. When that lands, it becomes another lazy
# FunctionSpec, exactly like if().

FUNCTIONS = {
    "today": FunctionSpec(
        "today", 0, 0, False, _fixed("date"), lambda values: date.today()
    ),
    "now": FunctionSpec(
        "now",
        0,
        0,
        False,
        _fixed("datetime"),
        lambda values: datetime.now().replace(microsecond=0),
    ),
    "time": FunctionSpec("time", 2, 3, False, _time_result, _time_impl),
    "abs": FunctionSpec("abs", 1, 1, False, _abs_result, _abs_impl),
    "round": FunctionSpec("round", 1, 2, False, _round_result, _round_impl),
    "min": FunctionSpec(
        "min", 1, None, False, _min_max_result("min"), _min_max_impl(min)
    ),
    "max": FunctionSpec(
        "max", 1, None, False, _min_max_result("max"), _min_max_impl(max)
    ),
    "sum": FunctionSpec("sum", 1, None, False, _sum_result, _sum_impl),
    "avg": FunctionSpec("avg", 1, None, False, _avg_result, _avg_impl),
    "if": FunctionSpec("if", 3, 3, True, _if_result, _if_impl),
    "days_between": FunctionSpec(
        "days_between",
        2,
        2,
        False,
        _days_between_result,
        lambda values: (values[1] - values[0]).days,
    ),
}
# ------------------------------------------------------------------
# Type checker and evaluator
#
# Two separate walks over the same AST. The checker guarantees the
# evaluator only ever sees well-typed operations, so type errors
# surface before any side effects or partial evaluation.
# ------------------------------------------------------------------


@dataclass
class Environment:
    variables: dict
    functions: dict


def _arity_text(spec) -> str:
    if spec.max_args is None:
        suffix = "s" if spec.min_args != 1 else ""
        return f"at least {spec.min_args} argument{suffix}"

    if spec.min_args == spec.max_args:
        suffix = "s" if spec.min_args != 1 else ""
        return f"exactly {spec.min_args} argument{suffix}"

    return f"between {spec.min_args} and {spec.max_args} arguments"


def check_types(node, variable_types: dict, functions: dict) -> str:
    """Return the expression's category or raise, without evaluating."""

    if isinstance(node, Literal):
        return category_of(node.value)

    if isinstance(node, Var):
        if node.name not in variable_types:
            raise ExpressionError(
                f"Unknown variable {node.name!r}.",
                node.position,
            )

        return variable_types[node.name]

    if isinstance(node, UnaryOp):
        operand = check_types(node.operand, variable_types, functions)
        rule = UNARY_RULES.get((node.op, operand))

        if rule is None:
            raise ExpressionError(
                f"Unary {node.op!r} cannot be applied to {label(operand)}.",
                node.position,
            )

        return rule[0]

    if isinstance(node, BinOp):
        left = check_types(node.left, variable_types, functions)
        right = check_types(node.right, variable_types, functions)
        rule = BINARY_RULES.get((node.op, left, right))

        if rule is None:
            raise ExpressionError(
                f"{node.op!r} is not defined for {label(left)} and {label(right)}.",
                node.position,
            )

        result = rule[0]
        return result if isinstance(result, str) else result(left, right)

    if isinstance(node, Call):
        spec = functions.get(node.name)

        if spec is None:
            raise ExpressionError(
                f"Unknown function: {node.name}().",
                node.position,
            )

        count = len(node.args)

        if count < spec.min_args or (
            spec.max_args is not None and count > spec.max_args
        ):
            raise ExpressionError(
                f"{spec.name}() requires {_arity_text(spec)}.",
                node.position,
            )

        categories = [
            check_types(argument, variable_types, functions) for argument in node.args
        ]

        return spec.result_type(categories, node)

    raise ExpressionError("Unsupported expression node.")


def evaluate_node(node, environment: Environment):
    try:
        if isinstance(node, Literal):
            return node.value

        if isinstance(node, Var):
            # The checker already verified the name exists.
            return environment.variables[node.name]

        if isinstance(node, UnaryOp):
            operand = evaluate_node(node.operand, environment)
            category = category_of(operand)
            _, impl = UNARY_RULES[(node.op, category)]
            return impl(operand)

        if isinstance(node, BinOp):
            left = evaluate_node(node.left, environment)
            right = evaluate_node(node.right, environment)
            key = (node.op, category_of(left), category_of(right))
            _, impl = BINARY_RULES[key]
            return impl(left, right)

        if isinstance(node, Call):
            spec = environment.functions[node.name]

            if spec.lazy:
                return spec.impl(node.args, environment, evaluate_node)

            values = [evaluate_node(argument, environment) for argument in node.args]

            return spec.impl(values)

        raise ExpressionError("Unsupported expression node.")
    except ExpressionError as error:
        # Runtime errors (divide by zero, calendar overflow, ...)
        # inherit the position of the nearest enclosing node.
        if error.position is None:
            error.position = node.position

        raise


@dataclass(frozen=True)
class EvaluationResult:
    value: object
    category: str
    variables: tuple[str, ...]


def evaluate_expression(
    expression: str,
    variables: dict | None = None,
    functions: dict | None = None,
) -> EvaluationResult:
    """tokenize -> parse -> type check -> evaluate."""

    if not isinstance(expression, str):
        raise TypeError("The expression must be text.")

    if len(expression) > 500:
        raise ExpressionError("The expression is too long.")

    variables = {} if variables is None else variables
    functions = FUNCTIONS if functions is None else functions

    try:
        node = parse(expression)

        variable_types = {name: category_of(value) for name, value in variables.items()}

        category = check_types(node, variable_types, functions)

        value = evaluate_node(
            node,
            Environment(variables=variables, functions=functions),
        )
    except RecursionError as error:
        raise ExpressionError("The expression is nested too deeply.") from error

    return EvaluationResult(
        value=value,
        category=category,
        variables=variables_in(node),
    )


# ------------------------------------------------------------------
# Formatting
# ------------------------------------------------------------------


def format_duration(value: Duration) -> str:
    components = []

    months = value.months
    days = value.days
    seconds = value.seconds

    years, remaining_months = divmod(abs(months), 12)

    hours, seconds_remainder = divmod(abs(seconds), 3600)
    minutes, remaining_seconds = divmod(seconds_remainder, 60)

    if months < 0:
        years = -years
        remaining_months = -remaining_months

    if seconds < 0:
        hours = -hours
        minutes = -minutes
        remaining_seconds = -remaining_seconds

    if years:
        components.append(f"{years}y")

    if remaining_months:
        components.append(f"{remaining_months}mo")

    if days:
        components.append(f"{days}d")

    if hours:
        components.append(f"{hours}h")

    if minutes:
        components.append(f"{minutes}min")

    if remaining_seconds:
        components.append(f"{remaining_seconds}s")

    return " ".join(components) or "0s"


def format_decimal(value: Decimal) -> str:
    text = f"{value:,f}"

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text or "0"


def format_result(value) -> str:
    # bool is a subclass of int: check it first.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, Quantity):
        if value.unit is Unit.CURRENCY:
            text = f"${abs(value.value):,.2f}"
            return f"-{text}" if value.value < 0 else text

        if value.unit is Unit.PERCENT:
            return f"{format_decimal(value.value * 100)}%"

        return f"{format_decimal(value.value)} t"

    if is_datetime(value):
        return value.isoformat(sep=" ", timespec="seconds")

    if is_date_only(value):
        return value.isoformat()

    if is_time_only(value):
        if value.second:
            return value.isoformat(timespec="seconds")

        return value.isoformat(timespec="minutes")

    if isinstance(value, Duration):
        return format_duration(value)

    if isinstance(value, Decimal):
        return format_decimal(value)

    return f"{value:,}"


__all__ = [
    "Duration",
    "EvaluationResult",
    "ExpressionError",
    "FUNCTIONS",
    "Quantity",
    "Unit",
    "category_of",
    "evaluate_expression",
    "format_result",
    "parse",
    "variables_in",
]
