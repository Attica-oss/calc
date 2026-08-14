"""Tokenizer: raw source text -> a flat list of tokens."""

import re
from dataclasses import dataclass

from .values import ExpressionError


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
    \d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])
       \x20
       (?:[01]\d|2[0-3]):[0-5]\d
       (?::[0-5]\d)?
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
    (?P<IMAGINARY>
        # A number immediately followed by 'i' is a pure-imaginary
        # literal (3 + 4i). A bare 'i' with no glued digits stays a
        # perfectly ordinary variable name, same as bare 't'.
        (?:\d+(?:\.\d*)?|\.\d+)
        i
    )
    |
    (?P<INFINITY_SYMBOL>∞)
    |
    (?P<STRING>
        "
        (?:[^"\\]|\\.)*
        "
    )
    |
    (?P<HEX_CHAR>
        0[xX][0-9A-Fa-f]+
    )
    |
    (?P<NUMBER>
        (?:\d+(?:\.\d*)?|\.\d+)
        (?:[eE][+-]?\d+)?
    )
    |
    (?P<POWER>\*\*)
    |
    (?P<DOUBLECOLON>::)
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
    (?P<LBRACKET>\[)
    |
    (?P<RBRACKET>\])
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
        assert kind is not None
        value = match.group()

        if kind != "SPACE":
            tokens.append(Token(kind=kind, value=value, position=position))

        position = match.end()

    tokens.append(Token("EOF", "", len(expression)))
    return tokens
