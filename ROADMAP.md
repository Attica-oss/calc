# Roadmap

Calc is a typed expression and data language for operational work.

Its core promise is simple:

> **Calc does not guess what values mean.**

A tonnage cannot silently become a plain number. Currency arithmetic stays exact. Dates, durations, percentages, container numbers, tables, arrays, and matrices retain their types through computation.

```text
calc> 15t + 1
error: '+' is not defined for a tonnage and a whole number.
    15t + 1
        ^
```

That refusal to guess is the product.

Calc is not trying to replace Excel or Google Sheets as a general-purpose grid. It is building a safer way to load operational data, calculate with it, transform it, validate it, and eventually automate repeatable workflows using one typed language.

---

## Guiding decisions

### One workflow first

The first milestone is not broad adoption.

It is replacing one real operational spreadsheet workflow end to end:

```text
data in
    ↓
typed values
    ↓
validate
    ↓
filter / lookup / transform
    ↓
aggregate
    ↓
data out
```

Catch reports, sales, tonnage, currencies, dates, container numbers, and similar operational data are the initial proving ground.

Other industries and personas come later through reusable domain types and functions.

### One language everywhere

Expressions, calculated columns, row expressions, scripts, and future modules use the same Calc DSL.

There will not be a second imperative scripting language bolted onto the engine.

The language computes values. The host application decides what to do with them.

### Explicit over implicit

Ambiguity is a type error.

Calc does not silently coerce incompatible types.

```text
$10 + 5
15t + 2
2026-01-01 + 3
```

should fail unless Calc has an explicit rule for the operation.

Collection broadcasting is allowed only where it is part of the language's defined operator semantics:

```text
array(1, 2, 3) * 10
```

is valid because scalar-to-collection arithmetic broadcasting is an explicit Calc rule.

This is different from implicit type coercion.

### Row context is lexical

There is no invisible DAX-style evaluation context.

Inside row-scoped operations:

```text
[unit_price]
```

means the current row's value.

```text
t::unit_price
```

means the whole column.

That distinction remains visible in the language.

### The engine is the source of truth

`src/engine/` owns:

* values and types
* lexer
* parser and AST
* static type checking
* operator dispatch
* function registry
* evaluation
* formatting
* calendar semantics
* casts

Every surface should call the engine rather than reimplement Calc semantics.

A fix should land once.

---

# Current baseline

Calc is no longer at the "expression evaluator prototype" stage.

The following are part of the current foundation.

## Typed scalar values

Calc supports typed scalar values including:

* whole numbers
* decimals
* booleans
* text
* characters
* dates
* times
* datetimes
* durations
* currency
* tonnage
* percentages
* complex numbers
* blank values
* ISO container numbers

Domain values may enforce their own invariants. For example, an ISO container number is not merely text: it carries its structure and validates its check digit.

## Structural types

Calc's `Type` system supports both flat and structured categories.

Examples:

```text
int
currency
container

array{decimal}
matrix{int}
column{unit_price: currency}
table{date: date, amount: currency}
```

Flat types remain compatible with the dispatch tables, while compound types carry schema information that the checker can inspect.

The goal is no longer to replace every category string with a wrapper object.

The goal is:

> **Every value has enough static type information for Calc to reject invalid operations before evaluation.**

## Collections

Calc has native:

* `Array`
* `Matrix`
* `Column`
* `Table`

Arrays and matrices are homogeneous.

Columns are named homogeneous vectors.

Tables have a typed schema.

Arithmetic over collections is derived from the scalar operator rules instead of maintaining a second arithmetic system.

## Table expressions

Calc already has the foundations of a relational expression language:

* `filter`
* `select`
* `extend`
* `sort`
* `groupby`
* aggregations
* lexical `[column]` row references
* `table::column` access

These should remain small, composable primitives rather than growing into a large catalogue of overlapping dataframe operations.

## Basic scripting

Calc supports scripts containing `let` bindings and a final expression.

```calc
let rate = $42;
let tonnes = 10t;

rate * tonnes
```

This is the beginning of the scripting model, not a separate language.

## Domain and time intelligence

The engine is beginning to support domain-aware operations directly rather than forcing users to encode everything as text and numbers.

Examples include:

* calendar month/quarter/year boundaries
* date and time operations
* public-holiday logic
* ISO container numbers
* typed quantities

This direction is important to Calc's differentiation and should remain driven by real workflows rather than by adding domain types speculatively.

## Development workbench

Calc already has interactive development surfaces around the engine, including a CodeMirror-based editor and notebook/workbench integrations.

These are useful development and testing surfaces.

They should not yet dictate the engine architecture.

---

# Milestone 1 — Make the language boring

The immediate priority is semantic stability.

Before expanding the language substantially, Calc should become predictable enough that existing behavior can be treated as a contract.

## Testing and CI

* [ ] Run `pytest` on every push
* [ ] Run `ruff` on every push
* [ ] Run `ty check` on every push
* [ ] Remove overlapping static-analysis tooling where it provides no additional value
* [ ] Add regression tests for every bug fixed in the engine
* [ ] Keep UI/workbench tests separate from language-semantics tests

