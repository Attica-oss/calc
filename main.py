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

from src.engine import (
    EvaluationResult,
    ExpressionError,
    Value,
    category_of,
    evaluate_expression,
    format_result,
)

THEME = Theme(
    {
        "calc.prompt": "bold cyan",
        "calc.value": "bold green",
        "calc.currency": "#2596be",
        "calc.tonnage": "bold #ff8700",
        "calc.boolean": "bold #86c204",
        "calc.category": "dim",
        "calc.variable": "yellow",
        "calc.error": "bold red",
        "calc.pointer": "red",
        "calc.source": "dim",
        "calc.info": "dim italic",
        "calc.command": "bold magenta",
        "calc.banner": "bold cyan",
    }
)

console = Console(theme=THEME)
error_console = Console(theme=THEME, stderr=True)

PROMPT = "calc> "

# figlet "ANSI Shadow" rendering of "calc"; each line is 33 columns
# wide, so run_repl() falls back to plain text below that width.
ASCII_ART = r"""
 ██████╗ █████╗ ██╗      ██████╗
██╔════╝██╔══██╗██║     ██╔════╝
██║     ███████║██║     ██║
██║     ██╔══██║██║     ██║
╚██████╗██║  ██║███████╗╚██████╗
 ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝
""".strip("\n")

ASCII_ART_WIDTH = max(len(line) for line in ASCII_ART.splitlines())

LET_RE = re.compile(r"^\s*let\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")

# Per-category value colors; anything not listed here falls back to
# "calc.value" (bold green).
CATEGORY_STYLES = {
    "currency": "calc.currency",
    "tonnage": "calc.tonnage",
    "boolean": "calc.boolean",
}

REPL_HELP = """\
Enter an expression to evaluate it. Commands:
  [calc.command]let[/calc.command] NAME = EXPR   bind a variable (e.g.  let price = $12.50)
  [calc.command]-[/calc.command]                 reuse the previous answer (shorthand for 'ans')
  [calc.command]vars[/calc.command]              list current variables
  [calc.command]clear[/calc.command]             clear the screen
  [calc.command]reset[/calc.command]             clear all bound variables, including ans
  [calc.command]help[/calc.command]              show this message
  [calc.command]exit[/calc.command] | [calc.command]quit[/calc.command]       leave (Ctrl-D also works)
Note: a plain '=' is the equality operator (qty = 3 is a comparison);
assignment always uses '[calc.command]let[/calc.command]'. The result of the last plain expression
is also available as the variable 'ans'."""


_ANSI_ESCAPE_RE = re.compile(r"(\x1b\[[0-9;]*m)")


def readline_safe_prompt(out: Console, markup: str) -> str:
    """Render rich markup to a prompt string GNU readline can use directly.

    readline sizes the prompt from whatever string it's handed, to
    know how many terminal columns to reserve when it redraws the
    line (backspace, arrow keys, history recall). Rich's ANSI color
    codes have zero visible width, but readline has no way to know
    that unless each escape sequence is wrapped in \\001/\\002
    ("start/end of non-printing characters") markers — without them,
    readline either counts the codes as visible columns, or (if the
    prompt is printed separately and a bare input() is used instead)
    doesn't know about the prompt's width at all. Either way, its
    internal cursor model drifts from the terminal's actual state and
    redraws corrupt the line.
    """

    with out.capture() as capture:
        out.print(markup, end="")

    return _ANSI_ESCAPE_RE.sub(r"\001\1\002", capture.get())


def clear_screen(out: Console) -> None:
    """Clear the visible screen and, on a real terminal, the scrollback
    buffer too.

    rich's Console.clear() only sends ED2 (``\\x1b[2J``), which wipes
    the visible screen but leaves scrollback intact — scrolling up
    still shows everything. The ``clear`` shell command additionally
    sends ED3 (``\\x1b[3J``), which is what users expect from 'clear'.
    """

    out.clear()

    if out.is_terminal:
        out.file.write("\x1b[3J")
        out.file.flush()


def value_style(category: str) -> str:
    """The rich style name for a value of this category (falls back to
    plain bold-green for any category with no dedicated color).
    """

    return CATEGORY_STYLES.get(category, "calc.value")


def styled_value(text: str, category: str) -> str:
    """Wrap `text` in the rich markup for its category's color."""

    style = value_style(category)
    return f"[{style}]{text}[/{style}]"


def print_error(expression: str, error: ExpressionError, out: Console) -> None:
    """Print an ExpressionError with the caret pointer when possible."""

    out.print(f"error: {error.message}", style="calc.error")

    if error.position is not None:
        out.print(f"    {expression}", style="calc.source")
        out.print("    " + " " * error.position + "^", style="calc.pointer")


def print_result(result: EvaluationResult, out: Console, *, bare: bool = False) -> None:
    """Print an evaluation result, styled by category (or, if `bare`,
    just the formatted value with no category suffix — for scripting).
    """

    if bare:
        out.print(format_result(result.value), style=value_style(result.category))
        return

    out.print(
        f"{styled_value(format_result(result.value), result.category)}"
        f"  [calc.category]({result.category})[/calc.category]"
    )


def bind_variables(definitions: list[str]) -> dict[str, Value]:
    """Evaluate --var NAME=EXPR definitions, left to right.

    Each value is itself an expression evaluated by the engine, so
    types come for free ($12.50 is currency, 2.4t is tonnage) and
    later definitions can reference earlier ones.
    """

    variables: dict[str, Value] = {}

    for definition in definitions:
        name, separator, expression = definition.partition("=")
        name = name.strip()

        if not separator or not expression.strip():
            raise typer.BadParameter(f"--var expects NAME=EXPRESSION, got {definition!r}")

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


def run_once(expression: str, variables: dict[str, Value], bare: bool) -> int:
    """Evaluate one expression and print its formatted result; return
    the process exit code (0 on success, 1 on an ExpressionError).
    """

    try:
        result = evaluate_expression(expression, variables)
    except ExpressionError as error:
        print_error(expression, error, error_console)
        return 1

    print_result(result, console, bare=bare)
    return 0


def run_repl(variables: dict[str, Value]) -> int:
    """Run the interactive REPL loop
    (let-bindings, ans, vars/clear/reset/
    exit) until the user quits;
    return the process exit code.
    """

    try:
        import readline  # noqa: F401  (line editing + history when available)
    except ImportError:
        pass

    if console.width >= ASCII_ART_WIDTH:
        console.print(ASCII_ART, style="calc.banner")
    else:
        console.print("calc", style="calc.banner")

    console.print(
        "Type '[calc.command]help[/calc.command]' for commands, "
        "'[calc.command]exit[/calc.command]' to leave.",
        style="calc.info",
    )

    prompt = readline_safe_prompt(console, "[calc.prompt]calc>[/calc.prompt] ")

    while True:
        try:
            line = input(prompt).strip()
        except EOFError:
            console.print()
            return 0
        except KeyboardInterrupt:
            console.print()
            continue

        if not line:
            continue

        if line in {"exit", "quit", "q"}:
            return 0

        if line == "help":
            console.print(REPL_HELP)
            continue

        if line == "clear":
            clear_screen(console)
            continue

        if line == "reset":
            variables.clear()
            console.print("All variables cleared.", style="calc.info")
            continue

        if line == "vars":
            if not variables:
                console.print("(no variables bound — use: let NAME = EXPR)", style="calc.info")
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
    """Main entry point for the calculator REPL."""
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
