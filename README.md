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
calc> abs(3 + 4i)
5  (decimal)
calc> coalesce(blank(), $0)
$0.00  (currency)
```

## Features

- **Typed values**: whole numbers, decimals, booleans, dates, datetimes,
  times, calendar-aware durations, currency, tonnage, percentages,
  complex numbers, and a single type-safe blank marker are all distinct
  types. `$5 + 3` is a type error, not a guess.
- **Exact arithmetic**: money and quantities use `Decimal`, never binary
  floats — `$0.10 + $0.20` is really `$0.30`.
- **Static type checking**: expressions are checked before they're
  evaluated, so a bad formula fails fast with a `^` pointing at the
  offending token instead of halfway through a computation.
- **Calendar-aware dates**: adding `1mo` to `2026-01-31` correctly lands on
  `2026-02-28`, not an invalid date.
- **A type-safe blank marker**: one sentinel for "missing" — `isblank()`
  and `coalesce()` are the only sanctioned ways to touch it; arithmetic
  or a comparison against anything else is a type error, not a silent 0.
- **Infinity**: `infinity()` or the literal `∞`, backed by Decimal's own
  IEEE-854 infinity — ordinary arithmetic and comparisons work on it for
  free, and indeterminate forms (`∞ − ∞`, `∞ / ∞`) fail loudly instead of
  guessing.
- **Variables and functions**: bind values with `let`, reference them
  later, and call `if`, `coalesce`, `sum`, `avg`, `min`, `max`, `round`,
  `ceil`, `abs`, `re`, `im`, `conj`, `isblank`, `days_between`, `today`,
  `now`, `time`, `pi`, `e`, and `infinity`.
- **Casts**: `value::TARGET` extracts a date/time field or converts
  between types — `2026-05-01::MONTH` is `5`, `now()::DATE` drops the
  time of day, `$5.15::DECIMAL` unwraps the currency to a plain number.
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
| `datetime`  | `2026-01-15T09:30`, `2026-01-15 09:30` | ISO 8601, `T`/`t` or a space as the separator |
| `time`      | `09:30`, `09:30:00`      | ISO 8601                                |
| `duration`  | `30min`, `2h`, `3d`, `4mo`, `1y` | calendar-aware, see below       |
| `currency`  | `$12.50`                 | 2 decimal places                        |
| `tonnage`   | `2.4t`                   | 3 decimal places, always shown          |
| `percent`   | `1.5%`                   | stored as a ratio, applied via `*`      |
| `complex`   | `4i`, `3+4i`             | bare `i` with no glued digits is still an ordinary variable name |
| `blank`     | `blank()`                | the one "missing value" marker — see below |

`3.14159...` and `2.71828...` are available as `pi()` and `e()` (function
calls, not bare identifiers, so they can never shadow a variable you
happen to name `pi`), each good to 50 significant digits — comfortably
past `Decimal`'s default 28-digit working precision, so a calculation's
own precision is always the limit, not the constant's. `infinity()` or
the literal `∞` gives you `Decimal`'s built-in IEEE-854 infinity, which
means every existing rule for `decimal` — comparisons, unary minus,
`min`/`max`, `abs` — already works on it with no special-casing:

```
infinity() > 10 ** 100    # TRUE
5 / infinity()             # 0
infinity() * -1              # -∞
-∞ < 0                        # TRUE
```

Indeterminate forms fail loudly rather than guessing:

```
infinity() - infinity()   # error — this has no defined result
0 * infinity()              # error — same
$5 * infinity()               # error — currency can't be infinite
```

Duration units: `s`, `min`, `h`, `d`, `w`, `mo`, `y`. Durations track
months, days, and seconds separately (calendar months aren't a fixed
number of days), so they support `+`/`-` with dates and each other, but
have no total ordering — `min`/`max`/`<` reject them on purpose.

### Blank

`blank()` is the single "missing value" marker — it plays the role SQL's
`NULL`, a blank spreadsheet cell, and floating-point `NaN` would
separately play elsewhere, unified into one sentinel. It's deliberately
inert: no arithmetic, and no comparison against anything but another
blank.

```
blank() + 5                  # error — arithmetic on blank isn't defined
blank() = 5                    # error — cross-type comparison isn't defined
isblank(x)                       # TRUE/FALSE, works on any type
coalesce(x, default)               # x if x isn't blank, else default
```

That's the "type safe" part: a blank value can never silently act like
`0` or `""` in a calculation the way it might in a spreadsheet — you
have to explicitly unwrap it with `coalesce()` first.

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
| `pi()` / `e()`                 | mathematical constants, 50 significant digits      |
| `infinity()`                    | `Decimal`'s IEEE-854 infinity; `∞` also works      |
| `abs(x)`                       | absolute value (number, duration, quantity, or the modulus of a complex number) |
| `round(x[, digits])`           | round to `digits` decimal places (default 0)       |
| `ceil(x, multiple)`            | round `x` up to the nearest multiple of `multiple` |
| `min(...)` / `max(...)`        | smallest/largest of same-typed, orderable values   |
| `sum(...)` / `avg(...)`        | total/average of numbers, quantities, durations, or complex numbers |
| `re(z)` / `im(z)`              | real / imaginary part of a complex number          |
| `conj(z)`                       | complex conjugate                                    |
| `blank()`                       | the missing-value marker                             |
| `isblank(x)`                     | `TRUE` iff `x` is blank — the one function that accepts any type |
| `coalesce(x, default)`            | `x`, or `default` if `x` is blank                    |
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

### Casts (`value::target`)

`::` extracts a field from a date/time value, or converts a value to a
different type. It's case-insensitive, so `::DAY` and `::day` are the
same, and it chains left to right: `x::datetime::date` casts to a
datetime first, then that result to a date.

```
2026-05-01::DAY               # 1
2026-05-01::MONTH               # 5
2026-05-01::YEAR                  # 2026
01:00::HOUR                         # 1
01:05::MINUTE                         # 5     — MINUTES also works
2026-01-05T14:30:00::DATE             # 2026-01-05
2026-01-05 01:00::DATE                 # 2026-01-05  — a space works as the datetime separator too, not just T
$5.15::DECIMAL                           # 5.15
5::CURRENCY                                # $5.00
5%::DECIMAL                                  # 0.05  — the raw stored ratio, not 5
7.9::INT                                       # 7     — truncates toward zero, unlike round()
```

| Target                          | From                          | Result                                    |
| --------------------------------- | ------------------------------- | -------------------------------------------- |
| `YEAR` / `MONTH` / `DAY`             | `date`, `datetime`                | `int`                                          |
| `HOUR` / `MINUTE` / `SECOND`           | `time`, `datetime`                  | `int` (plural spellings also accepted)           |
| `DATE`                                   | `datetime`                            | `date` — drops the time of day                     |
| `TIME`                                     | `datetime`                              | `time` — drops the date                              |
| `DATETIME`                                   | `date`                                    | `datetime` at midnight                                 |
| `DECIMAL`                                      | `currency`, `tonnage`, `percent`, `int`     | the raw stored number                                    |
| `CURRENCY` / `TONNAGE`                            | `int`, `decimal`                              | wraps the number in that unit                              |
| `PERCENT`                                           | `int`, `decimal`                                | read the way `%` reads a literal — `5::PERCENT` is `5%`, not `500%` |
| `INT`                                                 | `decimal`                                         | truncates toward zero (not `round()`'s half-up)                       |

`::` binds tighter than `**` and unary minus, so `-5::decimal` is
`-(5::decimal)` and `2 ** 3::decimal` is `2 ** (3::decimal)` — it binds
to the value immediately on its left, the same way `4i` or `2.4t`
binds only to the number directly before it. Cast a whole expression
by parenthesizing it first: `(a + b)::date`.

An unsupported combination is a type error, same as any other
mismatched types:

```
5::DAY                 # error — a whole number has no day field to extract
$5::TONNAGE               # error — no defined conversion between units
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

### Adding a type, function, or constant

See [`docs/EXTENDING.md`](docs/EXTENDING.md) for a walkthrough of each,
with a worked example and a checklist to follow.