**Exit:** a change cannot merge when tests, linting, or static typing fail.

## Define calendar arithmetic

Calendar arithmetic needs an explicit contract.

Questions such as:

```text
2026-01-31 + 1mo
```

and:

```text
(2026-01-31 + 1mo) + 1mo
```

must have deliberate semantics rather than inheriting accidental behavior from Python date operations.

* [ ] Specify month-addition behavior
* [ ] Specify end-of-month behavior
* [ ] Decide whether month arithmetic preserves an original day anchor
* [ ] Define subtraction semantics consistently for `date`, `datetime`, and `time`
* [ ] Add property/regression tests for calendar boundaries

## Stabilize collection semantics

* [ ] Preserve type agreement between checker and evaluator
* [ ] Ensure array/matrix/column lifting follows the same scalar dispatch rules
* [ ] Keep columns separate from anonymous array/matrix lifting where appropriate
* [ ] Reject unsupported column/array/matrix combinations rather than guessing
* [ ] Define naming semantics for derived columns
* [ ] Ensure unary and binary collection operations are consistent
* [ ] Keep shape/length mismatches as explicit runtime errors

## Stabilize table row semantics

* [ ] Require `extend()` row expressions to return scalar values
* [ ] Prevent nested `Column` objects from accidentally becoming table cells
* [ ] Keep `[column]` as the lexical row-value syntax
* [ ] Keep `table::column` as whole-column access
* [ ] Test row scope against same-named outer variables
* [ ] Test empty tables consistently across every table verb

## Define aggregate semantics

Explicitly decide what happens for empty collections.

For example:

```text
sum(empty)
avg(empty)
min(empty)
max(empty)
```

These should not inherit accidental Python behavior.

* [ ] Document each aggregate's empty-input behavior
* [ ] Ensure runtime behavior matches its static result type
* [ ] Add empty-column and empty-filter regression tests

## Finish language syntax cleanup

* [ ] Finalize `//` comment versus floor-division rules
* [ ] Keep lexer and CodeMirror tokenization behavior aligned
* [ ] Add lexer/parser tests for comments
* [ ] Add lexer/parser tests for domain literals such as container numbers
* [ ] Keep syntax highlighting semantic-free: highlighting recognizes shape, the engine validates meaning

**Exit:** Calc's current syntax and semantics are documented by tests strongly enough that new features do not repeatedly expose undefined behavior in old ones.

---

# Milestone 2 — Complete the typed table workflow

The next milestone is an end-to-end operational data pipeline.

The table machinery largely exists. The work now is completing the workflow around it.

## Typed import

* [ ] Define a stable CSV import API
* [ ] Allow explicit column schemas
* [ ] Infer types only when inference rules are deterministic
* [ ] Report row, column, raw value, and expected type on import failure
* [ ] Decide how explicit blank/null import works
* [ ] Support domain types such as container numbers during import
* [ ] Keep imported engine tables independent of pandas/Polars runtime semantics

The import experience is part of the product.

A bad value should produce an error such as:

```text
row 184, column "container":
  "MSCU1234567" has an invalid ISO 6346 check digit
```

rather than silently becoming text.

## Lookup and joins

`lookup` is the largest missing table primitive.

* [ ] Define one-to-one lookup
* [ ] Define missing-key behavior
* [ ] Define duplicate-key behavior
* [ ] Decide whether one-to-many lookup belongs in the first implementation
* [ ] Preserve schemas through lookup
* [ ] Reject incompatible key types
* [ ] Add clear diagnostics for duplicate and missing keys

Do not add multiple join APIs until one explicit lookup model proves insufficient.

## Export

* [ ] CSV export
* [ ] Stable formatting for domain values
* [ ] Define blank serialization
* [ ] Define date/time serialization
* [ ] Define quantity serialization
* [ ] Preserve enough schema metadata for reliable re-import where possible

## End-to-end workflow

The milestone that matters:

```text
CSV
 ↓
typed Table
 ↓
validate
 ↓
lookup
 ↓
filter
 ↓
extend
 ↓
groupby
 ↓
export
```

**Exit:** one real spreadsheet reconciliation workflow can be replaced completely using Calc.

At that point Calc is a product rather than only a language project.

---

# Milestone 3 — Domain-aware operational data

Calc should make common operational concepts safer by representing them as values rather than conventions encoded in text.

This work should be demand-driven.

## Existing direction

Examples already showing the model:

```text
$125.00            currency
15.250 t           tonnage
12.5%              percent
2mo 3d             duration
BICU1234565        container
2026-08-19         date
```

## Candidate domain types

Only add these when a real workflow needs them:

* vessel identifiers
* ports / UN LOCODE
* currencies and exchange rates
* weights and additional measurement units
* countries
* voyage identifiers
* product/species codes
* business calendars

A domain type should exist when it provides at least one of:

1. validation,
2. safer arithmetic,
3. safer comparison,
4. canonical formatting,
5. domain-specific functions.

