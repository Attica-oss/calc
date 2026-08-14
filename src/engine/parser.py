"""AST and parser.

The parser only builds a tree; it never computes a value. That split
is what makes variables, lazy functions such as if(), type checking,
and dependency extraction possible.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from .lexer import tokenize
from .values import (
    INFINITY,
    Char,
    Complex,
    Duration,
    ExpressionError,
    Quantity,
    Unit,
    Value,
)


@dataclass(frozen=True)
class Literal:
    """A literal value baked directly into the AST at parse time (a
    number, string, date, duration, ...). See parse_primary().
    """

    value: Value
    position: int


@dataclass(frozen=True)
class Var:
    """A reference to a bound variable, resolved against Environment.variables."""

    name: str
    position: int


@dataclass(frozen=True)
class RowRef:
    """[name] — a reference to the current row's column value inside a
    table verb's row expression (filter/extend/sort). Deliberately its
    own node type, distinct from Var: ROADMAP.md calls for "lexical
    [column] row scope — no DAX-style implicit context, ever", so a
    row-scope column and a same-named outer variable never collide —
    see the row_scope threading in evaluator.py. Valid only inside a
    row_scope_arg-designated argument; check_types raises a clear
    error anywhere else.
    """

    name: str
    position: int


@dataclass(frozen=True)
class UnaryOp:
    """A prefix +/- applied to a single operand, e.g. -qty."""

    op: str
    operand: Node
    position: int


@dataclass(frozen=True)
class BinOp:
    """A binary operator applied to two operands, e.g. price * qty."""

    op: str
    left: Node
    right: Node
    position: int


@dataclass(frozen=True)
class Call:
    """A function call, e.g. sum(t::qty). name is already lowercased —
    function names, unlike variable names, are case-insensitive.
    """

    name: str
    args: tuple[Node, ...]
    position: int


@dataclass(frozen=True)
class Cast:
    """value::target — a type conversion or field extraction.

    target is a fixed, lowercased vocabulary (day, month, year, date,
    decimal, ...), not a general expression, so there's nothing to
    evaluate on the right side — it's closer to a keyword than an
    operand. See CAST_RULES (casts.py) for what's registered.
    """

    value: Node
    target: str
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

STRING_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
}


def parse_string_literal(text: str, position: int) -> str:
    """Unescape a STRING token's raw text (quotes included) into its value."""

    body = text[1:-1]
    characters = []
    index = 0

    while index < len(body):
        character = body[index]

        if character == "\\":
            if index + 1 >= len(body):
                raise ExpressionError("Trailing backslash in a string literal.", position)

            escape = body[index + 1]

            if escape not in STRING_ESCAPES:
                raise ExpressionError(
                    f"Unknown escape sequence '\\{escape}' in a string literal.",
                    position,
                )

            characters.append(STRING_ESCAPES[escape])
            index += 2
        else:
            characters.append(character)
            index += 1

    return "".join(characters)


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
            "Durations must currently use whole numbers, such as 30min, 2h, 3d, 4mo, or 1y.",
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


class Parser:
    """Recursive-descent parser with one method per precedence level,
    each falling through to the next tighter-binding level below it:

        comparison -> addition -> multiplication -> unary -> power
        -> cast -> primary

    primary() is the leaf: literals, variables, calls, `[column]`
    row references, and parenthesized subexpressions.
    """

    def __init__(self, expression: str):
        self.expression = expression
        self.tokens = tokenize(expression)
        self.index = 0
        self.depth = 0

    @property
    def current(self):
        """The token at the current read position, not yet consumed."""

        return self.tokens[self.index]

    def advance(self):
        """Consume and return the current token."""

        token = self.current
        self.index += 1
        return token

    def accept(self, kind: str):
        """Consume and return the current token if it matches `kind`, else None."""

        if self.current.kind == kind:
            return self.advance()

        return None

    def expect(self, kind: str, description: str):
        """Like accept(), but raise a clear ExpressionError on a mismatch."""

        token = self.accept(kind)

        if token is None:
            raise ExpressionError(
                f"Expected {description}.",
                self.current.position,
            )

        return token

    def parse(self):
        """Parse the whole expression; raise if input remains afterward."""

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
        left = self.parse_cast()

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

    def parse_cast(self):
        value = self.parse_primary()

        # A while-loop, not a single check, so x::datetime::date chains:
        # cast to datetime, then cast that result to date. Each cast
        # binds only to the value immediately to its left — (a + b)::date
        # needs the parens for the same reason 4i binds only to the 4
        # immediately before it, not to a whole preceding expression.
        while True:
            token = self.accept("DOUBLECOLON")

            if token is None:
                return value

            target_token = self.expect("IDENTIFIER", "a cast target after '::'")

            value = Cast(
                value=value,
                target=target_token.value.lower(),
                position=token.position,
            )

    def parse_primary(self):
        """The leaf of the grammar: a literal, variable, call, row
        reference, or a fully parenthesized subexpression.
        """

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

        if token.kind == "IMAGINARY":
            self.advance()

            return Literal(
                value=Complex(Decimal(0), Decimal(token.value[:-1])),
                position=token.position,
            )
        if token.kind == "INFINITY_SYMBOL":
            self.advance()

            return Literal(value=INFINITY, position=token.position)

        if token.kind == "HEX_CHAR":
            self.advance()

            codepoint = int(token.value, base=0)

            if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
                raise ExpressionError(
                    f"{token.value!r} is not a valid Unicode codepoint.",
                    token.position,
                )

            return Literal(value=Char(codepoint), position=token.position)

        if token.kind == "STRING":
            self.advance()

            return Literal(
                value=parse_string_literal(token.value, token.position),
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

        if token.kind == "LBRACKET":
            self.advance()
            name_token = self.expect("IDENTIFIER", "a column name inside '[...]'")
            self.expect("RBRACKET", "']'")

            return RowRef(name=name_token.value, position=token.position)

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
            "Expected a number, date, duration, string, variable, function, '[column]', or '('.",
            token.position,
        )

    def parse_call(self, name_token):
        """Parse a comma-separated argument list after an identifier
        already confirmed (by parse_primary) to be followed by '('.
        """

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
        elif isinstance(current, Cast):
            walk(current.value)

    walk(node)
    return tuple(sorted(names))


type Node = Literal | Var | RowRef | UnaryOp | BinOp | Call | Cast
