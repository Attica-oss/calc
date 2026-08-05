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
    calc> -
    $1,080.00  (currency)
    calc> ans * 1.1
    $1,188.00  (currency)
    calc> let price = $12.50
    calc> let total = price * 3
    calc> total > $30
    TRUE  (boolean)
    calc> vars
    calc> reset
    calc> exit

Inside the REPL, ``let name = expression`` binds a variable. Plain ``=``
stays the language's equality operator, which is why assignment needs
the ``let`` keyword: ``qty = 3`` is a comparison, ``let qty = 3`` is a
binding. The result of the last plain expression is bound to ``ans``;
``-`` alone is shorthand for it. ``clear`` clears the screen, ``reset``
clears all bound variables.
"""

from __future__ import annotations

import re
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.theme import Theme

from src.engine_cli import (
    ExpressionError,
    category_of,
    evaluate_expression,
    format_result,
)

THEME = Theme(
    {
        "calc.prompt": "bold cyan",
        "calc.value": "bold green",
        "calc.currency": "bold #000080",
        "calc.tonnage": "bold #ff8700",
        "calc.category": "dim",
        "calc.variable": "yellow",
        "calc.error": "bold red",
        "calc.pointer": "red",
        "calc.source": "dim",
        "calc.info": "dim italic",
    }
)

console = Console(theme=THEME)
error_console = Console(theme=THEME, stderr=True)

PROMPT = "calc> "

LET_RE = re.compile(r"^\s*let\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")

# Per-category value colors; anything not listed here falls back to
# "calc.value" (bold green).
CATEGORY_STYLES = {
    "currency": "calc.currency",
    "tonnage": "calc.tonnage",
}

REPL_HELP = """\
Enter an expression to evaluate it. Commands:
  let NAME = EXPR   bind a variable (e.g.  let price = $12.50)
  -                 reuse the previous answer (shorthand for 'ans')
  vars              list current variables
  clear             clear the screen
  reset             clear all bound variables, including ans
  help              show this message
  exit | quit       leave (Ctrl-D also works)
