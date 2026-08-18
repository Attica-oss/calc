# Extending the expression engine

The engine has a small number of extension points:

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

For operators and casts, both passes use shared dispatch tables. Collection arithmetic is derived from scalar arithmetic rather than registered independently.

That means most extensions should be declarative:

* new function → register a function
* new operator behavior → register a scalar rule
* new cast → register a cast rule
* new collection behavior → usually nothing; scalar rules are lifted automatically
* new value type → representation + category + operations + formatting

Avoid adding special cases directly to `check_types` or `evaluate_node` unless the language feature genuinely cannot be represented through one of these extension points.

---

# Adding a function

Functions are described by `FunctionSpec`.

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

which runs during type checking, and:

```text
impl(values)
```

which runs during evaluation.

For lazy functions, `impl` instead receives the unevaluated argument nodes and an evaluation callback.

## Prefer registration helpers

New functions should not normally modify the `FUNCTIONS` dictionary directly.

Use a registration helper:

```python
def register_function(
    name: str,
    *,
    args: int | tuple[int, int | None],
    result,
    lazy: bool = False,
    row_scope_arg: int | None = None,
):
    if isinstance(args, int):
        min_args = max_args = args
    else:
        min_args, max_args = args

    def decorator(impl):
        FUNCTIONS[name] = FunctionSpec(
            name=name,
            min_args=min_args,
            max_args=max_args,
            lazy=lazy,
            result_type=result,
            impl=impl,
            row_scope_arg=row_scope_arg,
        )
        return impl

    return decorator
```

Then an ordinary function becomes:

```python
@register_function(
    "sqrt",
    args=1,
    result=_numeric_result,
)
def _sqrt(values):
    [value] = values
    return decimal_sqrt(value)
```

There is no separate registry entry to remember.

That is deliberate: defining the function should register the function.

---

## Fixed result types

Many functions always return the same category.

Instead of writing a new result checker for each one:

```python
def _today_result(categories, node):
    if categories:
        _fail(node, "today() takes no arguments.")

    return "date"
```

use a reusable helper:

```python
def fixed_result(category):
    def resolve(categories, node):
        return category

    return resolve
```

Then:

```python
@register_function(
    "today",
    args=0,
    result=fixed_result("date"),
)
def _today(values):
    return date.today()
```

The argument count is already enforced by `FunctionSpec`, so the result resolver only needs to determine the result type.

---

## Functions restricted to particular categories

Most functions have simple input rules.

Provide helpers for common cases:

```python
def accepts(*allowed, returns):
    allowed = set(allowed)

    def resolve(categories, node):
        if any(category not in allowed for category in categories):
            _fail(node, "Unsupported argument type.")

        return returns

    return resolve
```

For functions where the result type is the same as the input:

```python
def same_type(*allowed):
    allowed = set(allowed)

    def resolve(categories, node):
        [category] = categories

        if category not in allowed:
            _fail(node, "Unsupported argument type.")

        return category

    return resolve
```

Now something like `abs()` can be registered declaratively:

```python
@register_function(
    "abs",
    args=1,
    result=same_type(
        "int",
        "decimal",
        "currency",
        "tonnage",
        "duration",
    ),
)
def _abs(values):
    [value] = values
    return abs_value(value)
```

Keep a custom result resolver only when the function has genuinely custom type semantics.

---

## Example: `ceil()`

`ceil(x, multiple)` needs a custom resolver because its result depends on its arguments.

```python
def _ceil_result(categories, node):
    x, multiple = categories

    if x != multiple:
        _fail(
            node,
            "ceil() requires x and multiple to have the same type.",
        )

    if x == "int":
        return "int"

    if x in {
        "decimal",
        "currency",
        "tonnage",
        "percent",
        "duration",
    }:
        return x

    _fail(
        node,
        "ceil() accepts numbers, quantities, or durations.",
    )
```

Registration is still one operation:

```python
@register_function(
    "ceil",
    args=2,
    result=_ceil_result,
)
def _ceil(values):
    x, multiple = values

    if isinstance(x, Duration):
        return _ceil_duration(x, multiple)

    if isinstance(x, Quantity):
        return _ceil_quantity(x, multiple)

    return _ceil_number(x, multiple)
```

The evaluator does not need to repeat the category validation.

The checker already did that.

---

# Lazy functions

A function should be lazy only when evaluating every argument would change the semantics.

`if()` is the canonical example:

```text
if(x != 0, 1 / x, 0)
```

The unused branch must not be evaluated.

Register it explicitly as lazy:

```python
@register_function(
    "if",
    args=3,
    result=_if_result,
    lazy=True,
)
def _if(args, environment, evaluate, row_scope):
    condition = evaluate(
        args[0],
        environment,
        row_scope,
    )

    branch = args[1] if condition else args[2]

    return evaluate(
        branch,
        environment,
        row_scope,
    )
```

Do not make a function lazy merely as an optimization.

Lazy evaluation is part of the language semantics.

