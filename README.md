

` ██████╗ █████╗ ██╗      ██████╗
██╔════╝██╔══██╗██║     ██╔════╝
██║     ███████║██║     ██║
██║     ██╔══██║██║     ██║
╚██████╗██║  ██║███████╗╚██████╗
 ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝`

# calc
-----
**calc** is a strongly typed expression language (DSL). It is designed for performing calculations on dates, durations, quantities (e.g. currencies),arrays and matrices and for manipulating tabular data.

It combines the convenience of spreadsheet-style formulas with explicit types and predictable evaluation.

Invalid type combinations fail explicitly instead of being silently coerced. 

It is not ment for a replacement of Excel or Google Sheets, as it is slow and does not support all the features of these tools.

Key features are:


## Highlights

* **Strong typing** — integers, decimals, currency, percentages, quantities, dates, times, text, tables, arrays, matrices, and other values remain distinct.
* **Exact decimal arithmetic** — money and quantities use `Decimal`, avoiding binary floating-point rounding surprises. Float is illegal in calc.
* **Static type checking** — invalid expressions fail before evaluation with errors pointing to the offending expression.
* **Calendar-aware arithmetic** — months and years follow the calendar, including end-of-month clamping and leap years.
* **Date and time intelligence** — extract calendar fields, names, ISO weeks, and date boundaries directly from temporal values.
* **Type-safe missing values** — `blank()` cannot silently behave like `0`, empty text, or another value.
* **Typed tables** — build, filter, extend, select, sort, and aggregate immutable tables.
* **Arrays and matrices** — construct and inspect homogeneous collections using the same expression language.
* **Composable expressions** — functions, casts, variables, table operations, and scalar expressions share one evaluation model.
* **CLI and REPL** — evaluate expressions from scripts or work interactively.
* **Marimo Notebook** — evaluate expressions interactively in a notebook environment.

---

## Installation

calc requires **Python 3.14+** and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/gmounac/calc.git
cd calc
uv sync
```

Start the interactive REPL:

```sh
uv run main.py
```

Or evaluate an expression directly:

```sh
uv run main.py '1 + 2'
```

Or run the Marimo Notebook:

```sh
uv run marimo run src/engine_extensions.py
```

---

## Quick Start

### Numbers

```text
calc> 1 + 2 * 3
7  (int)

calc> 1 / 2
0.5  (decimal)

calc> 2 ^ 10
1,024  (decimal)
```

Integer division and modulo are available as well:

```text
calc> 7 // 2
3  (int)

calc> 7 % 3
1  (int)
```

### Currency (Exact money)

```text
calc> $0.10 + $0.20
$0.30  (currency)

calc> $12.50 * 3
$37.50  (currency)
```

calc does not silently mix unrelated types:

```text
calc> $5 + 3
error: ...
```

Convert explicitly when a conversion is meaningful:

```text
calc> $5.15::DECIMAL
5.15  (decimal)

calc> 5::CURRENCY
$5.00  (currency)
```

### Percentages

Percentage literals are stored as ratios and applied with multiplication:

```text
calc> 200 * 10%
20  (decimal)

calc> $100 * 5%
$5.00  (currency)

calc> 5%::DECIMAL
0.05  (decimal)
```

Ambiguous expressions are intentionally rejected:

```text
$100 + 5%
```

Write the intended calculation explicitly instead:

```text
$100 + $100 * 5%
```

### Dates

Calendar arithmetic understands real calendar months:

```text
calc> 2026-01-31 + 1mo
2026-02-28  (date)

calc> 2024-01-31 + 1mo
2024-02-29  (date)
```

### Date fields

The `::` operator can extract useful calendar information:

```text
calc> 2026-08-08::YEAR
2026  (int)

calc> 2026-08-08::MONTH
8  (int)

calc> 2026-08-08::MONTHNAME
August  (text)

calc> 2026-08-08::DAY
8  (int)

calc> 2026-08-08::DAYNAME
Saturday  (text)

calc> 2026-08-08::WEEKDAY
5  (int)

