# Vision

## Calc makes operational data trustworthy

Operational work is full of values that look simple but are not.

```text
$125.00
15.250 t
2026-08-19
2mo
12.5%
BICU1234565
```

A spreadsheet can store all of them.

The problem is that a spreadsheet usually does not know what they mean.

A currency can accidentally become a plain number. A container number can become arbitrary text. A month can be treated like thirty days. A malformed import can silently change type. A formula can operate on the wrong column and still produce something that looks plausible.

Calc exists to make those mistakes difficult.

> **Calc is a typed language for operational data.**

It combines exact values, explicit semantics, typed tables, domain validation, and composable expressions into a language small enough to understand and strict enough to trust.

---

# The problem

Spreadsheets are powerful because they are permissive.

That same permissiveness becomes dangerous when a workbook becomes operational infrastructure.

A real business workbook may contain:

* currencies
* quantities
* dates and times
* percentages
* container numbers
* vessel or voyage references
* lookup tables
* business calendars
* reconciliation rules
* imported CSV data
* accumulated formulas written by multiple people

The spreadsheet usually represents most of those concepts as generic cells.

The user knows that:

```text
15.250
```

means tonnes.

The computer often does not.

The user knows that:

```text
BICU1234565
```

is a container number with a valid check digit.

The computer often sees text.

The user knows that:

```text
1mo
```

is a calendar month rather than thirty days.

The computer may not.

That gap between **what a value is** and **how software represents it** is where many operational mistakes begin.

Calc closes that gap.

---

# The core idea

Every Calc value has meaning.

```calc
$100
```

is currency.

```calc
20%
```

is a percentage.

```calc
15t
```

is tonnage.

```calc
2026-08-19
```

is a date.

```calc
BICU1234565
```

is an ISO container number.

Those distinctions survive computation.

Calc should happily evaluate:

```calc
$100 * 20%
```

because that operation has an explicit meaning.

Calc should reject:

```calc
$100 + 20
```

unless the language has deliberately defined what adding a plain number to currency means.

The philosophy is:

> **Do not guess.**

If an operation has a clear meaning, define it.

If it does not, reject it.

---

# What Calc should feel like

Calc should feel familiar enough to read immediately:

```calc
let t = table(
    column("date", 2026-01-05, 2026-03-10, 2026-07-01),
    column("amount", $100, $200, $300)
);

let asof = 2026-08-08;

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

But underneath that familiar syntax, Calc should be much stricter than a spreadsheet.

The user should not need to think about an elaborate type system.

They should notice it mostly when it protects them.

```text
error: '+' is not defined for a tonnage and a whole number.
```

or:

```text
error: invalid container check digit.
```

or:

```text
error: extend()'s row expression must produce a scalar value.
```

A good Calc error is not an obstacle.

It is evidence that the system understood enough about the data to stop a bad calculation.

---

# Typed operational data

Calc should treat common operational concepts as first-class values when doing so provides real safety.

A type earns its place in Calc when it provides one or more of:

1. validation,
2. meaningful arithmetic,
3. meaningful comparison,
4. canonical formatting,
5. domain-specific operations.

This is why currency is not merely a formatted decimal.

It is why a container number is not merely text.

It is why a duration is not merely a number of seconds.

Types should represent meaning, not decoration.

---

# Tables are values

Operational work rarely consists of isolated expressions.

The real work is performed on datasets.

Calc therefore treats tables as normal typed values.

```calc
table(
    column("container", BICU1234565, ZEPU0037255),
    column("weight", 15t, 20t),
    column("amount", $1250, $1750)
)
```

A table has a schema.

```text
table{
    container: container,
    weight: tonnage,
    amount: currency
}
```

That schema participates in type checking.

A table should not be a bag of dynamically typed cells.

It should be a collection whose structure Calc understands before evaluation continues.

---

# Small relational primitives

Calc should prefer a small number of composable operations over a large dataframe API.

The core vocabulary includes ideas such as:

```text
filter
select
extend
sort
groupby
lookup
```

These operations should combine naturally.

```calc
filter(
    shipments,
    and(
        [date] >= soyear(asof),
        [date] <= asof
    )
)
```

Row context remains explicit:

```calc
[amount]
```

means the value in the current row.

```calc
shipments::amount
```

means the whole column.

There should be no hidden evaluation context.

---

# Collections inherit scalar meaning

Calc should have one arithmetic system.

If Calc knows:

```text
currency * int → currency
```

then:

```calc
array($10, $20, $30) * 2
```

should naturally produce:

```text
array{currency}
```

The collection should not invent new arithmetic rules.

Arrays, matrices, and columns should lift existing scalar semantics where that behavior is explicitly allowed.

This keeps the language coherent:

> **Define meaning once, reuse it everywhere.**

---

# Domain awareness without becoming a domain silo

Calc begins with operational workflows, but it should not hard-code one industry.

The engine should make it inexpensive to introduce meaningful domain values and functions when a workflow requires them.

Examples might include:

* ISO container numbers
* vessel identifiers
* ports
* currencies and exchange rates
* weights and measurements
* public-holiday calendars
* business days
* country codes
* voyage references
* product or commodity codes

The standard library can grow from real use.

Calc should avoid speculative domain types that provide no validation or semantic value.

---

# Import should be strict

Operational errors often enter a workflow during import.

Typed import is therefore part of Calc's safety model, not merely an I/O feature.

Given:

```text
row 184
container = MSCU1234567
```

Calc should be able to say:

```text
row 184, column "container":
invalid ISO container check digit
```

rather than silently treating the value as generic text.

Likewise, a malformed currency, date, percentage, or quantity should produce a diagnostic that identifies:

* the row,
* the column,
* the raw input,
* the expected type,
* the reason validation failed.

Loading data is the first calculation.

It deserves the same rigor as every calculation afterward.

---

# Errors are part of the language

Calc should optimize errors for understanding, not implementation convenience.

An error should answer:

1. What failed?
2. Where did it fail?
3. What did Calc expect?
4. What did Calc receive?

Source positions should be preserved whenever possible so an editor can point directly to the offending expression.

Internal exceptions such as:

```text
IndexError
KeyError
InvalidOperation
ValueError
```

should not leak through the language boundary when the problem can be expressed as an `ExpressionError`.

---

# One engine

Calc should have one semantic implementation.

```text
src/engine/
```

owns the language.

The CLI, notebook, editor, future LSP, web application, and other interfaces should call that engine rather than reproduce its behavior.

A date rule should not behave differently in the editor and CLI.

A container number should not validate differently during CSV import and expression evaluation.

A type error should not depend on the user interface.

> **Fix semantics once.**

---

# One language

Calc expressions and Calc scripts should be the same language.

Simple calculations:

```calc
$100 * 20%
```

table transformations:

```calc
extend(t, "total", [price] * [qty])
```

and scripts:

```calc
let filtered = filter(t, [date] <= asof);
let totals = groupby(filtered, "port", "amount", "sum");

