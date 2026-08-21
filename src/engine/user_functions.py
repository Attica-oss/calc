"""Named, statically typed user-defined functions for Calc scripts.

First version intentionally keeps functions out of the ordinary value domain:

    fn add(x: int, y: int) -> int = x + y;
    add(2, 5)

A function declaration extends the script's function registry with a normal
FunctionSpec, so calls continue to use the same checker/evaluator path as
built-ins. Function values, closures, recursion, multiline bodies, and
higher-order functions are deliberately deferred.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import lexer as lexer_module
from . import parser as parser_module
from .evaluator import Environment, EvaluationResult, check_types, evaluate_node
from .functions import FUNCTIONS, FunctionSpec
from .lexer import Token
from .values import Blank, ExpressionError, Type, Value, category_of


@dataclass(frozen=True)
class FnParameter:
    """One typed parameter in a named function declaration."""

    name: str
    annotation: Type
    position: int


@dataclass(frozen=True)
class FnDef:
    """A script-level named function declaration with one expression body."""

    name: str
    parameters: tuple[FnParameter, ...]
    return_type: Type
    body: Any
    position: int


# These are concrete runtime categories. Structured types need richer syntax
# (e.g. array{int}, table{...}) and are intentionally left for a later phase.
_ANNOTATABLE_TYPES = frozenset(
    {
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
        "text",
        "char",
        "container",
        "type",
    }
)


def tokenize(expression: str) -> list[Token]:
    """Tokenize Calc source, adding the ``:`` and ``->`` tokens used by fn.

    The core TOKEN_RE remains the source of truth for every existing token.
    We intercept only the two new pieces of punctuation before delegating the
    rest of the input to that regex.
    """

    tokens: list[Token] = []
    position = 0

    while position < len(expression):
        if expression.startswith("->", position):
            tokens.append(Token("ARROW", "->", position))
            position += 2
            continue

        if expression[position] == ":" and not expression.startswith("::", position):
            tokens.append(Token("COLON", ":", position))
            position += 1
            continue

        match = lexer_module.TOKEN_RE.match(expression, position)

        if match is None:
            character = expression[position]
            raise ExpressionError(
                f"Unexpected character {character!r}.",
                position,
            )

        kind = match.lastgroup
        assert kind is not None
        value = match.group()

        if kind not in ("SPACE", "COMMENT"):
            tokens.append(Token(kind=kind, value=value, position=position))

        position = match.end()

    tokens.append(Token("EOF", "", len(expression)))
    return tokens


class FnParser(parser_module.Parser):
    """The normal expression parser plus script-level ``fn`` declarations."""

    def __init__(self, expression: str):
        self.expression = expression
        self.tokens = tokenize(expression)
        self.index = 0
        self.depth = 0

    def _starts_function_definition(self) -> bool:
        return (
            self.current.kind == "IDENTIFIER"
            and self.current.value == "fn"
            and self.index + 2 < len(self.tokens)
            and self.tokens[self.index + 1].kind == "IDENTIFIER"
            and self.tokens[self.index + 2].kind == "LPAREN"
        )

    def parse_statement(self):
        if self._starts_function_definition():
            return self.parse_function_definition()

        return super().parse_statement()

    def parse_function_definition(self) -> FnDef:
        fn_token = self.advance()  # fn
        name_token = self.expect("IDENTIFIER", "a function name after 'fn'")

        if name_token.value != name_token.value.lower():
            raise ExpressionError(
                "Function names must be lowercase: "
                f"use {name_token.value.lower()!r} instead of {name_token.value!r}.",
                name_token.position,
            )

        self.expect("LPAREN", "'(' after the function name")

        parameters: list[FnParameter] = []
        seen_names: set[str] = set()

        if self.current.kind != "RPAREN":
            while True:
                parameter_token = self.expect("IDENTIFIER", "a parameter name")

                if parameter_token.value in seen_names:
                    raise ExpressionError(
                        f"Duplicate parameter name {parameter_token.value!r}.",
                        parameter_token.position,
                    )

                seen_names.add(parameter_token.value)
                self.expect("COLON", "':' after the parameter name")
                type_token = self.expect("IDENTIFIER", "a parameter type after ':'")

                parameters.append(
                    FnParameter(
                        name=parameter_token.value,
                        annotation=Type(type_token.value.lower()),
                        position=parameter_token.position,
                    )
                )

                if not self.accept("COMMA"):
                    break

        self.expect("RPAREN", "')' after the parameters")
        self.expect("ARROW", "'->' before the return type")
        return_token = self.expect("IDENTIFIER", "a return type after '->'")
        self.expect("EQ", "'=' before the function body")

        return FnDef(
            name=name_token.value,
            parameters=tuple(parameters),
            return_type=Type(return_token.value.lower()),
            body=self.parse_comparison(),
            position=fn_token.position,
        )


def parse_script(expression: str):
    """Parse a script containing let statements, fn declarations, or expressions."""

    return FnParser(expression).parse_script()


def _validate_annotation(annotation: Type, *, what: str, position: int) -> None:
    name = str(annotation)

    if annotation.fields is not None or name not in _ANNOTATABLE_TYPES:
        raise ExpressionError(
            f"Unknown or unsupported {what} type {name!r}.",
            position,
        )


def _calls_function(node: Any, name: str) -> bool:
    """Whether an expression directly contains a call to ``name``."""

    if isinstance(node, parser_module.Call):
        return node.name == name or any(_calls_function(arg, name) for arg in node.args)

    if isinstance(node, parser_module.UnaryOp):
        return _calls_function(node.operand, name)

    if isinstance(node, parser_module.BinOp):
        return _calls_function(node.left, name) or _calls_function(node.right, name)

    if isinstance(node, parser_module.Cast):
        return _calls_function(node.value, name)

    return False


def _build_function_spec(
    declaration: FnDef,
    functions: dict[str, FunctionSpec],
) -> FunctionSpec:
    if declaration.name in functions:
        raise ExpressionError(
            f"Function {declaration.name!r} already exists.",
            declaration.position,
        )

    for parameter in declaration.parameters:
        _validate_annotation(
            parameter.annotation,
            what=f"parameter {parameter.name!r}",
            position=parameter.position,
        )

    _validate_annotation(
        declaration.return_type,
        what="return",
        position=declaration.position,
    )

    if _calls_function(declaration.body, declaration.name):
        raise ExpressionError(
            "Recursive functions are not supported yet.",
            declaration.position,
        )

    parameter_types = {
        parameter.name: parameter.annotation for parameter in declaration.parameters
    }
    parameter_names = set(parameter_types)
    free_variables = set(parser_module.variables_in(declaration.body)) - parameter_names

    if free_variables:
        names = ", ".join(sorted(free_variables))
        raise ExpressionError(
            "Function bodies cannot capture outer variables yet; "
            f"found: {names}.",
            declaration.position,
        )

    body_type = check_types(
        declaration.body,
        parameter_types,
        functions,
    )

    if body_type != declaration.return_type:
        raise ExpressionError(
            f"{declaration.name}() declares return type {declaration.return_type}, "
            f"but its body returns {body_type}.",
            declaration.position,
        )

    parameters = declaration.parameters
    return_type = declaration.return_type
    body = declaration.body
    name = declaration.name

    def result_type(categories, node):
        for index, (parameter, actual) in enumerate(zip(parameters, categories), start=1):
            if actual != parameter.annotation:
                raise ExpressionError(
                    f"{name}() argument {index} ({parameter.name}) must be "
                    f"{parameter.annotation}, got {actual}.",
                    node.position,
                )

        return return_type

    def impl(values: Sequence[Value]) -> Value:
        local_variables = {
            parameter.name: value for parameter, value in zip(parameters, values)
        }
        return evaluate_node(
            body,
            Environment(
                variables=local_variables,
                functions=functions,
            ),
        )

    return FunctionSpec(
        name=name,
        min_args=len(parameters),
        max_args=len(parameters),
        lazy=False,
        result_type=result_type,
        impl=impl,
    )


def evaluate_script(
    expression: str,
    variables: Mapping[str, Any] | None = None,
    functions: Mapping[str, FunctionSpec] | None = None,
) -> EvaluationResult:
    """Evaluate a script with let statements and named typed functions."""

    if not isinstance(expression, str):
        raise TypeError("The expression must be text.")

    if len(expression) > 500:
        raise ExpressionError("The expression is too long.")

    variables = {} if variables is None else dict(variables)
    function_scope = dict(FUNCTIONS if functions is None else functions)

    variable_types = {
        name: category_of(value) for name, value in variables.items()
    }
    environment = Environment(
        variables=variables,
        functions=function_scope,
    )

    bound: dict[str, Any] = {}
    referenced: set[str] = set()
    last_assigned: str | None = None
    category: Any = "blank"
    value: Any = Blank()

    try:
        for statement in parse_script(expression):
            if isinstance(statement, FnDef):
                function_scope[statement.name] = _build_function_spec(
                    statement,
                    function_scope,
                )
                category = "blank"
                value = Blank()
                last_assigned = None
                continue

            if isinstance(statement, parser_module.Let):
                category = check_types(
                    statement.value,
                    variable_types,
                    function_scope,
                )
                value = evaluate_node(statement.value, environment)

                variable_types[statement.name] = category
                environment.variables[statement.name] = value
                bound[statement.name] = value
                last_assigned = statement.name

                referenced.update(
                    name
                    for name in parser_module.variables_in(statement.value)
                    if name not in bound
                )
                continue

            category = check_types(statement, variable_types, function_scope)
            value = evaluate_node(statement, environment)
            last_assigned = None

            referenced.update(
                name
                for name in parser_module.variables_in(statement)
                if name not in bound
            )
    except RecursionError as error:
        raise ExpressionError("The expression is nested too deeply.") from error

    return EvaluationResult(
        value=value,
        category=category,
        variables=tuple(sorted(referenced)),
        bindings=bound,
        assigned=last_assigned,
    )