calc> 2026-08-08::WEEK
32  (int)
```

`WEEKDAY` follows Python's weekday convention:

* Monday = `0`
* Tuesday = `1`
* ...
* Sunday = `6`

`WEEK` returns the ISO week number.

### Datetimes

Datetime literals use a **space** between the date and time:

```text
2026-05-08 01:05
2026-05-08 01:05:30
```

Seconds are optional.

```text
calc> 2026-05-08 01:05::HOUR
1  (int)

calc> 2026-05-08 01:05::MINUTE
5  (int)

calc> 2026-05-08 01:05:30::SECOND
30  (int)
```

### Variables

Bind values in the REPL with `let`:

```text
calc> let price = $12.50
calc> let qty = 3
calc> price * qty
$37.50  (currency)
```

The previous result is available through `ans`:

```text
calc> 20 * 4
80  (int)

calc> ans + 5
85  (int)
```

---

# Usage

## One-shot evaluation

Pass an expression directly to `main.py`:

```sh
uv run main.py '1 + 2'
uv run main.py '$5.20 * 1.5%'
```

### Variables from the command line

Use repeatable `--var NAME=EXPR` options:

```sh
uv run main.py \
  --var 'price=$12.50' \
  --var 'qty=3' \
  'price * qty'
```

Variable definitions are evaluated from left to right, so later variables can reference earlier ones:

```sh
uv run main.py \
  --var 'price=$12.50' \
  --var 'qty=3' \
  --var 'total=price * qty' \
  'total'
```

### Bare output

Use `--bare` to suppress the type label:

```sh
uv run main.py --bare '$10 / 4'
```

Output:

```text
2.5
```

This is useful when piping calc into another command.

> Shells give special meaning to characters such as `$`, `*`, and parentheses. Quote expressions appropriately for your shell.

---

## Interactive REPL

Run calc without an expression:

```sh
uv run main.py
```

Example session:

```text
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
```

### REPL commands

| Command           | Description                       |
| ----------------- | --------------------------------- |
| `let NAME = EXPR` | Bind a variable                   |
| `ans`             | Reuse the previous answer (`ans`) |
| `vars`            | List current variables            |
| `clear`           | Clear the terminal                |
| `reset`           | Clear variables, including `ans`  |
| `help`            | Show command help                 |
| `exit` / `quit`   | Leave the REPL                    |

A plain `=` is the equality operator:

```text
qty = 3
```

Assignment therefore always uses `let`:

```text
let qty = 3
```

---

# Language Reference

## Types

calc keeps values strongly typed throughout an expression.

| Category   | Example                          | Description                            |
| ---------- | -------------------------------- | -------------------------------------- |
| `int`      | `42`                             | Whole number                           |
| `decimal`  | `3.14`, `2e3`                    | Exact decimal number                   |
| `boolean`  | result of `1 < 2`                | `TRUE` or `FALSE`                      |
| `date`     | `2026-01-15`                     | Calendar date                          |
| `datetime` | `2026-01-15 09:30`               | Date and time                          |
| `time`     | `09:30`, `09:30:45`              | Clock time                             |
| `duration` | `30min`, `2h`, `3d`, `4mo`, `1y` | Calendar-aware duration                |
| `currency` | `$12.50`                         | Monetary quantity                      |
| `tonnage`  | `2.4t`                           | Tonnage quantity                       |
| `percent`  | `1.5%`                           | Percentage stored as a ratio           |
| `complex`  | `4i`, `3 + 4i`                   | Complex number                         |
| `blank`    | `blank()`                        | Missing-value sentinel                 |
| `text`     | `"hello"`                        | Text value                             |
| `char`     | `0x2B`                           | One Unicode codepoint                  |
| `table`    | `table(...)`                     | Typed columnar table                   |
| `array`    | `array(1, 2, 3)`                 | Homogeneous one-dimensional collection |
| `matrix`   | `matrix(...)`                    | Homogeneous two-dimensional collection |
| `column`   | `column()`        | A column from a table or and array with a header     |

---

## Operators

### Arithmetic

```text
+   -   *   /   //   %   ^
```

Standard precedence applies:

```text
1 + 2 * 3
# 7
```

Parentheses can be used explicitly:

```text
(1 + 2) * 3
# 9
```

### Comparisons

```text
=   <>   <   <=   >   >=
```

Example:

```text
5 >= 3
# TRUE
```

Chained comparisons are not supported:

```text
1 < 2 < 3
```

Write them explicitly:

```text
and(1 < 2, 2 < 3)
```

### Logical operations

Logical operations are functions rather than infix keywords:

```text
and(...)
or(...)
not(...)
```

For example:

```text
and(price > $10, qty > 0)
```

`and()` and `or()` short-circuit, so an unreachable argument is not evaluated.

---

# Numbers and Quantities

## Integers and decimals

Integers remain integers when the operation can preserve the type.

Division returns a decimal:

```text
1 / 2
# 0.5
```

Exponentiation also returns a decimal:

```text
2 ** 8
# 256
```

Decimal calculations use Python's `Decimal` rather than binary floating point.

---

## Currency

Currency literals begin with `$`:

```text
$10
$12.50
$.50
```

Currency arithmetic preserves the unit where appropriate:

```text
$10 + $5
$10 - $2
$10 * 3
$10 / 2
```

A currency value divided by another currency value produces a dimensionless decimal ratio.

---

## Tonnage

Tonnage literals use the `t` suffix:

```text
2.4t
15t
```

Output is formatted to three decimal places:

```text
2.400 t
```

---

## Currency × tonnage

calc currently supports a convenience rule where a currency amount can act as an implicit per-tonne rate:

```text
$450 * 2.4t
# $1,080.00
```

The operation is symmetric:

```text
2.4t * $450
```

produces the same result.

---

## Percentages

A percentage literal represents its ratio:

```text
5%
```

internally represents:

```text
0.05
```

Apply percentages with multiplication:

```text
200 * 10%
# 20
```

Percentage arithmetic includes:

```text
50% + 10%
50% - 10%
50% * 10%
50% / 2
50% / 10%
```

Casting exposes the raw ratio:

```text
5%::DECIMAL
# 0.05
```

Casting in the other direction interprets a plain number as a percentage:

```text
5::PERCENT
# 5%
```

---

# Complex Numbers

A number immediately followed by `i` is an imaginary literal:

```text
4i
3 + 4i
```

A bare `i` remains an ordinary variable name.

Supported operations include:

```text
+   -   *   /
```

with complex values and ordinary numbers.

Functions:

```text
re(3 + 4i)
# 3

