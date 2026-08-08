"""calc expression engine: tokenize -> parse -> type check -> evaluate.

Plain importable package — no marimo dependency. The notebook and the
CLI both import from here, so this package is the single source of
truth.

Split by responsibility:

- values: the value domain (ExpressionError, Unit, Duration, Quantity,
  Complex, Blank, category_of, ...) — no syntax, no operators.
- calendar_utils: calendar-aware date/datetime/time arithmetic.
- lexer: tokenizer (Token, tokenize).
- parser: AST node types and the recursive-descent Parser.
- operators: the BINARY_RULES/UNARY_RULES dispatch tables.
- casts: the CAST_RULES dispatch table for value::target.
- functions: the FUNCTIONS registry (abs, round, if, ...).
- evaluator: the type checker, evaluator, and evaluate_expression.
- formatting: rendering evaluated values back to display text.
"""

from .calendar_utils import (
    add_duration_to_date,
    add_duration_to_datetime,
    add_duration_to_time,
    add_months,
    time_to_seconds,
    timedelta_to_duration,
)
from .casts import CAST_RULES, register_cast, register_field
from .evaluator import (
    Environment,
    EvaluationResult,
    check_types,
    evaluate_expression,
    evaluate_node,
)
from .formatting import (
    format_column,
    format_complex,
    format_decimal,
    format_duration,
    format_result,
    format_table,
)
from .functions import FUNCTIONS, PI, E, FunctionSpec
from .lexer import Token, tokenize
from .operators import BINARY_RULES, UNARY_RULES, compare_key
from .parser import (
    BinOp,
    Call,
    Cast,
    Literal,
    Parser,
    UnaryOp,
    Var,
    parse,
    parse_duration_literal,
    variables_in,
)
from .values import (
    CATEGORY_LABELS,
    INFINITY,
    UNIT_QUANTA,
    Blank,
    Column,
    Complex,
    Duration,
    ExpressionError,
    Quantity,
    Table,
    Type,
    Unit,
    category_of,
    is_date_only,
    is_datetime,
    is_time_only,
    label,
    negate_duration,
    to_decimal,
)

__all__ = [
    "BINARY_RULES",
    "CAST_RULES",
    "CATEGORY_LABELS",
    "FUNCTIONS",
    "INFINITY",
    "PI",
    "UNARY_RULES",
    "UNIT_QUANTA",
    "BinOp",
    "Blank",
    "Call",
    "Cast",
    "Column",
    "Complex",
    "Duration",
    "E",
    "Environment",
    "EvaluationResult",
    "ExpressionError",
    "FunctionSpec",
    "Literal",
    "Parser",
    "Quantity",
    "Table",
    "Token",
    "Type",
    "UnaryOp",
    "Unit",
    "Var",
    "add_duration_to_date",
    "add_duration_to_datetime",
    "add_duration_to_time",
    "add_months",
    "category_of",
    "check_types",
    "compare_key",
    "evaluate_expression",
    "evaluate_node",
    "format_column",
    "format_complex",
    "format_decimal",
    "format_duration",
    "format_result",
    "format_table",
    "is_date_only",
    "is_datetime",
    "is_time_only",
    "label",
    "negate_duration",
    "parse",
    "parse_duration_literal",
    "register_cast",
    "register_field",
    "time_to_seconds",
    "timedelta_to_duration",
    "to_decimal",
    "tokenize",
    "variables_in",
]