Note: a plain '=' is the equality operator (qty = 3 is a comparison);
assignment always uses 'let'. The result of the last plain expression
is also available as the variable 'ans'."""


def value_style(category: str) -> str:
    return CATEGORY_STYLES.get(category, "calc.value")


def styled_value(text: str, category: str) -> str:
    style = value_style(category)
    return f"[{style}]{text}[/{style}]"


def print_error(expression: str, error: ExpressionError, out: Console) -> None:
    """Print an ExpressionError with the caret pointer when possible."""

    out.print(f"error: {error.message}", style="calc.error")

    if error.position is not None:
        out.print(f"    {expression}", style="calc.source")
        out.print("    " + " " * error.position + "^", style="calc.pointer")


def print_result(result, out: Console, *, bare: bool = False) -> None:
    if bare:
        out.print(format_result(result.value), style=value_style(result.category))
        return

    out.print(
        f"{styled_value(format_result(result.value), result.category)}"
        f"  [calc.category]({result.category})[/calc.category]"
    )


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
            raise typer.BadParameter(
                f"--var expects NAME=EXPRESSION, got {definition!r}"
            )

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise typer.BadParameter(f"{name!r} is not a valid variable name")

        try:
            result = evaluate_expression(expression.strip(), variables)
        except ExpressionError as error:
            error_console.print(f"error in --var {name}:", style="calc.error")
            print_error(expression.strip(), error, error_console)
            raise typer.Exit(1)

        variables[name] = result.value

    return variables


def run_once(expression: str, variables: dict, bare: bool) -> int:
    try:
        result = evaluate_expression(expression, variables)
    except ExpressionError as error:
        print_error(expression, error, error_console)
        return 1

    print_result(result, console, bare=bare)
    return 0


def run_repl(variables: dict) -> int:
    try:
        import readline  # noqa: F401  (line editing + history when available)
    except ImportError:
        pass

    console.print("calc — type 'help' for commands, 'exit' to leave.", style="calc.info")

    while True:
        try:
            line = console.input("[calc.prompt]calc>[/calc.prompt] ").strip()
        except EOFError:
            console.print()
            return 0
        except KeyboardInterrupt:
            console.print()
            continue

        if not line:
            continue

        if line in {"exit", "quit"}:
            return 0

        if line == "help":
            console.print(REPL_HELP)
            continue

        if line == "clear":
            console.clear()
            continue

        if line == "reset":
            variables.clear()
            console.print("All variables cleared.", style="calc.info")
            continue

        if line == "vars":
            if not variables:
                console.print(
                    "(no variables bound — use: let NAME = EXPR)", style="calc.info"
                )
            for name, value in sorted(variables.items()):
                console.print(
                    f"  [calc.variable]{name}[/calc.variable] = "
                    f"{styled_value(format_result(value), category_of(value))}"
                    f"  [calc.category]({category_of(value)})[/calc.category]"
                )
            continue

        # '-' alone reuses the last computed result, same as typing 'ans'.
        if line == "-":
            line = "ans"

        assignment = LET_RE.match(line)

        if assignment is not None:
            name, expression = assignment.groups()

            try:
                result = evaluate_expression(expression, variables)
            except ExpressionError as error:
                print_error(expression, error, console)
                continue

            variables[name] = result.value
            console.print(
                f"  [calc.variable]{name}[/calc.variable] = "
                f"{styled_value(format_result(result.value), result.category)}"
                f"  [calc.category]({result.category})[/calc.category]"
            )
            continue

        try:
            result = evaluate_expression(line, variables)
        except ExpressionError as error:
            print_error(line, error, console)
            continue

        variables["ans"] = result.value

        output = (
            f"{styled_value(format_result(result.value), result.category)}"
            f"  [calc.category]({result.category})[/calc.category]"
        )

        if result.variables:
            names = ", ".join(result.variables)
            output += f"  [calc.info][uses: {names}][/calc.info]"

        console.print(output)


app = typer.Typer(
    add_completion=False,
    rich_markup_mode="rich",
    help=(
        "Evaluate strongly typed spreadsheet-style expressions "
        "(numbers, dates, times, durations, currency, tonnage, "
        "percentages).\n\n"
        "With no EXPRESSION, an interactive REPL starts. Quote "
        "expressions in the shell: symbols like $, *, and parentheses "
        "are shell metacharacters."
    ),
)


@app.command()
def main(
    expression: Annotated[
        str | None,
        typer.Argument(help="expression to evaluate once (omit to start the REPL)"),
    ] = None,
    var: Annotated[
        list[str] | None,
        typer.Option(
            "--var",
            metavar="NAME=EXPR",
            help=(
                "bind a variable before evaluating; the value is itself an "
                "expression (repeatable, later ones may use earlier ones)"
            ),
        ),
    ] = None,
    bare: Annotated[
        bool,
        typer.Option("--bare", help="print only the formatted value (no type), for scripting"),
    ] = False,
) -> None:
    variables = bind_variables(var or [])

    if expression is not None:
        raise typer.Exit(run_once(expression, variables, bare))

    raise typer.Exit(run_repl(variables))


KNOWN_FLAGS = {"--bare", "--help"}
KNOWN_VALUE_OPTIONS = {"--var"}


def _looks_like_known_option(token: str) -> bool:
    name = token.split("=", 1)[0]
    return name in KNOWN_FLAGS or name in KNOWN_VALUE_OPTIONS


def normalize_argv(argv: list[str]) -> list[str]:
    """Let an expression starting with '-' (e.g. "-5 + 3") be typed as
    a normal argument.

    Click otherwise mistakes a leading '-' for an unknown option
    (``No such option: -5``) since it can't tell an expression from a
    flag. The fix is the POSIX '--' end-of-options marker; this
    inserts it automatically in front of the first token that isn't
    one of calc's own options, so users don't have to know about it.
    """

    normalized: list[str] = []

    for index, token in enumerate(argv):
        if normalized and normalized[-1] == "--var":
            normalized.append(token)
            continue

        if token == "--":
            normalized.extend(argv[index:])
            return normalized

        if _looks_like_known_option(token):
            normalized.append(token)
            continue

        if not token.startswith("-") or token == "-":
            normalized.append(token)
            continue

        normalized.append("--")
        normalized.extend(argv[index:])
        return normalized

    return normalized


if __name__ == "__main__":
    app(args=normalize_argv(sys.argv[1:]))
