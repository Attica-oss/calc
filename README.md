# calc

A strongly typed, spreadsheet-style expression evaluator. Write formulas like
`$450 * 2.4t` or `price * qty + $4.99` and get back an exact answer with its
type — no silent unit mixing, no binary-float rounding surprises on money.

```
calc> $450 * 2.4t
$1,080.00  (currency)
calc> price - price * 15%
calc> 2026-01-31 + 1mo
2026-02-28  (date)
```

## Features

- **Typed values**: whole numbers, decimals, booleans, dates, datetimes,
  times, calendar-aware durations, currency, tonnage, and percentages are
  all distinct types. `$5 + 3` is a type error, not a guess.
- **Exact arithmetic**: money and quantities use `Decimal`, never binary
  floats — `$0.10 + $0.20` is really `$0.30`.
- **Static type checking**: expressions are checked before they're
  evaluated, so a bad formula fails fast with a `^` pointing at the
  offending token instead of halfway through a computation.
- **Calendar-aware dates**: adding `1mo` to `2026-01-31` correctly lands on
  `2026-02-28`, not an invalid date.
- **Variables and functions**: bind values with `let`, reference them
  later, and call `if`, `sum`, `avg`, `min`, `max`, `round`, `abs`,
  `days_between`, `today`, `now`, and `time`.
- **A CLI and a REPL**: one-shot evaluation for scripting, or an
  interactive shell with variable history, colorized output, and an
  `ans` register.
- **A marimo notebook** (`src/notebook.py`) importing the same engine
  module as the CLI, for interactive exploration and inline tests.

## Installation

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Attica-oss/calc.git
cd calc
uv sync
```

## Usage

### One-shot evaluation

```sh
uv run main.py "1 + 2"
uv run main.py "$5.2 * 1.5%"
uv run main.py --var price='$12.50' --var qty=3 "price * qty"
uv run main.py --bare "$10 / 4"     # value only, no type label — pipe-friendly
```

Quote expressions in the shell: `$`, `*`, and parentheses are shell
metacharacters. An expression that starts with `-` (e.g. `-5 + 3`) is fine
too — calc inserts the POSIX `--` separator for you so it isn't mistaken
for a flag.

- `--var NAME=EXPR` binds a variable before evaluating; the value is
  itself an expression, so types come for free and it's repeatable
  (later ones may reference earlier ones).
- `--bare` prints only the formatted value, with no type label — useful
  when piping into another command.

### Interactive REPL

Run with no expression argument to start the REPL:

```
$ uv run main.py
calc — type 'help' for commands, 'exit' to leave.
calc> let price = $12.50
calc> let total = price * 3
calc> total > $30
TRUE  (boolean)
calc> -
TRUE  (boolean)
calc> vars
  price = $12.50  (currency)
  total = $37.50  (currency)
  ans = TRUE  (boolean)
calc> exit
```

REPL commands:

| Command            | Effect                                             |
| ------------------ | --------------------------------------------------- |
| `let NAME = EXPR`   | bind a variable                                     |
| `-`                 | reuse the previous answer (shorthand for `ans`)     |
| `vars`              | list currently bound variables                      |
| `clear`             | clear the screen                                    |
| `reset`             | clear all bound variables, including `ans`          |
| `help`              | show the command summary                            |
| `exit` / `quit`     | leave (Ctrl-D also works)                           |

Note that a plain `=` is the equality operator (`qty = 3` is a
comparison), which is why assignment uses the `let` keyword. The result
of the last plain expression is always available as `ans`.

## The language

### Types

| Category    | Example literal        | Notes                                  |
| ----------- | ----------------------- | --------------------------------------- |
| `int`       | `42`                     | plain digit runs                        |
| `decimal`   | `3.14`, `2e3`            | exact, never a binary float             |
| `boolean`   | result of a comparison   | printed as `TRUE` / `FALSE`             |
| `date`      | `2026-01-15`             | ISO 8601                                |
| `datetime`  | `2026-01-15T09:30`       | ISO 8601, `T` or `t` separator          |
| `time`      | `09:30`, `09:30:00`      | ISO 8601                                |
| `duration`  | `30min`, `2h`, `3d`, `4mo`, `1y` | calendar-aware, see below       |
| `currency`  | `$12.50`                 | 2 decimal places                        |
| `tonnage`   | `2.4t`                   | 3 decimal places                        |
| `percent`   | `1.5%`                   | stored as a ratio, applied via `*`      |

Duration units: `s`, `min`, `h`, `d`, `w`, `mo`, `y`. Durations track
months, days, and seconds separately (calendar months aren't a fixed
number of days), so they support `+`/`-` with dates and each other, but
have no total ordering — `min`/`max`/`<` reject them on purpose.

### Operators

`+  -  *  /  //  %  **` and comparisons `=  <>  <  <=  >  >=`. Chained
comparisons (`1 < 2 < 3`) are rejected — write `1 < 2 and 2 < 3` style
logic with `if()` instead.

