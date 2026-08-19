# Extending the expression engine

Calc's engine has a small number of extension points:

```text
source
  ↓
tokenize
  ↓
parse → AST
  ↓
check_types
  ↓
evaluate
  ↓
format
```

The most important invariant is:

> **The type checker and evaluator must resolve the same operation and agree on its result type.**

If the checker determines:

```text
X OP Y → Z
```

then evaluation must either:

1. produce a runtime value whose category is `Z`, or
2. raise an `ExpressionError`.

It must not silently return a different kind of value.

For operators and casts, both checking and evaluation use shared dispatch rules. Collection arithmetic derives its behavior from scalar arithmetic wherever collection lifting permits it.

The general extension model is:

* new function → add a `FunctionSpec`
* new scalar operator behavior → register a binary or unary rule
* new cast → register a cast rule
* new scalar arithmetic behavior → arrays, matrices, and columns usually inherit it automatically
* new runtime value → representation + category + semantics + formatting
* new literal syntax → lexer + parser + value representation

Avoid adding parallel special cases directly to `check_types` and `evaluate_node` unless the feature genuinely cannot be represented through the existing registries or resolvers.

---

# Result-type correctness

This rule applies to every extension point.

Suppose the checker resolves:

```text
decimal - decimal → decimal
```

The runtime implementation must not return:

```text
Blank()
```

or:

```text
int
```

because those values disagree with the result category promised by the checker.

The same rule applies to:

* functions
* binary operators
* unary operators
* casts
* array lifting
* matrix lifting
* column lifting

A value-dependent operation may still fail at runtime.

For example:

```text
1 / 0
```

is valid by category:

```text
int / int → decimal
```

but invalid for those particular values.

That distinction should remain clear:

```text
unsupported categories
    → type-check error

supported categories + invalid runtime values
    → runtime ExpressionError
```

---

# Adding a function

Functions are represented by `FunctionSpec`.

Conceptually:

```python
@dataclass(frozen=True)
class FunctionSpec:
    name: str
    min_args: int
    max_args: int | None
    lazy: bool
    result_type: Callable
    impl: Callable
    row_scope_arg: int | None = None
```

The two important pieces are:

```text
result_type(categories, node)
```

which runs during static type checking, and:

```text
impl(values)
```

which runs during evaluation.

For lazy functions, the implementation receives unevaluated argument nodes and an evaluation callback instead.

---

## Current registration style

At present, built-in functions are added to `FUNCTIONS` using `FunctionSpec`.

For example:

```python
def _example_result(categories, node):
    if categories[0] != "decimal":
        _fail(
            node,
            "example() requires a decimal.",
        )

    return "decimal"


def _example_impl(values):
    [value] = values
    return value


FUNCTIONS = {
    ...
    "example": FunctionSpec(
        "example",
        1,
        1,
        False,
        _example_result,
        _example_impl,
    ),
}
```

The argument count is enforced by `FunctionSpec`.

The result resolver should therefore focus on type semantics rather than repeating arity validation.

---

## Fixed result types

Many functions always return the same category.

Calc already uses a reusable helper for this pattern:

```python
def _fixed(category):
    return lambda categories, node: category
```

A zero-argument function can therefore use:

```python
"today": FunctionSpec(
    "today",
    0,
    0,
    False,
    _fixed("date"),
    _today_impl,
)
```

There is no need to write a dedicated result resolver merely to return a constant category.

---

## Functions restricted to particular categories

When a function has simple type rules, keep the result resolver simple.

For example:

```python
def _example_result(categories, node):
    category = categories[0]

    if category not in {
        "int",
        "decimal",
    }:
        _fail(
            node,
            "example() requires a number.",
        )

    return category
```

Do not force a generic helper onto a function whose semantics contain important exceptions.

For example, `abs()` is not a pure same-type function:

```text
abs(int)      → int
abs(decimal)  → decimal
abs(currency) → currency
abs(complex)  → decimal
```

The `complex → decimal` case means `abs()` deserves a custom resolver.

---

## Example: `ceil()`

`ceil(x, multiple)` needs a custom result resolver because its result depends on both arguments.