totals
```

should all compose from the same semantics.

Future functions and modules should extend this language rather than introducing a second scripting model.

---

# Determinism matters

Operational calculations should be reproducible.

Given the same:

* source,
* input data,
* evaluation context,
* Calc version,

the result should be the same.

Functions such as:

```calc
today()
now()
```

should eventually use an evaluation clock so one execution has one consistent notion of time.

External state should enter Calc explicitly.

Hidden state should be minimized.

---

# The first product

Calc does not need to become a universal programming language before it becomes useful.

The first real product is simpler:

> **Replace one important spreadsheet workflow from input to output.**

That means:

```text
CSV / operational data
        ↓
typed import
        ↓
validation
        ↓
lookup
        ↓
filter
        ↓
extend
        ↓
aggregate
        ↓
export
```

with enough diagnostics that a user can trust the result and understand failures without opening Python.

If Calc can repeatedly replace one fragile operational workbook with a typed, auditable workflow, the core idea is proven.

---

# What success looks like

Calc succeeds when a user can look at this:

```calc
let shipments = load(...);

let valid = filter(
    shipments,
    and(
        [date] <= asof,
        not(public_holiday([date]))
    )
);

groupby(
    valid,
    "port",
    "amount",
    "sum"
)
```

and understand both:

* what the calculation does, and
* what kinds of values it operates on.

Success means fewer assumptions live only in someone's head.

Success means malformed data fails close to where it enters the workflow.

Success means a calculation cannot casually turn currency into a dimensionless number.

Success means business logic can move from fragile workbooks into readable, version-controlled Calc code.

---

# What Calc is not

Calc is not trying to become:

* another general spreadsheet grid
* Python with different syntax
* pandas with unit literals
* SQL with a calculator attached
* a dynamically coerced scripting language
* a general-purpose programming language
* a system that silently repairs malformed data
* a giant catalogue of business-specific functions
* a performance project before correctness is established

Calc should be small enough that its semantics can be understood.

---

# Design principles

## Meaning before convenience

A short expression with ambiguous semantics is worse than a slightly longer expression whose meaning is clear.

## Correctness before breadth

A small set of trustworthy operations is more valuable than a large set of partially defined operations.

## Explicit over implicit

Implicit type coercion should be rare.

Invisible evaluation context should not exist.

Domain assumptions should be expressed rather than inferred.

## Composition over feature count

Prefer a small vocabulary that composes well.

```text
filter
extend
lookup
groupby
```

is more valuable than dozens of narrowly specialized table operations.

## Domain types must earn their place

A first-class type should validate, constrain, compute, compare, or format something meaningful.

## Static knowledge is valuable

If Calc can know an expression is invalid before evaluating it, it should reject it before evaluation.

## Runtime failures should remain clear

Type-safe does not mean every runtime value is valid.

Division by zero, invalid calendar results, bad checksums, and out-of-range operations still fail.

They should fail deliberately.

## Tests define the contract

Whenever undefined behavior is discovered and resolved, add a regression test.

The language should gradually become boring.

That is success.

---

# Long-term direction

Once the core workflow is proven, Calc can grow outward.

Possible future capabilities include:

* user-defined functions
* reusable `.calc` modules
* richer business calendars
* lookup and relational relationships
* additional units
* rate types such as currency-per-tonne
* typed persistence formats
* LSP diagnostics and completion
* CLI automation
* richer table interfaces
* domain libraries
* workflow applications built on the engine

These should remain consequences of successful workflows, not prerequisites for them.

---

# The vision

Calc should become a place where operational logic can be written plainly without giving up the guarantees normally associated with more formal systems.

It should sit in the space between a spreadsheet and a programming language:

* easier to read than application code,
* stricter than a spreadsheet,
* more domain-aware than a dataframe library,
* smaller than a general-purpose language.

The goal is not to make calculations clever.

The goal is to make them trustworthy.

> **Calc gives operational data types, gives calculations explicit meaning, and refuses to guess when the meaning is unclear.**