im(3 + 4i)
# 4

conj(3 + 4i)
# 3-4i

abs(3 + 4i)
# 5
```

Complex numbers support equality and inequality, but they do not have a total ordering.

---

# Infinity

Infinity is available through either:

```text
infinity()
```

Examples:

```text
infinity() > 10 ** 100
# TRUE

5 / infinity()
# 0

-infinity()
# -∞
```

Indeterminate expressions fail explicitly:

```text
infinity() - infinity()
infinity() / infinity()
0 * infinity()
```

Infinity is a decimal concept and cannot be silently converted into quantities such as currency.

---

# Dates, Times, and Durations

## Date literals

Dates use:

```text
YYYY-MM-DD
```

Example:

```text
2026-08-14
```

Invalid calendar dates are rejected.

---

## Datetime literals

Datetimes use:

```text
YYYY-MM-DD HH:MM
YYYY-MM-DD HH:MM:SS
```

Example:

```text
2026-08-14 09:30
2026-08-14 09:30:45
```

The literal syntax uses a space between the date and time.

Hours use `00`–`23`, and minutes and seconds use `00`–`59`.

---

## Time literals

Times use:

```text
HH:MM
HH:MM:SS
```

Examples:

```text
09:30
09:30:45
```

---

## Durations

Supported duration suffixes are:

| Suffix | Unit            |
| ------ | --------------- |
| `s`    | seconds         |
| `min`  | minutes         |
| `h`    | hours           |
| `d`    | days            |
| `w`    | weeks           |
| `mo`   | calendar months |
| `y`    | calendar years  |

Examples:

```text
30min
2h
3d
2w
1mo
1y
```

Durations internally keep calendar months, days, and seconds separate.

This matters because a month is not a fixed number of days.

---

## Calendar-aware arithmetic

```text
2026-01-31 + 1mo
# 2026-02-28
```

Leap years are handled correctly:

```text
2024-01-31 + 1mo
# 2024-02-29
```

A bare date cannot accept a duration containing hours, minutes, or seconds:

```text
2026-01-01 + 2h
# error
```

Use a datetime instead:

```text
2026-01-01 00:00 + 2h
```

Clock-time arithmetic wraps around midnight.

---

## Temporal differences

Subtracting two temporal values returns a duration:

```text
2026-05-01 - 2026-04-28
# 3d
```

Use `days_between()` when you specifically want an integer number of days:

```text
days_between(2026-01-01, 2026-02-01)
# 32
```

Durations do not have a universal total ordering because calendar months do not have fixed lengths.

---

# Casts and Field Extraction

The syntax:

```text
value::TARGET
```

is used for both explicit conversion and field extraction.

Target names are case-insensitive:

```text
2026-05-01::DAY
2026-05-01::day
2026-05-01::Day
```

are equivalent.

Casts can be chained:

```text
2026-01-05 14:30:00::DATE::MONTH
# 1
```

---

## Date fields

Available on `date` values:

| Target      | Result                     |
| ----------- | -------------------------- |
| `YEAR`      | Year number                |
| `MONTH`     | Month number               |
| `MONTHNAME` | Full month name            |
| `DAY`       | Day of month               |
| `DAYNAME`   | Full weekday name          |
| `WEEKDAY`   | Weekday number, Monday = 0 |
| `WEEK`      | ISO week number            |
| `EOMONTH`   | Last date of the month     |

Examples:

```text
2026-08-08::YEAR
# 2026