The type checker enforces sensible combinations only:

```
$5 + 3                 # error — currency and a plain number don't mix
price + shipment        # error — currency and tonnage don't add
2026-01-01 + 2h          # error — a date can't take hours; use a datetime
$100 + 5%                # error — ambiguous ("grow by 5%" or "add a raw ratio"?)
                          #   write $100 * 1.05 or $100 * 5% + $100 instead
```

A few deliberate, documented exceptions carry real meaning:

```
$450 * 2.4t             # $1,080.00 — currency acts as an implicit per-tonne rate
$5.20 * 1.5%             # $0.08    — percentages apply, Excel-style
200 * 10%                 # 20       — same rule for plain numbers
2026-01-31 + 1mo           # 2026-02-28 — calendar month, clamped to a valid day
```

### Functions

| Function                     | Description                                      |
| ----------------------------- | ------------------------------------------------- |
| `today()`                     | current date                                       |
| `now()`                       | current datetime (seconds precision)               |
| `time(h, m[, s])`              | build a time value                                 |
| `abs(x)`                       | absolute value (number, duration, or quantity)     |
| `round(x[, digits])`           | round to `digits` decimal places (default 0)       |
| `ceil(x, multiple)`            | round `x` up to the nearest multiple of `multiple` |
| `min(...)` / `max(...)`        | smallest/largest of same-typed, orderable values   |
| `sum(...)` / `avg(...)`        | total/average of numbers, quantities, or durations |
| `if(cond, then, else)`         | lazy — the untaken branch is never evaluated       |
| `days_between(date, date)`     | whole days between two dates, as an `int`          |

`ceil()` is Excel's `CEILING` rather than a plain math ceiling: it needs
a second argument to round up *to*, since "round up" is meaningless for
a duration or a currency amount on its own. `multiple` must be the same
kind of value as `x` (both numbers, or matching durations/quantities)
and positive.

```
ceil(7, 5)                # 10
ceil(3h + 20min, 1h)       # 4h    — round a duration up to the nearest hour
ceil(50min, 15min)          # 1h    — nearest 15-minute increment
ceil($12.30, $0.50)          # $12.50
ceil(3h, 1mo)                 # error — a month has no fixed length to divide by
```

### Variables

`--var` on the command line, or `let NAME = EXPR` in the REPL. A
variable's type is whatever its bound expression evaluates to, and later
definitions can reference earlier ones:

```sh
uv run main.py --var price='$12.50' --var qty=3 --var total='price * qty' total
# $37.50  (currency)
```

## Development

```sh
uv run ruff check .          # lint
uv run main.py "1 + 2"        # run the CLI
uv run marimo edit src/notebook.py   # explore the engine interactively, with inline tests
```

### Project structure

```
main.py               CLI entry point (typer + rich)
src/engine_cli.py      the expression engine: tokenizer, parser, type checker,
                        evaluator, formatter — imported by main.py and the notebook
src/notebook.py         a marimo notebook importing engine_cli.py, with
                         inline tests and an interactive UI
```

`src/engine_cli.py` is the single source of truth for the engine; both
`main.py` and `src/notebook.py` import from it rather than redefining
any of it, so a fix or new feature only has to be made once.
