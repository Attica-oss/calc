"""Tokenizer: raw source text -> a flat list of tokens."""

import re
from dataclasses import dataclass
from re import Pattern

from .values import ExpressionError


@dataclass(frozen=True)
class Token:
    """One lexeme: its grammar kind (a TOKEN_RE group name, e.g.
    "NUMBER" or "PLUS"), raw text, and zero-based source position
    (used to point the parser's error caret at the right character).
    """

    kind: str
    value: str
    position: int


# One named group per token kind, tried in order — order matters
# whenever one pattern is a prefix of another (e.g. DATETIME before
# DATE, POWER "**" before MULTIPLY "*", LE "<=" before LT "<"), so a
# longer/more specific alternative always gets first refusal.
TOKEN_RE: Pattern[str] = re.compile(
    r"""
    (?P<SPACE>\s+)
    |
    (?P<COMMENT>^[ \t]*//[^\r\n]*)
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
    (?P<POWER>\^ )
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
    (?P<SEMICOLON>;)
    |
    (?P<IDENTIFIER>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE | re.MULTILINE,
)


def tokenize(expression: str) -> list[Token]:
    """Split `expression` into a list of Tokens terminated by an EOF
    token, matching TOKEN_RE repeatedly from left to right. Whitespace
    is consumed but not emitted as a token. Raises ExpressionError on
    the first character that matches no alternative.
    """

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

        kind: str | None = match.lastgroup
        assert kind is not None
        value: str = match.group()

        if kind not in ("SPACE", "COMMENT"):
            tokens.append(Token(kind=kind, value=value, position=position))

        position = match.end()

    tokens.append(Token("EOF", "", len(expression)))
    return tokens