2026-08-08::MONTHNAME
# August

2026-08-08::DAYNAME
# Saturday

2026-08-08::WEEKDAY
# 5

2026-08-08::WEEK
# 32

2026-08-08::EOMONTH
# 2026-08-31
```

---

## Datetime fields

Datetimes support the date fields above plus:

```text
::HOUR
::MINUTE
::SECOND
```

Example:

```text
2026-08-08 14:30:45::HOUR
# 14
```

---

## Time fields

Times support:

```text
::HOUR
::MINUTE
::SECOND
```

Example:

```text
14:30:45::SECOND
# 45
```

---

## Temporal conversions

```text
datetime::DATE
datetime::TIME
date::DATETIME
```

Examples:

```text
2026-01-05 14:30:00::DATE
# 2026-01-05

2026-01-05 14:30:00::TIME
# 14:30:00

2026-01-05::DATETIME
# 2026-01-05 00:00:00
```

Identity casts are also supported:

```text
date::DATE
time::TIME
datetime::DATETIME
```

---

## Numeric conversions

Examples:

```text
5::DECIMAL
7.9::INT
5::CURRENCY
2.4::TONNAGE
5::PERCENT
```

`DECIMAL -> INT` truncates toward zero:

```text
7.9::INT
# 7

-7.9::INT
# -7
```

This is deliberately different from `round()`.

---

## Text conversions

Scalar values can be converted to their display representation with `::TEXT`:

```text
5::TEXT
$5.20::TEXT
2026-05-01::TEXT
```

Text can be parsed back into supported scalar types:

```text
"5"::INT
"5.5"::DECIMAL
"12.50"::CURRENCY
"2.4"::TONNAGE
"5"::PERCENT
"true"::BOOLEAN
"2026-01-05"::DATE
"2026-01-05 14:30:00"::DATETIME
"14:30:00"::TIME
```

Invalid parsing fails explicitly:

```text
"abc"::INT
# error
```

---

# Text

Text literals use double quotes:

```text
"hello"
```

Supported escape sequences include:

```text
\"
\\
\n
\t
```

Text supports comparisons:

```text
"a" = "a"
"a" <> "b"
"a" < "b"
"a" <= "a"
"b" > "a"
```

Text arithmetic is intentionally not defined. However, we can use `concat()` to concatenate text values.

Use explicit casts when combining textual representations with other types.

We also have `left()`, `right()`, and `mid()` for extracting parts of text, and `at()` for extracting a single character by index.

The Excel `TEXT()` function is available as `format()`. However the temporal formatting uses the chrono syntax.

| %Y  | 4-digit year
| %y  | 2-digit year
| %m  | month number
| %B  | full month name
| %b  | abbreviated month name
| %d  | day of month
| %A  | full weekday
| %a  | abbreviated weekday
| %H  | hour, 24-hour
| %I  | hour, 12-hour
| %M  | minute
| %S  | second
| %f  | fractional seconds
| %p  | AM/PM

while the number formatting adopts the Excel versions.
---

# Characters

A `char` represents one Unicode codepoint and is distinct from text.

Character literals use hexadecimal Unicode codepoints:

```text
0x2B
```

which represents:

```text
+
```

Conversions:

```text
0x2B::INT
# 43