```python
def _ceil_result(categories, node):
    x_category, multiple_category = categories

    if x_category != multiple_category:
        _fail(
            node,
            "ceil() requires x and multiple to be the same kind of value.",
        )

    if x_category in {"int", "decimal"}:
        return x_category

    if x_category in {
        "currency",
        "tonnage",
        "percent",
        "duration",
    }:
        return x_category

    _fail(
        node,
        "ceil() accepts numbers, quantities, or durations.",
    )
```

Its implementation then only needs to choose the runtime operation:

```python
def _ceil_impl(values):
    x, multiple = values

    if isinstance(x, Duration):
        return _ceil_duration(x, multiple)

    if isinstance(x, Quantity):
        return _ceil_quantity(x, multiple)

    return _ceil_number(x, multiple)
```

The evaluator does not need to repeat the category validation.

The checker already performed it.

---

# Lazy functions

A function should be lazy only when evaluating all arguments would change its semantics.

`if()` is the canonical example:

```text
if(x <> 0, 1 / x, 0)
```

When `x` is zero, the unused branch must not be evaluated.

A lazy function receives the argument nodes rather than already evaluated values:

```python
def _if_impl(
    args,
    environment,
    evaluate,
    row_scope,
):
    condition = evaluate(
        args[0],
        environment,
        row_scope,
    )

    chosen = args[1] if condition else args[2]

    return evaluate(
        chosen,
        environment,
        row_scope,
    )
```

Lazy evaluation is a language semantic.

Do not use it merely as a performance optimization.

---

# Row-scoped functions

Some table functions evaluate one argument against the current row.

For example:

```calc
filter(t, [qty] > 2t)
```

The expression:

```text
[qty]
```

is resolved using the schema of `t`.

These functions use `row_scope_arg`.

For example:

```python
"filter": FunctionSpec(
    "filter",
    2,
    2,
    True,
    _filter_result,
    _filter_impl,
    row_scope_arg=1,
)
```

The checker derives a row scope from argument `0` and uses it while checking argument `1`.

The evaluator performs the same operation once per table row.

Keep `row_scope_arg` limited to arguments that genuinely contain row expressions.

Functions such as:

```calc
select(t, "name", "qty")
```

do not need row scope when their column arguments are compile-time text names.

---

# Row values versus whole columns

Calc deliberately distinguishes:

```calc
[unit_price]
```

from:

```calc
t::unit_price
```

Inside a row-scoped function:

```text
[unit_price]
```

means:

> the current row's scalar value.

Whereas:

```text
t::unit_price
```

means:

> the entire `Column`.

For example:

```calc
extend(
    t,
    "total",
    [unit_price] * 10
)
```

produces a scalar value for each row.

Using:

```calc
extend(
    t,
    "total",
    t::unit_price * 10
)
```

produces a whole-column expression instead.

Row-scoped functions should reject collection-valued row expressions where a scalar result is required.

---

# Adding a constant

Calc currently represents constants as zero-argument functions.

Examples include:

```calc
pi()
e()
infinity()
today()
now()
```

This avoids creating reserved bare identifiers.

A user may therefore still write:

```calc
let pi = 3;

pi
```

without conflicting with:

```calc
pi()
```

Function names use lowercase call syntax.

---

# Adding arithmetic

Arithmetic is defined for scalar categories first.

Example:

```python
register_binary(
    "-",
    "time",
    "time",
    "duration",
    time_subtract,
)
```

That rule means:

```text
time - time → duration
```

Do not separately register:

```text
array{time} - array{time}
matrix{time} - matrix{time}
column{...: time} - column{...: time}
```

when the operation should use normal element-wise lifting.

The collection resolver derives those operations from the scalar rule.

---

# Adding a binary operator rule

Use:

```python
register_binary(
    op,
    left_category,
    right_category,
    result_category,
    impl,
)
```

Example:

```python
register_binary(
    "+",
    "complex",
    "complex",
    "complex",
    complex_add,
)
```

Some registrations accept the alias:

```text
number
```

which expands to:

```text
int
decimal
```

For example:

```python
register_binary(
    "+",
    "number",
    "number",
    numeric_result,
    numeric_add,
)
```

---

## Symmetric operations

Use:

```python
symmetric=True
```

only when reversing the operands produces the same semantic operation.