---

# Row-scoped functions

Table functions such as:

```text
filter(table, [qty] > 2t)
```

evaluate one argument inside the schema of a table.

Those functions use `row_scope_arg`.

For example:

```python
@register_function(
    "filter",
    args=2,
    result=_filter_result,
    lazy=True,
    row_scope_arg=1,
)
def _filter(args, environment, evaluate, row_scope):
    ...
```

The checker uses the schema of argument `0` while checking argument `1`.

Keep this mechanism limited to functions whose syntax actually contains a row expression.

Functions such as:

```text
select(table, "name", "qty")
```

do not need row scope if their column arguments are compile-time names rather than arbitrary expressions.

---

# Adding a constant

Constants are zero-argument functions.

Use a helper:

```python
def register_constant(name, category, value):
    @register_function(
        name,
        args=0,
        result=fixed_result(category),
    )
    def constant(values):
        return value

    return constant
```

Then:

```python
register_constant("pi", "decimal", PI)
register_constant("e", "decimal", E)
```

This keeps constants in the same namespace and dispatch system as every other function.

Using:

```text
pi()
```

instead of a reserved identifier also means:

```text
let pi = 3
```

can remain legal without introducing special parser rules.

---

# Adding arithmetic

Arithmetic is defined for scalar types first.

```python
register_binary(
    "-",
    "time",
    "time",
    "duration",
    time_subtract,
)
```

That registration means:

```text
time - time → duration
```

Do not separately register arithmetic for every collection containing a time.

Collection arithmetic is lifted from scalar arithmetic.

---

# Collection lifting

Arrays and matrices have compound types:

```text
array{int}
array{currency}
matrix{decimal}
```

They do not have flat operator registrations such as:

```python
register_binary("+", "array", "array", ...)
```

A compound type is not equal to the flat category `"array"`, so such a rule would never match.

Instead:

```text
array{X} OP array{Y}
```

resolves the scalar rule:

```text
X OP Y
```

and lifts that implementation element-wise.

For example:

```text
array{int} / array{int}
```

looks up:

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

broadcasts the scalar operand and resolves:

```text
currency * int
```

for every element.

This preserves the central rule:

> Collections do not invent arithmetic semantics. They inherit the semantics of their elements.

---

# Columns

Columns are also typed collections, but unlike arrays their field name is metadata:

```text
column{time_in: time}
column{time_out: time}
```

Operator compatibility should depend on the element type, not the column name.

Therefore:

```text
column{time_out: time}
-
column{time_in: time}
```

resolves:

```text
time - time → duration
```

and produces a derived duration column.

The names `time_in` and `time_out` must not participate in operator dispatch.

A useful distinction is:

```text
column{time_in: time}
```

for a named source column, versus:

```text
column{duration}
```

for a derived expression.

This avoids pretending that:

```text
time_out - time_in
```

is still either the `time_out` or `time_in` column.

---

# Arithmetic supported by collections

Only arithmetic operators are lifted automatically:

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

ELEMENTWISE_UNARY_OPS = frozenset({
    "+",
    "-",
})
```

Comparisons are intentionally not lifted.

For scalar values:

```text
a = b
```

produces:

```text
boolean
```

Automatically lifting that operation would make:

```text
array = array
```

produce:

```text
array{boolean}
```

which has very different semantics from ordinary language-level equality.

Comparison behavior for collections should therefore be introduced explicitly rather than inherited accidentally.

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

Some registrations support category aliases such as:

```text
number
```

which expand to:

```text
int
decimal
```

Use:

```python
symmetric=True
```

only when the operation itself is genuinely commutative.

Good candidates:

```text
a + b
a * b
```

Bad candidates:

```text
a - b
a / b
```

Never use symmetry simply to save a second registration if reversing the operands changes the operation.

---

# Adding a unary operator rule

Unary operations use the same model:

```python
register_unary(
    "-",
    "complex",
    "complex",
    complex_negate,
)
```

Arrays and matrices inherit that operation automatically:

```text
-array{complex}
```

becomes element-wise complex negation.

---

# Adding a cast

Casts have their own registry:

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

An absent cast registration is a type error automatically.

Do not add a special rejection branch elsewhere.

When adding a cast, decide deliberately:

1. What does the conversion mean?
2. Does it round-trip sensibly?
3. Does it truncate, round, or reject invalid precision?
4. Can it fail at runtime?

For example:

```text
5%::DECIMAL → 0.05
0.05::PERCENT → 5%
```

forms a sensible inverse pair.

---

# Adding a type

A genuinely new type requires several decisions.

## 1. Representation

Prefer an immutable value object:

```python
@dataclass(frozen=True)
class YourType:
    ...
```

Examples include:

```text
Duration
Quantity
Complex
Blank
Array
Matrix
Column
```

Use `__post_init__` for representation-level normalization or validation when needed.

---

## 2. Category

Add the value to `category_of`.

```python
if isinstance(value, YourType):
    return "your_type"