0x2B::TEXT
# +

43::CHAR
# +

"+"::CHAR
# +
```

Invalid Unicode scalar values are rejected.

Characters support comparisons but not arithmetic.

---

# Blank Values

`blank()` is calc's type-safe missing-value marker.

```text
blank()
```

It cannot silently act as zero or empty text:

```text
blank() + 5
# error
```

Use:

```text
isblank(value)
```

to test for it, or:

```text
coalesce(value, default)
```

to replace it.

Example:

```text
coalesce(blank(), $0)
# $0.00
```

This keeps missing-value handling explicit.

---

# Functions

## Numeric and aggregate functions

| Function             | Description                                         |
| -------------------- | --------------------------------------------------- |
| `abs(x)`             | Absolute value; complex values return their modulus |
| `round(x[, digits])` | Round to the requested number of decimal places     |
| `ceil(x, multiple)`  | Round upward to a positive multiple                 |
| `min(...)`           | Smallest compatible value                           |
| `max(...)`           | Largest compatible value                            |
| `sum(...)`           | Sum compatible values                               |
| `avg(...)`           | Average compatible values                           |
| `pi()`               | π                                                   |
| `e()`                | Euler's number                                      |
| `infinity()`         | Positive infinity                                   |
| `format(value, format)` | Format a text value according to the specified format  |
| `left(text, n)`      | Return the leftmost `n` characters of `text`        |
| `right(text, n)`     | Return the rightmost `n` characters of `text`       |
| `mid(text, start, n)` | Return the `n` characters of `text` starting at `start` |


### `ceil()`

`ceil()` rounds to a specified multiple rather than merely to the next integer:

```text
ceil(7, 5)
# 10

ceil(3h + 20min, 1h)
# 4h

ceil(50min, 15min)
# 1h