Good candidates:

```text
number * percent
percent * number
```

or:

```text
date + duration
duration + date
```

when the implementation deliberately defines them as equivalent.

Do not use symmetry for:

```text
a - b
a / b
```

just to reduce registrations.

Operand order is part of those operations.

---

# Adding a unary operator rule

Unary operations use the corresponding unary registry.

Example:

```python
register_unary(
    "-",
    "duration",
    "duration",
    negate_duration,
)
```

Where collection lifting permits it:

```text
-array{duration}
```

inherits the scalar rule and becomes element-wise duration negation.

---

# Collection lifting

Arrays and matrices use compound types such as:

```text
array{int}
array{currency}
matrix{decimal}
```

A compound type is intentionally distinct from the flat string:

```text
"array"
```

Therefore this is not how array arithmetic is defined:

```python
register_binary(
    "+",
    "array",
    "array",
    ...
)
```

Such a rule would not match:

```text
array{int}
```

or:

```text
array{currency}
```

Instead, Calc lifts scalar arithmetic.

Given:

```text
array{X} OP array{Y}
```

the resolver looks up:

```text
X OP Y
```

and applies that implementation element-wise.

For example:

```text
array{int} / array{int}
```

uses:

```text
int / int → decimal
```

and therefore produces:

```text
array{decimal}
```

Likewise:

```text
array{currency} * int
```

broadcasts the scalar operand and applies:

```text
currency * int → currency
```

to every element.

The central rule is:

> **Collections do not invent scalar arithmetic semantics. They inherit the semantics of their elements.**

---

# Which operators are lifted

Only explicitly approved arithmetic operators are lifted.

For binary arithmetic:

```python
ELEMENTWISE_BINARY_OPS = frozenset({
    "+",
    "-",
    "*",
    "/",
    "//",
    "%",
    "**",
})
```

For unary arithmetic:

```python
ELEMENTWISE_UNARY_OPS = frozenset({
    "+",
    "-",
})
```

Comparisons are intentionally excluded.

For scalar values:

```text
a = b
```

returns:

```text
boolean
```

Automatically lifting equality would make:

```text
array = array
```

return:

```text
array{boolean}
```

which is a different semantic idea from ordinary language-level equality.

Collection comparison semantics should therefore be introduced explicitly rather than inherited accidentally.

---

# Arrays and matrices

Arrays and matrices are anonymous homogeneous collections.

Their element type is represented using an unnamed field:

```python
Type(
    "array",
    fields=((None, element_type),),
)
```

and:

```python
Type(
    "matrix",
    fields=((None, element_type),),
)
```

They share the scalar lifting model but retain distinct collection semantics.

For example:

```text
array + matrix
```

does not automatically mean anything.

Do not invent cross-collection broadcasting rules unless the language explicitly defines them.

---

# Columns

Columns are homogeneous typed collections with names.

A source column may have a type such as:

```text
column{time_in: time}
```

Operator compatibility depends on:

```text
time
```

not on the field name:

```text
time_in
```

Therefore:

```text
column{time_out: time}
-
column{time_in: time}
```

resolves the scalar operation:

```text
time - time → duration
```

The current column lifting implementation preserves a source column name for the derived result.

For example, with the left-hand column providing the name:

```text
column{time_out: time}
-
column{time_in: time}

→

column{time_out: duration}
```

The field name is metadata.

It must not determine whether the arithmetic rule is valid.

If Calc later adopts anonymous derived columns, that should be an explicit language change rather than documentation assuming behavior the engine does not yet implement.

---

# Adding a cast

Casts have their own registry.

Conceptually:

```python
register_cast(
    source_category,
    target,
    result_category,
    impl,
)
```

Example:

```python
register_cast(
    "datetime",
    "date",
    "date",
    lambda value: value.date(),
)
```

An absent cast rule is a type error.

Do not add a second rejection path elsewhere unless the cast semantics genuinely require it.

When adding a cast, decide deliberately:

1. What does the conversion mean?
2. What result category does it produce?
3. Does it round, truncate, normalize, or reject?
4. Can it fail at runtime?
5. Does the reverse cast exist?
6. If both directions exist, should they round-trip?

For example:

```text
5%::DECIMAL
→
0.05
```