Do not create wrapper types solely for visual labeling.

## Calendars

Public-holiday and business-day logic should evolve into a coherent calendar model.

Possible direction:

```calc
public_holiday(date)
business_day(date)
next_business_day(date)
business_days_between(a, b)
```

Before adding many functions:

* [ ] Decide how calendars are selected
* [ ] Keep holiday definitions deterministic
* [ ] Separate recurring rules from one-time holidays
* [ ] Test calendar changes by year
* [ ] Decide how jurisdiction-specific calendars are represented

**Exit:** the first operational workflow can express its important domain rules directly in Calc instead of embedding them in spreadsheet conventions.

---

# Milestone 4 — Real workflow, real users

Do not broaden Calc before observing it on messy data.

The goal is a small number of real users performing the target workflow themselves.

Questions to answer:

* Do typed failures feel protective or obstructive?
* Which import errors happen repeatedly?
* Is `filter/select/extend/lookup/groupby/sort` enough?
* Which domain values actually deserve first-class types?
* Are users comfortable with lexical `[column]` syntax?
* Which errors are understandable without knowing Calc internals?
* What parts of the workflow still require Python or spreadsheet cleanup?

Use those observations to improve Milestones 1–3 before adding major new language surface.

**Exit:** at least one workflow is repeatedly completed with Calc using real operational data, not only fixtures and examples.

---

# Milestone 5 — Functions and modules

Basic `let` scripting already establishes the model.

The next language step is reusable computation.

## User-defined functions

Target shape:

```calc
fn landed_cost(price: currency, freight: currency) -> currency =
    price + freight;
```

Exact syntax is still open.

Requirements:

* [ ] typed parameters
* [ ] statically checked body
* [ ] statically known return type
* [ ] clear arity/type errors
* [ ] same function registry model as built-ins where practical
* [ ] recursion only if it can be bounded safely
* [ ] closures deferred until there is a demonstrated need

## Modules

* [ ] load Calc files
* [ ] module-local bindings
* [ ] exported values/functions
* [ ] deterministic import resolution
* [ ] no implicit filesystem mutation from the language
* [ ] dependency-cycle diagnostics

## Determinism

`today()` and `now()` should be stable within one evaluation pass.

* [ ] evaluation clock/context
* [ ] same clock shared through a script/table calculation
* [ ] deterministic tests using an injected clock

## Language specification

Once user modules exist, syntax becomes a compatibility surface.

Before then:

* [ ] document literals
* [ ] document operators and precedence
* [ ] document types
* [ ] document collection semantics
* [ ] document table row scope
* [ ] document casts
* [ ] document error guarantees

Then establish:

* [ ] compatibility policy
* [ ] deprecation policy
* [ ] versioning strategy

**Exit:** useful business logic can live in version-controlled `.calc` files without Python.

---

# Product surfaces

Surfaces should expose the engine, not redefine it.

There is no need to build them in a rigid sequence.

Prioritize based on actual use.

## Development workbench

Continue using the current interactive editor/notebook surface for:

* syntax experimentation
* result inspection
* CSV exploration
* error UX
* language demos

It is a workbench, not a second engine.

## CLI / REPL

The CLI should remain capable of running the complete workflow without a graphical interface.

Target:

```text
calc script.calc input.csv > output.csv
```

or an equivalent explicit interface.

## LSP

Calc already tracks source positions and performs static type checking, making editor diagnostics a natural extension.

Potential capabilities:

* diagnostics
* hover types
* function signatures
* completion
* go-to definition for module bindings
* formatting

## Table UI

A richer grid or web application should be built when it makes the proven workflow substantially easier.

The UI should visualize Calc types and errors rather than hiding them.

---

# Later / open questions

These are intentionally not near-term commitments.

* user-defined units
* dimensional unit algebra
* richer rate types such as currency-per-tonne
* persistence format beyond CSV
* nested tables
* records/structs
* one-to-many relationships
* richer matrix operations
* optimization/vectorized execution
* package ecosystem for domain types/functions
* open-core/commercial sustainability
* collaboration/multi-user workflows

Performance work remains demand-driven.

Profile first.

---

# Non-goals

Calc is not trying to become:

* a general replacement for Excel or Google Sheets
* a freeform spreadsheet cell grid
* pandas with different syntax
* SQL with unit literals
* a dynamically coerced scripting language
* a second Python
* a language with invisible evaluation context
* a system that guesses what malformed operational data meant
* a performance project before correctness is established

Calc should continue rejecting implicit type coercion.

Convenient syntax is welcome.

Ambiguous semantics are not.

---

# Near-term priority

If there is uncertainty about what to build next, work in this order:

1. **Semantic correctness**
2. **Tests and static checking**
3. **Typed import diagnostics**
4. **Lookup**
5. **Export**
6. **One real end-to-end workflow**
7. **Domain features required by that workflow**
8. **User-defined functions and modules**
9. **Additional product surfaces**
10. **Everything else**

The core rule remains:

> **Do not widen Calc until the existing language can be trusted.**