ceil($12.30, $0.50)
# $12.50
```

The multiple must be positive and compatible with the value.

---

## Complex functions

| Function  | Description         |
| --------- | ------------------- |
| `re(z)`   | Real component      |
| `im(z)`   | Imaginary component |
| `conj(z)` | Complex conjugate   |

---

## Conditional and logical functions

| Function                    | Description                  |
| --------------------------- | ---------------------------- |
| `if(condition, then, else)` | Lazy conditional             |
| `and(...)`                  | Lazy logical AND             |
| `or(...)`                   | Lazy logical OR              |
| `not(x)`                    | Boolean negation             |
| `isblank(x)`                | Test for `blank()`           |
| `coalesce(x, default)`      | Replace blank with a default |

`if()` evaluates only the selected branch:

```text
if(1 = 1, 2, 1 // 0)
# 2
```

The division by zero is never evaluated.

---

## Type function

| Function                       | Description                            |
| ------------------------------ | -------------------------------------- |
| `type_of(value)`                  | Return the type of `value`             |
---

## Temporal functions

| Function                       | Description                            |
| ------------------------------ | -------------------------------------- |
| `today()`                      | Current date                           |
| `now()`                        | Current datetime, second precision     |
| `time(hour, minute[, second])` | Construct a time                       |
| `days_between(start, end)`     | Whole number of days between two dates |
| `somonth(value)`          | First day of the month                 |
| `eomonth(value)`            | Last day of the month                  |
| `soquarter(value)`        | First day of the quarter               |
| `eoquarter(value)`          | Last day of the quarter                |
| `soyear(value)`           | First day of the year                  |
| `eoyear(value)`             | Last day of the year                   |

---

# Time Intelligence

Date-boundary functions provide explicit calendar operations without implicit evaluation context.

```text
somonth(2026-08-08)
# 2026-08-01

eomonth(2026-08-08)
# 2026-08-31

eomonth(2024-02-15)
# 2024-02-29

soquarter(2026-08-08)
# 2026-07-01

eoquarter(2026-08-08)
# 2026-09-30

soyear(2026-08-08)
# 2026-01-01

eoyear(2026-08-08)
# 2026-12-31
```

When passed a datetime, the boundary functions return a datetime at midnight:

```text
somonth(2026-08-08 14:30:00)
# 2026-08-01 00:00:00
```

These functions compose naturally with table operations.

For example, a year-to-date total can be expressed directly:

```text
sum(
    filter(
        t,
        and(
            [date] >= soyear(asof),
            [date] <= asof
        )
    )::amount
)
```

There is no hidden filter context: every part of the calculation is visible in the expression.

---

# Tables

A table is an immutable, columnar, statically typed value.

Its type includes its schema.

For example:

```text
table{vessel: text, qty: tonnage}
```

is structurally different from:

```text
table{vessel: text, amount: currency}
```

---

## Creating columns and tables

Columns are constructed with `column()`:
Note "vessel" is the header and "Njord", "Selkie" are the values.
```text
column("vessel", "Njord", "Selkie")
```

Create a table by combining columns:

```text
table(
    column("vessel", "Njord", "Selkie"),
    column("qty", 3.4t, 2.1t)
)
```

All columns must contain the same number of rows.

Column names become part of the table's static schema and therefore must be literal text.

Column names are case-sensitive for lookup.

---

## Column access

The same `::` operator used for casts is used to extract table columns:

```text
t::qty
```

Aggregates accept columns directly:

```text
sum(t::qty)
avg(t::qty)
min(t::qty)
max(t::qty)
```

Count rows with:

```text
rowcount(t)
```

---

# Table Operations

Table transformations are ordinary functions and compose with the rest of the language.

## `filter`

Keep rows matching an expression:

```text
filter(t, [qty] > 2t)
```

## `select`

Select or reorder columns:

```text
select(t, "vessel", "qty")
```

## `extend`

Add a computed column:

```text
extend(
    t,
    "value",
    [qty] * [price]
)
```

## `sort`

Sort rows by a row expression:

```text
sort(t, [qty])
```

Descending:

```text
sort(t, [qty], "desc")
```

## `groupby`

Group and aggregate:

```text
groupby(t, "vessel", "qty", "sum")
```

Supported aggregate names are:

```text
sum
avg
min
max
count
```

---

## Row expressions

`filter()`, `extend()`, and `sort()` evaluate an expression once per row.

Inside a row expression:

```text
[column]
```

means the current row's value.

A bare identifier remains an ordinary variable.

For example:

```text
let qty = 2.5t

filter(t, [qty] > qty)
```

Here:

* `[qty]` is the current table row's `qty`
* `qty` is the outer variable

The two scopes are deliberately explicit and never collide.

---

## Composing table operations

Table operations can be nested into pipelines:

```text
select(
    sort(
        extend(
            filter(t, [qty] > 1.5t),
            "value",
            [qty] * [price]
        ),
        [value],
        "desc"
    ),
    "vessel",
    "value"
)
```

No separate query language is required.

---

# Arrays and Matrices

## Arrays

An array is a homogeneous, headerless sequence:

```text
array(1, 2, 3)
```

Aggregate functions work directly with arrays:

```text
sum(array(1, 2, 3))
# 6

min(array("b", "a", "c"))
# a
```

Get its size with:

```text
len(array(1, 2, 3))
# 3
```

---

## Matrices

A matrix is a homogeneous two-dimensional grid:

```text
matrix(
    array(1, 2, 3),
    array(4, 5, 6)
)
```

Matrices currently support construction, indexing, and introspection.

Matrix arithmetic is not yet defined.

---

## Indexing

Collections use `at()` rather than bracket indexing.

Indexes are **1-based**.

```text
at(array(10, 20, 30), 2)
# 20
```

Matrix indexing uses row and column:

```text
at(
    matrix(
        array(1, 2),
        array(3, 4)
    ),
    2,
    1
)
# 3
```

Matrix dimensions can be inspected with:

```text
rowcount(m)
colcount(m)
```

---

# Type Safety

calc deliberately avoids silent coercion.

Examples of invalid operations include:

```text
$5 + 3
```

because currency and an ordinary number are different types.

```text
$5 + 2t
```

because currency and tonnage cannot be added.

```text
2026-01-01 + 2h
```

because adding clock time to a date would change the result from a date into a datetime.

```text
3h < 1mo
```

because a calendar month does not have a fixed duration that can be universally ordered against three hours.

```text
(1 + 2i) < (3 + 4i)
```

because complex numbers do not have a total ordering.

Unsupported combinations fail rather than guessing what the expression was intended to mean.

---

# Errors

Parse, type, and evaluation errors are reported as `ExpressionError`.

The CLI prints the source expression and, when a source position is available, points to the offending location:

```text
error: ...
    expression here
          ^
```

Type checking happens before evaluation whenever possible, so invalid formulas fail early.

---

# Architecture

The expression engine is split into focused modules under `src/engine/`:

```text
src/
├── engine/
│   ├── __init__.py
│   ├── calendar_utils.py
│   ├── casts.py
│   ├── evaluator.py
│   ├── formatting.py
│   ├── functions.py
│   ├── lexer.py
│   ├── operators.py
│   ├── parser.py
│   └── values.py
├── notebook.py
└── vision.py
```

At a high level:

```text
source text
    ↓
lexer
    ↓
parser
    ↓
typed expression tree
    ↓
type checker
    ↓
evaluator
    ↓
formatter
```

### Modules

* `lexer.py` — tokenizes source text
* `parser.py` — builds the expression tree
* `values.py` — value and type definitions
* `operators.py` — unary and binary operator dispatch
* `casts.py` — `::TARGET` conversion and field extraction
* `functions.py` — built-in functions and table operations
* `calendar_utils.py` — calendar-aware date/time arithmetic
* `evaluator.py` — type checking and evaluation
* `formatting.py` — user-facing value formatting
* `main.py` — CLI and interactive REPL

The CLI, notebooks, and tests use the same engine implementation.

---

# Development

Install dependencies:

```sh
uv sync
```

Run the test suite:

```sh
uv run pytest -q
```

Run the linter:

```sh
uv run ruff check .
```

Run calc directly:

```sh
uv run main.py '1 + 2'
```

Launch the interactive notebook:

```sh
uv run marimo edit src/notebook.py
```

The project also includes `src/vision.py` for experimenting with larger expression and table workflows.

---

# Extending calc

The language is intentionally table-driven in several important areas.

New behavior is generally added by registering:

* a type
* an operator rule
* a cast
* a function
* a formatter

See [`docs/EXTENDING.md`](docs/EXTENDING.md) for the extension guide and implementation checklist.

---

# Design Principles

calc follows a small set of principles throughout the engine.

### Explicit types

A value's meaning should be visible from its type. Currency is not just a decimal with a formatting flag, and a date is not just a string.

### No silent coercion

When two types do not have a clearly defined operation, calc reports an error instead of inventing one.

### Exact arithmetic where precision matters

Decimal values and quantities avoid binary floating-point arithmetic.

### Calendar units are calendar units

A month is not defined as 30 days. Adding one month means moving one calendar month while keeping the resulting date valid.

### Explicit context

Table row values use `[column]`; ordinary variables use ordinary identifiers. There is no hidden row or filter context.

### Composability

Functions, casts, tables, arrays, temporal operations, and scalar expressions all use the same expression model.

---

## Example

A larger expression can combine multiple parts of the language without switching syntax:

```text
sum(
    filter(
        extend(
            shipments,
            "value",
            [qty] * rate
        ),
        and(
            [date] >= startofyear(asof),
            [date] <= asof
        )
    )::value
)
```

The expression:

1. computes a value for each shipment,
2. filters the table to the current year,
3. extracts the computed column,
4. and sums it.

That is the core idea behind calc: **typed calculations that remain explicit, predictable, and composable as they grow.**