and:

```text
0.05::PERCENT
→
5%
```

form a sensible pair.

---

# Adding a new value type

A genuinely new Calc value requires several coordinated decisions.

`ContainerNumber` is a useful example because it is:

* a scalar
* structured internally
* validated
* represented by native literal syntax
* formatted canonically
* distinct from ordinary text

---

## 1. Runtime representation

Prefer an immutable value object.

```python
@dataclass(frozen=True)
class YourType:
    ...
```

Examples in Calc include:

* `Duration`
* `Quantity`
* `Complex`
* `Blank`
* `Char`
* `ContainerNumber`
* `Array`
* `Matrix`
* `Column`
* `Table`

Use `__post_init__` when the representation has invariants that should always hold.

For example, `ContainerNumber` validates its ISO container-number structure and check digit when it is constructed.

A value object should not be able to exist in an invalid internal state.

---

## 2. Add it to `Value`

Every runtime value must be represented by the `Value` type alias.

For example:

```python
type Value = (
    Number
    | bool
    | str
    | Temporal
    | Duration
    | Quantity
    | Complex
    | Blank
    | Char
    | ContainerNumber
    | Column
    | Table
    | Array
    | Matrix
)
```

Forgetting this step causes static typing to disagree with the actual runtime domain.

---

## 3. Define its category

Add it to `category_of()`.

Use a `Type`:

```python
if isinstance(value, YourType):
    return Type("your_type")
```

For example:

```python
if isinstance(value, ContainerNumber):
    return Type("container")
```

Also add a human-readable diagnostic label:

```python
CATEGORY_LABELS = {
    ...
    "container": "a container number",
}
```

---

## 4. Decide whether it is scalar or compound

Do not use `Type.fields` merely because a runtime object contains several Python fields.

For example, `ContainerNumber` internally contains:

```text
owner code
equipment category
serial number
check digit
```

but it is still one scalar Calc value:

```text
container
```

It should therefore use:

```python
Type("container")
```

not:

```python
Type(
    "container",
    fields=(...),
)
```

`Type.fields` is for static structural information that matters to Calc's type system, such as:

```text
array{decimal}
matrix{int}
column{price: currency}
table{date: date, amount: currency}
```

The internal fields of a scalar dataclass do not automatically belong in the static type schema.

---

## 5. Literal syntax

Only add lexer/parser support when the type has native literal syntax.

For example:

```calc
BICU1234565
```

is a container-number literal.

That requires:

```text
lexer
  ↓
CONTAINER token
  ↓
parser
  ↓
ContainerNumber(...)
```

The lexer should validate lexical shape.

The runtime value should validate semantic invariants.

For example, a regex can recognize:

```text
3 letters
+ U/J/Z
+ 7 digits
```

while `ContainerNumber` validates the check digit.

Do not put semantic validation into syntax highlighting.

---

## 6. Lexer ordering

Token order matters.

A specific literal must usually appear before a more general token that could consume the same text.

For example:

```text
BICU1234565
```

also matches Calc's general identifier shape.

Therefore:

```text
CONTAINER
```

must be attempted before:

```text
IDENTIFIER
```

The same principle applies to existing tokens such as:

```text
DATETIME before DATE
POWER before MULTIPLY
<= before <
```

---

## 7. Parser construction

The parser should convert the token into its actual runtime value.

For example:

```python
if token.kind == "CONTAINER":
    self.advance()

    return Literal(
        value=parse_container_literal(
            token.value,
            token.position,
        ),
        position=token.position,
    )
```

The parser should not leave a native literal represented as arbitrary text when Calc has a dedicated runtime value for it.

---

## 8. Editor highlighting

If the development editor mirrors the Calc lexer, add the literal there too.

The editor should recognize the same lexical shape as the engine.

For example:

```js
const CONTAINER =
  /^[A-Za-z]{3}[UuJjZz]\d{7}(?![A-Za-z0-9_])/;
```

and recognize it before general identifiers.

Highlighting should only recognize the token's shape.

The Python engine remains responsible for semantic validation such as a container check digit.

---

## 9. Comparison semantics

Choose deliberately.

### Fully ordered

Support:

```text
=
<>
<
<=
>
>=
```