```

Also add a human-readable category label for diagnostics.

Before adding a category, ask whether the value really represents a new kind of thing.

For example, infinity does not need its own category if it is represented naturally as a `Decimal`.

---

## 3. Compound types

Collection-like categories should use `Type` rather than inventing encoded strings.

Examples:

```text
array{currency}
matrix{decimal}
column{time_in: time}
```

The structure of the type should describe information that matters to checking and dispatch.

Metadata that does not affect operator compatibility should not accidentally become part of scalar dispatch.

---

## 4. Literal syntax

Only add tokenizer/parser support if users can construct the type with literal syntax.

A function-created type such as:

```text
blank()
```

does not require a token.

If the type does have a literal, place specific token rules before generic numeric rules when their prefixes overlap.

---

## 5. Comparison semantics

Choose deliberately between:

### Fully ordered

```text
=
<>
<
<=
>
>=
```

Examples include ordinary numbers, dates, and other values with a meaningful total order.

### Equality only

```text
=
<>
```

Examples include values for which equality makes sense but ordering does not.

### No comparison

Register nothing.

An unsupported operation should generally be represented by the absence of a dispatch rule.

---

## 6. Arithmetic

Register only operations with clear semantics.

For example, IP addresses might reasonably support:

```text
IP + int → IP
IP - int → IP
IP - IP  → int
```

while deliberately not defining:

```text
IP + IP
IP * int
IP / int
```

Leaving a rule unregistered is a feature, not an incomplete implementation.

---

# Result-type correctness

Never return a runtime value whose category differs from the category promised by the checker.

For example, if:

```text
decimal - decimal → decimal
```

was resolved during type checking, the implementation must either:

1. return a decimal, or
2. raise an `ExpressionError`.

It must not quietly return:

```text
Blank()
```

or some other category.

Otherwise the checker and evaluator disagree, and downstream consumers receive incorrect type information.

This applies equally to:

* operators
* functions
* casts
* lifted collection operations

---

# Runtime failures

Some operations are valid by type but invalid for particular values.

Examples include:

```text
1 / 0
```

or an arithmetic result outside the valid range of a bounded type.

Those are runtime `ExpressionError`s, not type errors.

Keep the distinction:

```text
unsupported categories → type-check error
valid categories + invalid values → runtime error
```

---

# Formatting

New runtime values need a formatting rule.

Type-specific formatting should occur before generic fallbacks.

For compound values, prefer reusing the scalar formatter for their contained values rather than duplicating formatting rules.

For example, formatting:

```text
array{currency}
```

should ultimately reuse currency formatting for each element.

---

# Tests

Every extension should test both sides of the checker/evaluator contract.

For a new function:

* valid input
* invalid category
* result category
* runtime result
* arity error

For a new scalar operator:

* valid operation
* unsupported operation
* result category
* runtime value

For arithmetic-capable element types, also test collection lifting:

```text
scalar OP scalar
array OP array
array OP scalar
matrix OP matrix
```

when those combinations are intended to work.

For columns, include at least one test proving that different column names do not prevent compatible element-wise arithmetic.

Example:

```text
column{time_out: time}
-
column{time_in: time}
→
column{duration}
```

The most valuable test is one that goes through the real public evaluator rather than testing the implementation function directly. That catches forgotten registrations automatically.

---

# Recommended extension API

The long-term goal should be that most additions look like one of these.

## Function

```python
@function("sqrt", args=1, result=same_type("decimal"))
def sqrt(values):
    ...
```

## Constant

```python
constant("pi", "decimal", PI)
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
    duration_negate,
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

The registry remains the source of truth, but extension authors no longer need to manipulate the registry directly.

---

# Checklist

## New function

* [ ] Define argument count
* [ ] Define result-type rule
* [ ] Define implementation
* [ ] Register through `@function`
* [ ] Add valid-input test
* [ ] Add invalid-category test
* [ ] Add arity test

## New operator behavior

* [ ] Define scalar semantics first
* [ ] Register the scalar rule
* [ ] Decide whether the operation is symmetric
* [ ] Verify returned runtime category matches the registered result
* [ ] Test collection lifting where applicable

## New cast

* [ ] Register source and target
* [ ] Decide round / truncate / reject behavior
* [ ] Check whether the conversion should round-trip
* [ ] Test runtime failures

## New type

* [ ] Immutable representation
* [ ] `category_of`
* [ ] diagnostic label
* [ ] compound `Type` structure if applicable
* [ ] literal syntax if applicable
* [ ] comparison semantics
* [ ] arithmetic registrations
* [ ] cast registrations
* [ ] function compatibility
* [ ] formatting
* [ ] scalar tests
* [ ] lifted collection tests where applicable

The main design rule is simple:

> **Add semantics to the registries, then let the checker, evaluator, and collection lifting derive behavior from them.**

If adding a feature requires parallel special cases in both `check_types` and `evaluate_node`, first check whether the missing abstraction belongs in the registry or resolver instead.
