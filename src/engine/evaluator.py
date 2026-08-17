"""Type checker and evaluator.

Two separate walks over the same AST. The checker guarantees the
evaluator only ever sees well-typed operations, so type errors
surface before any side effects or partial evaluation.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .casts import CAST_RULES
from .functions import FUNCTIONS, FunctionSpec
from .operators import BINARY_RULES, UNARY_RULES
from .parser import (
    BinOp,
    Call,
    Cast,
    Let,
    Literal,
    RowRef,
    UnaryOp,
    Var,
    parse,
    parse_script,
    variables_in,
)
from .values import Column, ExpressionError, Table, Type, Value, category_of, label

# Scalar categories such as "int", "date", etc. are strings.
# Structured categories such as tables use Type.
type Category = str | Type


@dataclass
class Environment:
    """The runtime bindings evaluate_node() resolves Var/Call nodes
    against: bound variables and the (possibly UDF-extended) function
    registry.
    """

    variables: dict[str, Value]
    functions: Mapping[str, FunctionSpec]


def _table_fields(category: Type) -> tuple[tuple[str, Type], ...]:
    """A table Type's schema, narrowed from the general Type.fields
    shape (str | None names, for array/matrix's single unnamed field)
    to the guarantee that actually holds for "table": every column
    has a real name.
    """

    assert category.fields is not None
    return tuple((name, field_type) for name, field_type in category.fields if name is not None)


def _arity_text(spec: FunctionSpec) -> str:
    """Render a function's argument-count rule for an error message,
    e.g. "at least 1 argument" or "between 2 and 3 arguments".
    """

    if spec.max_args is None:
        suffix = "s" if spec.min_args != 1 else ""
        return f"at least {spec.min_args} argument{suffix}"

    if spec.min_args == spec.max_args:
        suffix = "s" if spec.min_args != 1 else ""
        return f"exactly {spec.min_args} argument{suffix}"

    return f"between {spec.min_args} and {spec.max_args} arguments"


def check_types(
    node: Node,
    variable_types: Mapping[str, Category],
    functions: Mapping[str, FunctionSpec],
    row_scope: Mapping[str, Category] | None = None,
) -> Category:
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
        operand: str = check_types(
            node=node.operand,
            variable_types=variable_types,
            functions=functions,
            row_scope=row_scope,
        )
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

        if count < spec.min_args or (spec.max_args is not None and count > spec.max_args):
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
                    categories.append(check_types(argument, variable_types, functions, row_scope))
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
                    name.lower(): field_type for name, field_type in _table_fields(table_category)
                }
                categories.append(check_types(argument, variable_types, functions, inner_scope))

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
                (
                    (name, field_type)
                    for name, field_type in _table_fields(source)
                    if name.lower() == node.target
                ),
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

        # Only enforced once the target is confirmed to be a real
        # built-in cast keyword (not a table's own column name, which
        # already returned above, and not a typo — that already
        # raised "Cannot cast" regardless of casing).
        if node.raw_target != node.raw_target.upper():
            raise ExpressionError(
                f"Cast targets must be uppercase: "
                f"use '::{node.raw_target.upper()}' instead of '::{node.raw_target}'.",
                node.position,
            )

        return rule[0]

    raise ExpressionError("Unsupported expression node.")


def evaluate_node(
    node: Node,
    environment: Environment,
    row_scope: dict[str, Value] | None = None,
) -> Value:
    """Evaluate a checked AST node to a runtime value.

    Assumes `node` already passed check_types() against a compatible
    variable_types/row_scope — lookups that the checker guarantees
    resolve (a known Var name, a RowRef inside its row scope, a
    registered operator/cast combination) are trusted here without
    re-validating, so this must never run on unchecked input.
    """

    try:
        if isinstance(node, Literal):
            return node.value

        if isinstance(node, Var):
            # The checker already verified the name exists.
            return environment.variables[node.name]

        if isinstance(node, RowRef):
            # The checker already verified this resolves, which also
            # guarantees row_scope isn't None here.
            assert row_scope is not None
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

            values = [evaluate_node(argument, environment, row_scope) for argument in node.args]

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
    """What evaluate_expression()/evaluate_script() hands back: the
    computed value, its static category (as a display string), and
    the variable names the expression referenced.

    value is typed Any rather than Value: callers branch on `category`
    (a runtime string) to know which concrete type to expect, the same
    dynamic-by-design pattern as e.g. json.loads()'s return type.

    bindings and assigned only carry information for evaluate_script():
    bindings holds every 'let NAME = expr' binding made anywhere in
    the script (so a caller with persistent state, like a REPL, can
    merge them into its own variable scope), and assigned is the name
    the *last* statement bound, when that statement was itself a let
    (so the caller can tell "the script's result is a fresh binding"
    apart from "the script's result is an ordinary value").
    """

    value: Any
    category: str
    variables: tuple[str, ...]
    bindings: Mapping[str, Any] = field(default_factory=dict)
    assigned: str | None = None


def evaluate_expression(
    expression: str,
    variables: Mapping[str, Any] | None = None,
    functions: Mapping[str, Any] | None = None,
) -> EvaluationResult:
    """tokenize -> parse -> type check -> evaluate.

    variables and functions default to an empty scope and the global
    FUNCTIONS registry respectively; pass functions to evaluate
    against a UDF-extended copy of the registry instead.
    """

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


def evaluate_script(
    expression: str,
    variables: Mapping[str, Any] | None = None,
    functions: Mapping[str, Any] | None = None,
) -> EvaluationResult:
    """tokenize -> parse -> type check -> evaluate a ';'-separated
    sequence of statements, e.g. "let x = 5; let y = x * 2; y".

    Each 'let NAME = expr' statement binds NAME for every statement
    after it, including later let statements — variable_types and
    environment.variables both grow as the script runs, so unlike
    evaluate_expression() this walk can't type-check the whole script
    up front; each statement is checked only once the ones before it
    have already been evaluated. The result is the last statement's
    value and category — for a script ending in a let, that's the
    value just bound (see EvaluationResult.assigned).

    variables/functions have the same meaning as in
    evaluate_expression(); pre-existing variables can be read but not
    reassigned by a let (shadowing isn't supported — see check_types's
    Var branch, which resolves a name to a single category).
    """

    if not isinstance(expression, str):
        raise TypeError("The expression must be text.")

    if len(expression) > 500:
        raise ExpressionError("The expression is too long.")

    variables = {} if variables is None else dict(variables)
    functions = FUNCTIONS if functions is None else dict(functions)

    variable_types: dict[str, Category] = {
        name: category_of(value) for name, value in variables.items()
    }
    environment = Environment(variables=variables, functions=functions)

    bound: dict[str, Any] = {}
    referenced: set[str] = set()
    last_assigned: str | None = None
    category: Category = "blank"
    value: Any = None

    try:
        for statement in parse_script(expression):
            if isinstance(statement, Let):
                category = check_types(statement.value, variable_types, functions)
                value = evaluate_node(statement.value, environment)

                variable_types[statement.name] = category
                environment.variables[statement.name] = value
                bound[statement.name] = value
                last_assigned = statement.name

                referenced.update(
                    name for name in variables_in(statement.value) if name not in bound
                )
                continue

            category = check_types(statement, variable_types, functions)
            value = evaluate_node(statement, environment)
            last_assigned = None

            referenced.update(name for name in variables_in(statement) if name not in bound)
    except RecursionError as error:
        raise ExpressionError("The expression is nested too deeply.") from error

    return EvaluationResult(
        value=value,
        category=category,
        variables=tuple(sorted(referenced)),
        bindings=bound,
        assigned=last_assigned,
    )


type Node = Literal | Var | RowRef | UnaryOp | BinOp | Call | Cast
