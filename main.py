"""Command-line interface for the calc expression engine.

Usage
-----
One-shot evaluation:

    python main.py "1 + 2"
    python main.py "$5.2 * 1.5%"
    python main.py --var price='$12.50' --var qty=3 "price * qty"
    python main.py --bare "$10 / 4"          # value only, pipe-friendly

Interactive REPL (no expression argument):

    python main.py
    calc> $450 * 2.4t
    $1,080.00  (currency)
    calc> let price = $12.50
    calc> let total = price * 3
    calc> total > $30
    TRUE  (boolean)
    calc> vars
    calc> exit

Inside the REPL, ``let name = expression`` binds a variable. Plain ``=``
stays the language's equality operator, which is why assignment needs
the ``let`` keyword: ``qty = 3`` is a comparison, ``let qty = 3`` is a
binding.
"""

from __future__ import annotations

import argparse
import re
import sys

from src.engine_cli import (
    ExpressionError,
    category_of,
    evaluate_expression,
    format_result,
)

PROMPT = "calc> "

LET_RE = re.compile(r"^\s*let\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")

REPL_HELP = """\
Enter an expression to evaluate it. Commands:
  let NAME = EXPR   bind a variable (e.g.  let price = $12.50)
  vars              list current variables
  help              show this message
  exit | quit       leave (Ctrl-D also works)
Note: a plain '=' is the equality operator (qty = 3 is a comparison);
assignment always uses 'let'."""


def render_error(expression: str, error: ExpressionError) -> str:
    """Format an ExpressionError with the caret pointer when possible."""

    lines = [f"error: {error.message}"]

    if error.position is not None:
        lines.append(f"    {expression}")
        lines.append("    " + " " * error.position + "^")

    return "\n".join(lines)


def bind_variables(definitions: list[str]) -> dict:
    """Evaluate --var NAME=EXPR definitions, left to right.

    Each value is itself an expression evaluated by the engine, so
    types come for free ($12.50 is currency, 2.4t is tonnage) and
    later definitions can reference earlier ones.
    """

    variables: dict = {}

    for definition in definitions:
        name, separator, expression = definition.partition("=")
        name = name.strip()

        if not separator or not expression.strip():
            raise SystemExit(
                f"error: --var expects NAME=EXPRESSION, got {definition!r}"
            )

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise SystemExit(f"error: {name!r} is not a valid variable name")

        try:
            result = evaluate_expression(expression.strip(), variables)
        except ExpressionError as error:
            raise SystemExit(
                f"error in --var {name}:\n"
                f"{render_error(expression.strip(), error)}"
            )

        variables[name] = result.value

    return variables


def run_once(expression: str, variables: dict, bare: bool) -> int:
    try:
        result = evaluate_expression(expression, variables)
    except ExpressionError as error:
        print(render_error(expression, error), file=sys.stderr)
        return 1

    if bare:
        print(format_result(result.value))
    else:
        print(f"{format_result(result.value)}  ({result.category})")

    return 0


def run_repl(variables: dict) -> int:
    try:
        import readline  # noqa: F401  (line editing + history when available)
    except ImportError:
        pass

    print("calc — type 'help' for commands, 'exit' to leave.")

    while True:
        try:
            line = input(PROMPT).strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue

        if not line:
            continue

        if line in {"exit", "quit"}:
            return 0

        if line == "help":
            print(REPL_HELP)
            continue

        if line == "vars":
            if not variables:
                print("(no variables bound — use: let NAME = EXPR)")
            for name, value in sorted(variables.items()):
                print(
                    f"  {name} = {format_result(value)}"
                    f"  ({category_of(value)})"
                )
            continue

        assignment = LET_RE.match(line)

        if assignment is not None:
            name, expression = assignment.groups()

            try:
                result = evaluate_expression(expression, variables)
            except ExpressionError as error:
                print(render_error(expression, error))
                continue

            variables[name] = result.value
            print(
                f"  {name} = {format_result(result.value)}"
                f"  ({result.category})"
            )
            continue

        try:
            result = evaluate_expression(line, variables)
        except ExpressionError as error:
            print(render_error(line, error))
            continue

        output = f"{format_result(result.value)}  ({result.category})"

        if result.variables:
            output += f"  [uses: {', '.join(result.variables)}]"

        print(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calc",
        description=(
            "Evaluate strongly typed spreadsheet-style expressions "
            "(numbers, dates, times, durations, currency, tonnage, "
            "percentages)."
        ),
        epilog=(
            "With no EXPRESSION, an interactive REPL starts. "
            "Quote expressions in the shell: symbols like $, *, and "
            "parentheses are shell metacharacters."
        ),
    )
    parser.add_argument(
        "expression",
        nargs="?",
        help="expression to evaluate once (omit to start the REPL)",
    )
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="NAME=EXPR",
        help=(
            "bind a variable before evaluating; the value is itself an "
            "expression (repeatable, later ones may use earlier ones)"
        ),
    )
    parser.add_argument(
        "--bare",
        action="store_true",
        help="print only the formatted value (no type), for scripting",
    )

    arguments = parser.parse_args(argv)
    variables = bind_variables(arguments.var)

    if arguments.expression is not None:
        return run_once(arguments.expression, variables, arguments.bare)

    return run_repl(variables)


if __name__ == "__main__":
    sys.exit(main())