only when the type has a meaningful total ordering.

### Equality only

Support:

```text
=
<>
```

when identity/equality makes sense but ordering does not.

### No comparison

Register nothing.

An absent operator rule is normally preferable to inventing semantics.

Also remember:

> Python/dataclass equality is not automatically Calc-language equality.

A frozen dataclass may support:

```python
a == b
```

inside Python.

That does not automatically make this valid Calc syntax:

```calc
a = b
```

The Calc comparison operator must still be registered.

---

## 10. Arithmetic semantics

Register only operations with a clear meaning.

For example, a future IP-address type might support:

```text
IP + int → IP
IP - int → IP
IP - IP  → int
```

while deliberately rejecting:

```text
IP + IP
IP * int
IP / int
```

Leaving an operation unregistered is not an incomplete implementation.

It is how Calc expresses:

> this operation has no defined meaning.

---

## 11. Cast semantics

Decide whether the new type should convert to or from existing categories.

For example, a domain value might sensibly support:

```text
container::TEXT
```

while rejecting:

```text
container::INT
```

Do not add casts simply because their Python representation makes a conversion technically possible.

A cast should have meaningful Calc semantics.

---

## 12. Formatting

Every runtime value needs display formatting.

Type-specific formatting should happen before generic fallbacks.

For example:

```python
if isinstance(value, ContainerNumber):
    return format_container(value)
```

and:

```python
def format_container(
    value: ContainerNumber,
) -> str:
    return str(value)
```

Composite formatters should reuse `format_result()` for their contained values.

That means a new scalar formatter automatically works inside:

* arrays
* matrices
* columns
* tables

without duplicating its formatting logic.

---

# Constants versus literal types

Not every special value needs literal syntax.

For example:

```calc
blank()
```

is constructed through a function and therefore requires no token.

Likewise:

```calc
pi()
```

is a zero-argument function.

A native token should be added only when direct literal syntax materially improves the language.

---

# Runtime failures

Some operations are valid by type but invalid for particular values.

Examples include:

```text
1 / 0
```

or:

```text
2026-01-31 + an unsupported calendar operation
```

or constructing a domain value with an invalid checksum.

These should raise:

```python
ExpressionError
```

rather than leaking implementation exceptions such as:

```text
ValueError
InvalidOperation
IndexError
KeyError
```

Where possible, preserve the source position so the UI can point to the failing expression.

---

# Formatting

`format_result()` is the common display path for runtime values.

A new type should normally add one type-specific branch:

```python
if isinstance(value, YourType):
    return format_your_type(value)
```

Compound formatters should recursively use `format_result()`.

For example:

```python
format_array()
format_matrix()
format_column()
format_table()
```

should not independently reimplement formatting for:

```text
currency
container
date
duration
```

This keeps nested values consistent with scalar values.

---

# Tests

Every extension should test both sides of the checker/evaluator contract.

Prefer tests that use the real public evaluator.

A direct implementation-unit test may prove that a helper works while missing:

* a forgotten registry entry
* the wrong result category
* parser integration
* lexer integration
* collection lifting
* formatting

---

## New function tests

Test:

* valid input
* invalid category
* result category
* runtime value
* minimum arity
* maximum arity
* runtime failure cases
* row scope if applicable
* laziness if applicable

---

## New operator tests

Test:

* valid scalar operation
* unsupported scalar operation
* result category
* runtime value
* operand order where non-commutative
* symmetry where registered
* runtime failure cases

For arithmetic-capable types, also test intended collection lifting:

```text
scalar OP scalar
array OP array
array OP scalar
matrix OP matrix
matrix OP scalar
column OP column
column OP scalar
```

Do not assume every combination should work.

---

## Column tests

Include at least one test proving that column names do not determine scalar compatibility.

For example:

```text
column{time_out: time}
-
column{time_in: time}
```

should resolve based on:

```text
time - time
```

not on whether:

```text
time_out == time_in
```

Also test the actual current naming semantics of the derived result.

---

## New cast tests

Test:

* valid conversion
* unsupported conversion
* result category
* exact runtime value
* precision behavior
* invalid runtime values
* round-trip behavior when relevant

---

## New type tests

Test:

* construction
* normalization
* invalid representation
* `category_of`
* diagnostic label
* literal tokenization if applicable
* parsing if applicable
* semantic validation
* formatting
* comparison rules
* arithmetic rules
* cast rules
* array use
* matrix use where meaningful
* column use
* table use

For a native literal, test at least one case that could otherwise be confused with a generic identifier or number.

---

# Current extension API

The current source of truth remains the engine registries and resolver functions.

## Function

```python
FUNCTIONS["name"] = FunctionSpec(
    "name",
    min_args,
    max_args,
    lazy,
    result_type,
    impl,
    row_scope_arg=...,
)
```

## Binary operator

```python
register_binary(
    op,
    left,
    right,
    result,
    impl,
)
```

## Unary operator

```python
register_unary(
    op,
    category,
    result,
    impl,
)
```

## Cast

```python
register_cast(
    source,
    target,
    result,
    impl,
)
```

These registries are part of the implementation contract today.

---

# Proposed extension API

A cleaner declarative API may eventually remove the need for extension authors to manipulate registries directly.

This section describes a desired direction, not necessarily the current implementation.

## Function

Target style:

```python
@function(
    "sqrt",
    args=1,
    result=same_type("decimal"),
)
def sqrt(values):
    ...
```

## Constant

```python
constant(
    "pi",
    "decimal",
    PI,
)
```

## Binary operation

```python
binary(
    "-",
    "time",
    "time",
    "duration",
    time_subtract,
)
```

## Unary operation

```python
unary(
    "-",
    "duration",
    "duration",
    negate_duration,
)
```

## Cast

```python
cast(
    "datetime",
    "date",
    "date",
    datetime_to_date,
)
```

The goal is not to replace the registry model.

The goal is to make the registry model easier and safer to extend.

The registries should remain the source of truth.

---

# Checklist

## New function

* [ ] define minimum argument count
* [ ] define maximum argument count
* [ ] define result-type rule
* [ ] define implementation
* [ ] decide whether evaluation must be lazy
* [ ] decide whether an argument uses row scope
* [ ] add registry entry
* [ ] add valid-input test
* [ ] add invalid-category test
* [ ] add arity tests
* [ ] add runtime-failure tests where applicable
* [ ] verify runtime category matches the checked result type

## New operator behavior

* [ ] define scalar semantics first
* [ ] register scalar rule
* [ ] decide whether operation is symmetric
* [ ] verify runtime category matches registered result
* [ ] test unsupported operand combinations
* [ ] test runtime failures
* [ ] test array lifting where applicable
* [ ] test matrix lifting where applicable
* [ ] test column lifting where applicable

## New cast

* [ ] define source category
* [ ] define target
* [ ] define result category
* [ ] register cast
* [ ] decide normalize / round / truncate / reject behavior
* [ ] decide whether reverse conversion exists
* [ ] test runtime failures
* [ ] test result category
* [ ] test round-trip behavior where meaningful

## New type

* [ ] immutable runtime representation
* [ ] representation validation / normalization
* [ ] add to `Value`
* [ ] add `category_of()` rule returning `Type(...)`
* [ ] add diagnostic label
* [ ] decide scalar versus compound `Type`
* [ ] add literal syntax if applicable
* [ ] add lexer rule if applicable
* [ ] add parser construction if applicable
* [ ] add editor highlighting if applicable
* [ ] define comparison semantics
* [ ] define arithmetic semantics
* [ ] define cast semantics
* [ ] add function compatibility where applicable
* [ ] add formatting
* [ ] add construction tests
* [ ] add invalid-value tests
* [ ] add scalar operation tests
* [ ] add collection tests where applicable
* [ ] verify checker/evaluator category agreement

---

# Design rule

The main rule is:

> **Add semantics to the shared registries and resolvers, then let the checker, evaluator, and collection machinery derive behavior from them.**

If adding a feature seems to require parallel special cases in both:

```text
check_types
```

and:

```text
evaluate_node
```

first ask whether the missing abstraction belongs in:

* a function result resolver
* the function registry
* the operator registry
* the cast registry
* `category_of`
* collection lifting
* the lexer/parser for genuinely new syntax

Special cases should be the exception.

Shared semantics should be the default.
