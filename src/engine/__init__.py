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
- type_runtime: first-class runtime Type values and type equality.
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
    evaluate_script,
)
from .formatting import (
    format_array,
    format_column,
    format_complex,
    format_decimal,
    format_duration,
    format_matrix,
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
    Let,
    Literal,
    Parser,
    RowRef,
    UnaryOp,
    Var,
    parse,
    parse_duration_literal,
    parse_script,
    variables_in,
)
from .values import (
    CATEGORY_LABELS,
    INFINITY,
    UNIT_QUANTA,
    Array,
    Blank,
    Char,
    Column,
    Complex,
    ContainerNumber,
    Duration,
    ExpressionError,
    Matrix,
    Number,
    Quantity,
    Table,
    Type,
    Unit,
    Value,
    category_of,
    is_date_only,
    is_datetime,
    is_time_only,
    label,
    negate_duration,
    to_decimal,
)
from .type_runtime import install_type_value_semantics as _install_type_value_semantics

_install_type_value_semantics()
del _install_type_value_semantics

__all__ = [
    "BINARY_RULES",
    "CAST_RULES",
    "CATEGORY_LABELS",
    "FUNCTIONS",
    "INFINITY",
    "PI",
    "UNARY_RULES",
    "UNIT_QUANTA",
    "Array",
    "BinOp",
    "Blank",
    "Call",
    "Cast",
    "Char",
    "Column",
    "Complex",
    "ContainerNumber",
    "Duration",
    "E",
    "Environment",
    "EvaluationResult",
    "ExpressionError",
    "FunctionSpec",
    "Let",
    "Literal",
    "Matrix",
    "Number",
    "Parser",
    "Quantity",
    "RowRef",
    "Table",
    "Token",
    "Type",
    "UnaryOp",
    "Unit",
    "Value",
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
    "evaluate_script",
    "format_array",
    "format_column",
    "format_complex",
    "format_decimal",
    "format_duration",
    "format_matrix",
    "format_result",
    "format_table",
    "is_date_only",
    "is_datetime",
    "is_time_only",
    "label",
    "negate_duration",
    "parse",
    "parse_duration_literal",
    "parse_script",
    "register_cast",
    "register_field",
    "time_to_seconds",
    "timedelta_to_duration",
    "to_decimal",
    "tokenize",
    "variables_in",
]
