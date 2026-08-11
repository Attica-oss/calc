"""Type checker and evaluator.

Two separate walks over the same AST. The checker guarantees the
evaluator only ever sees well-typed operations, so type errors
surface before any side effects or partial evaluation.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .casts import CAST_RULES
from .functions import FUNCTIONS
from .operators import BINARY_RULES, UNARY_RULES
from .parser import (
    BinOp,
    Call,
    Cast,
    Literal,
    RowRef,
    UnaryOp,
    Var,
    parse,
    variables_in,
)
from .values import Column, ExpressionError, Table, Type, category_of, label


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


def check_types(node, variable_types: dict, functions: dict, row_scope: dict | None = None) -> str:
    """Return the expression's category or raise, without evaluating.

    row_scope, when set, is the current table verb's row scope — a
    dict of lowercased column name -> Type, used only to resolve
    RowRef ([colname]) nodes. It propagates unchanged through every
    ordinary recursive call, so [colname] still resolves correctly
    nested arbitrarily deep inside a row expression; only the Call
    branch below ever replaces it, and only for the one argument a
    row-scoped FunctionSpec (filter/extend/sort) designates.
    """

    if isinstance(node, Literal):
        return category_of(node.value)

    if isinstance(node, Var):
        if node.name not in variable_types:
            raise ExpressionError(
                f"Unknown variable {node.name!r}.",
                node.position,
            )

        return variable_types[node.name]

    if isinstance(node, RowRef):
        if row_scope is None:
            raise ExpressionError(
                f"[{node.name}] can only be used inside filter()/extend()/"
                "sort()'s row expression.",
                node.position,
            )

        key = node.name.lower()

        if key not in row_scope:
            raise ExpressionError(
                f"Unknown column {node.name!r} in this row scope.",
                node.position,
            )

        return row_scope[key]

    if isinstance(node, UnaryOp):
        operand = check_types(node.operand, variable_types, functions, row_scope)
        rule = UNARY_RULES.get((node.op, operand))

        if rule is None:
            raise ExpressionError(
                f"Unary {node.op!r} cannot be applied to {label(operand)}.",
                node.position,
            )

        return rule[0]

    if isinstance(node, BinOp):
        left = check_types(node.left, variable_types, functions, row_scope)
        right = check_types(node.right, variable_types, functions, row_scope)
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

        if spec.row_scope_arg is None:
            categories = [
                check_types(argument, variable_types, functions, row_scope)
                for argument in node.args
            ]
        else:
            # A row-scoped verb (filter/extend/sort): every argument
            # checks under the *ambient* row_scope as usual, except
            # the one designated argument, which checks under a new
            # row scope derived from the table argument's own schema
            # (always argument 0, already checked by the time we
            # reach the designated index since args are processed in
            # order).
            categories = []

            for index, argument in enumerate(node.args):
                if index != spec.row_scope_arg:
                    categories.append(
                        check_types(argument, variable_types, functions, row_scope)
                    )
                    continue

                table_category = categories[0]

                if not (
                    isinstance(table_category, Type)
                    and table_category.fields
                    and str.__eq__(table_category, "table")
                ):
                    raise ExpressionError(
                        f"{spec.name}()'s first argument must be a table.",
                        node.position,
                    )

                inner_scope = {
                    name.lower(): field_type for name, field_type in table_category.fields
                }
                categories.append(
                    check_types(argument, variable_types, functions, inner_scope)
                )

        return spec.result_type(categories, node)

    if isinstance(node, Cast):
        source = check_types(node.value, variable_types, functions, row_scope)

        # Table field access (t::colname) isn't a fixed-vocabulary cast
        # — the "target" is one of the table's own column names, known
        # only from this particular table's schema — so it's resolved
        # dynamically here, ahead of the static CAST_RULES dict, same
        # spirit as the dispatch tables but keyed by this one Type's
        # fields instead of a module-load-time registration.
        if isinstance(source, Type) and source.fields and str.__eq__(source, "table"):
            field = next(
                ((name, field_type) for name, field_type in source.fields if name.lower() == node.target),
                None,
            )

            if field is not None:
                name, field_type = field
                return Type("column", fields=((name, field_type),))

        rule = CAST_RULES.get((source, node.target))

        if rule is None:
            raise ExpressionError(
                f"Cannot cast {label(source)} to {node.target}.",
                node.position,
            )

        return rule[0]

    raise ExpressionError("Unsupported expression node.")


def evaluate_node(node, environment: Environment, row_scope: dict | None = None):
    try:
        if isinstance(node, Literal):
            return node.value

        if isinstance(node, Var):
            # The checker already verified the name exists.
            return environment.variables[node.name]

        if isinstance(node, RowRef):
            # The checker already verified this resolves.
            return row_scope[node.name.lower()]

        if isinstance(node, UnaryOp):
            operand = evaluate_node(node.operand, environment, row_scope)
            category = category_of(operand)
            _, impl = UNARY_RULES[(node.op, category)]
            return impl(operand)

        if isinstance(node, BinOp):
            left = evaluate_node(node.left, environment, row_scope)
            right = evaluate_node(node.right, environment, row_scope)
            key = (node.op, category_of(left), category_of(right))
            _, impl = BINARY_RULES[key]
            return impl(left, right)

        if isinstance(node, Call):
            spec = environment.functions[node.name]

            if spec.lazy:
                return spec.impl(node.args, environment, evaluate_node, row_scope)

            values = [
                evaluate_node(argument, environment, row_scope) for argument in node.args
            ]

            return spec.impl(values)

        if isinstance(node, Cast):
            value = evaluate_node(node.value, environment, row_scope)

            if isinstance(value, Table):
                names = [name.lower() for name, _ in value.schema]

                if node.target in names:
                    index = names.index(node.target)
                    name, field_type = value.schema[index]
                    return Column(name=name, values=value.columns[index], element_type=field_type)

            _, impl = CAST_RULES[(category_of(value), node.target)]
            return impl(value)

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
    variables: Mapping[str, Any] | None = None,
    functions: Mapping[str, Any] | None = None,
) -> EvaluationResult:
    """tokenize -> parse -> type check -> evaluate."""

    if not isinstance(expression, str):
        raise TypeError("The expression must be text.")

    if len(expression) > 500:
        raise ExpressionError("The expression is too long.")

    variables = {} if variables is None else dict(variables)
    functions = FUNCTIONS if functions is None else dict(functions)

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
