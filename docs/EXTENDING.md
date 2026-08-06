# Extending the engine

Everything lives in `src/engine_cli.py`, in five stages, in this order in
the file:

```
tokenize  ->  parse (AST)  ->  check_types  ->  evaluate_node  ->  format_result
```

`check_types` and `evaluate_node` are two separate walks over the same
AST. The checker decides an expression's category *without* running it;
the evaluator runs it and must produce a value of exactly that category.
Nearly every bug you can introduce while extending this engine is some
form of breaking that agreement — the checker promises `"decimal"`, the
evaluator hands back something else. Keep that invariant in mind below;
it's called out again where it actually bites.

The four sections below are ordered easiest-first. If you're adding
something that's both a new function *and* touches a new kind of value
(most real features), read the relevant ones together — start with
**type**, since a function or cast usually exists to build or inspect
one.
function usually exists to build or inspect one.

---

## Adding a function

This is the cheapest extension: no tokenizer or parser changes, ever.
Every function is one `FunctionSpec`:

```python
@dataclass(frozen=True)
class FunctionSpec:
    name: str
    min_args: int
    max_args: int | None       # None means unbounded (sum, avg, min, max)
    lazy: bool                  # True only for if() — see below
    result_type: Callable       # (categories, node) -> category, or raises
    impl: Callable               # eager: (values) -> value
                                    # lazy:  (arg_nodes, environment, evaluate) -> value
```

`result_type` runs during type-checking, before anything is evaluated —
it only ever sees the *categories* of the arguments (strings like
`"currency"` or `"complex"`), never their values. `impl` runs during
evaluation and only ever sees values that already passed `result_type`,
so it doesn't need to re-validate types — just handle the categories
`result_type` promised to allow.

### Worked example: `ceil()`

`ceil(x, multiple)` is a good template because its result category
*depends on its argument's* category (number in → number out, duration
in → duration out) rather than being fixed like `today()`. Read it in
`src/engine_cli.py` (search `_ceil_result`) alongside this:

```python
def _ceil_result(categories, node):
    x_category, multiple_category = categories

    if x_category != multiple_category:
        _fail(node, "ceil() requires x and multiple to be the same kind of value.")

    if x_category in NUMERIC_CATEGORIES:
        return "int" if x_category == "int" else "decimal"

    if x_category in {"currency", "tonnage", "percent", "duration"}:
        return x_category

    _fail(node, "ceil() accepts numbers, quantities, or durations.")
```

`_fail(node, message)` raises `ExpressionError` with the call's source
position attached — always use it instead of a bare `raise`, so errors
point at the right place in the CLI's `^` output. The `impl` side
dispatches on the *runtime* type instead of re-deriving categories,
since by the time `impl` runs, `result_type` already vouched for them:

```python
def _ceil_impl(values):
    x, multiple = values
    if isinstance(x, Duration):
        return _ceil_duration(x, multiple)
    if isinstance(x, Quantity):
        return _ceil_quantity(x, multiple)
    return _ceil_number(x, multiple)
```

Then register it — this is the only step that's easy to forget, and
forgetting it is exactly how `ceil()` shipped broken before this
review: implemented, tested in the notebook, documented in the README,
and never added to `FUNCTIONS`, so every call failed with `Unknown
function: ceil()`. **If your new tests reference the function, run them
before you consider the function done** — that alone would have caught
it.

```python
FUNCTIONS = {
    ...
    "ceil": FunctionSpec("ceil", 2, 2, False, _ceil_result, _ceil_impl),
}
```

### Lazy functions

`if()` is the only lazy function today, and it's the reason `impl`
functions get an `evaluate` callback at all:

```python
def _if_impl(args, environment, evaluate):
    condition = evaluate(args[0], environment)
    chosen = args[1] if condition else args[2]
    return evaluate(chosen, environment)
```

Make a function lazy only when evaluating an argument unconditionally
would be wrong or unsafe — `if(x > 0, 1 / x, 0)` must not divide by
zero when `x <= 0`. If there's no such hazard, keep it eager (simpler,
and the default for everything else, including `coalesce()`, which
looks like it should be lazy but isn't: this language has no side
effects, so evaluating the branch you don't end up using costs nothing
and never causes an error).

### The one function with no type restriction

`isblank(x)` is deliberately the single exception to "validate your
argument categories": it has to accept *anything*, since the entire
point is testing a value whose type you don't know in advance.

```python
def _isblank_result(categories, node):
    return "boolean"
```

If you ever want a second fully-polymorphic function, this is the
pattern — just don't reach for it by default; every other function in
the registry validates its inputs, and that's what makes the checker's
error messages useful.

---

## Adding a constant

Constants are zero-argument functions — `today()`, `pi()`, `e()`,
`infinity()` are all `FunctionSpec("name", 0, 0, False, _fixed(category), lambda values: THE_VALUE)`.

```python
"pi": FunctionSpec("pi", 0, 0, False, _fixed("decimal"), lambda values: PI),
```

This is a deliberate choice over a bare reserved identifier (`PI`
instead of `pi()`): identifiers are how variables work in this
language, and a bare `PI` would either silently shadow a variable
someone names `pi`, or require carving out a list of reserved words
that `let pi = ...` can't use. Requiring the call syntax sidesteps the
whole question — `pi` the variable and `pi()` the constant simply can't
collide. Function names are lowercased during parsing, so `PI()`,
`Pi()`, and `pi()` all already resolve to the same entry; you don't
need to register casing variants.

If the constant is expensive or arbitrary-precision (like `PI`/`E`,
hardcoded to 50 digits rather than computed), define it once at module
level near the other constants and close over it in the lambda, as
above — don't recompute it inside the lambda on every call.

---

## Adding a cast rule

`value::TARGET` is its own dispatch table, `CAST_RULES`, deliberately
separate from `BINARY_RULES` — the key shape is `(source_category,
target_name)` rather than `(op, left_category, right_category)`, since
a cast target (`day`, `date`, `decimal`, ...) is a fixed keyword, not a
typed operand with its own category to check.

```python
def register_cast(source_category, target, result_category, impl):
    CAST_RULES[(source_category, target)] = (result_category, impl)

register_cast("datetime", "date", "date", lambda v: v.date())
```

Same rules as the operator table apply: leaving a combination
unregistered makes it a clean type error for free (`5::DAY` fails
because `("int", "day")` was never registered, not because of a
special check anywhere), and every `impl` must actually return a value
whose category matches what you registered — the same invariant from
§6 above, since `check_types` and `evaluate_node` both consult
`CAST_RULES` independently.

The target name itself is just a string key, lowercased by the parser
before lookup (`Cast.target`) — no tokenizer or grammar change is
needed to add a new target for a type that already has a literal.
You only touch the tokenizer if you're adding an entirely new *kind*
of value with its own literal syntax (see "Adding a type" above); a
new cast *target* for an existing type is purely a `CAST_RULES`
registration.

Two things worth deciding deliberately, the way `PERCENT`'s casts
were:

- **Is the conversion actually reversible, and does it read the way
  a user would expect?** `5%::DECIMAL` gives `0.05` (percent's raw
  internal ratio) and `0.05::PERCENT` gives back `5%` — the two casts
  are genuine inverses. It would have been just as easy to make
  `5::PERCENT` mean "the number 5, relabeled as a percent" (i.e.
  `500%`), which is internally consistent but surprising and *not*
  the inverse of the other direction. When a type has more than one
  plausible reading, prefer the one that round-trips.
- **Should this cast truncate, round, or reject?** `::INT` truncates
  toward zero (a real cast, distinct from `round()`, which rounds
  half-up) — pick one behavior deliberately and say so in a comment;
  don't leave it to whatever Python's default happens to do.

---

## Adding a type

This is the real work, and the one place a wrong turn costs you a
rewrite. Work through these in order; skipping the ordering question
(step 4) is the most common way to end up with a type that silently
does the wrong thing.

### 1. Pick a representation

A `@dataclass(frozen=True)` with plain fields. Every existing type
follows this:

| Type       | Fields                          | Why                                             |
| ---------- | -------------------------------- | ------------------------------------------------ |
| `Duration` | `months, days, seconds` (`int`)  | three axes with genuinely different arithmetic    |
| `Quantity` | `value: Decimal, unit: Unit`     | one number, several possible units                |
| `Complex`  | `real, imag` (`Decimal`)          | no unit — every complex number is the same "kind" |
| `Blank`    | *(none)*                          | zero fields ⇒ every instance is equal for free    |

Frozen + immutable so values can be dict keys, compared by `==`, and
never mutated out from under a variable binding. If your type wraps a
`Decimal` that needs fixed precision (money-like), quantize it in
`__post_init__`, the way `Quantity` does — and see step 6 about what
happens when that quantize call can fail.

### 2. Wire it into `category_of`

```python
def category_of(value) -> str:
    ...
    if isinstance(value, YourType):
        return "your_category"
    ...
```

Order matters only for subclass relationships (`bool` before `int`,
since `bool` is an `int` subclass in Python) — otherwise put your check
wherever's convenient. Add the category to `CATEGORY_LABELS` too; that
string is what shows up in every "not defined for ..." error message,
so write it as a noun phrase (`"a complex number"`, not `"complex"`).

**Ask first: does this need a new category at all?** Infinity didn't —
it's just `Decimal("Infinity")`, so `category_of` already returns
`"decimal"` for it, and every dispatch rule already registered for
`"decimal"` (comparisons, unary minus, `min`/`max`, `abs`) works on it
for free. A new category is for a genuinely new *kind* of value, not
every new *value*.

### 3. Literal syntax, if it needs any

Not every type needs one — `Blank` has no literal, only `blank()`.
If yours does, add a token to `TOKEN_RE` **before** `NUMBER` in the
alternation (order is significant: Python's `re` tries alternatives
top-to-bottom and takes the first match, not the longest, so a
generic pattern listed above a specific one will shadow it):

```python
(?P<IMAGINARY>
    (?:\d+(?:\.\d*)?|\.\d+)
    i
)
```

then a branch in `Parser.parse_primary`:

```python
if token.kind == "IMAGINARY":
    self.advance()
    return Literal(value=Complex(Decimal(0), Decimal(token.value[:-1])), position=token.position)
```

A number glued directly to a suffix character (`4i`, `2.4t`, `1.5%`) is
the established convention for "number with an implicit unit." A bare
occurrence of that same letter (`i`, `t`) stays a perfectly ordinary
variable name — the glued-vs-bare distinction is what the tokenizer's
lookahead already gives you, so this comes for free; you don't need to
reserve the letter.

If a single symbol is the whole literal (no digits attached, like `∞`),
that's simpler still: one token, one `parse_primary` branch returning
a fixed `Literal`.

### 4. Decide how it compares — this is the step to not skip

Every type falls into one of three buckets. Get this wrong and you'll
either reject sensible code or accept nonsensical code silently.

- **Full ordering** (`=  <>  <  <=  >  >=`): numbers, dates, times,
  currency, tonnage, percent. Add the category to the big loop in the
  "Comparisons" section of the dispatch table.
- **Equality only** (`=  <>`, nothing else): `Duration`, `Complex`,
  `Blank`, `boolean`. Anything with no total order compatible with its
  own arithmetic — is `1mo` more than `30d`? There's no single correct
  answer, so `min`/`max`/`<` reject durations on purpose rather than
  picking an arbitrary tiebreak. Add these to the smaller
  `for _category in ("duration", "boolean", "complex", "blank"):` loop.
- **No comparison at all against other categories** — this one's free:
  simply register nothing. `Blank` is equality-only *against itself*
  specifically (`blank() = blank()` works, `blank() = 5` is a type
  error) rather than universally comparable, precisely so it can't
  silently coerce to "equal to zero" the way blank cells do in a
  spreadsheet. If you want a type to be safely comparable against
  values of unknown type, that's what a polymorphic function like
  `isblank()` is for (see above) — not a comparison operator.

### 5. Register arithmetic in the dispatch table — or don't

`BINARY_RULES` and `UNARY_RULES` are flat dicts keyed by
`(op, left_category, right_category)` / `(op, category)`. `register_binary`
and `register_unary` are the only way anything gets in:

```python
register_binary("+", "complex", "complex", "complex", complex_add)
register_binary("+", "complex", "number", "complex", complex_add, symmetric=True)
```

`"number"` as a category argument is shorthand that expands to both
`"int"` and `"decimal"` — use it instead of writing both out.
`symmetric=True` registers the mirrored `(right, left)` pairing too,
**only for genuinely commutative operations**. `+` and `*` usually
qualify; `-` and `/` almost never do — get this wrong and
`5 - (3+4i)` silently computes `(3+4i) - 5` instead. When in doubt,
register both directions explicitly with the correct argument order
rather than reaching for `symmetric=True`.

If your type has no sensible arithmetic (or you haven't decided its
semantics yet), register nothing — an unregistered `(op, cat_a, cat_b)`
combination is *automatically* a clean type error from `check_types`,
with no extra code. This is why `Blank` needed zero binary-rule
registrations: leaving them out isn't a gap to fill in later, it's the
entire feature. Don't reach for a special case inside `check_types` or
`evaluate_node` to reject a combination — if you find yourself writing
one, you almost always want to *not register the rule* instead.

### 6. The invariant that's easy to break silently

`check_types` declares a result category before anything runs.
`evaluate_node` then must return a value whose *actual* category
(via `category_of`) matches what was declared — nothing downstream
re-derives the category from the value; `EvaluationResult.category`
is what the checker said. If `impl` can sometimes return a different
kind of value than `result_type` promised, everything downstream
(formatting, the CLI's `(category)` label, a future spreadsheet's
dependency graph) can silently believe the wrong thing.

This came up directly while adding infinity: `Decimal("Infinity") -
Decimal("Infinity")` is mathematically undefined, and Python's
`decimal` module already knows that — it raises `InvalidOperation`.
The tempting fix is to catch that and quietly hand back `Blank()`
instead. Don't do that: the checker already promised `"decimal"` for
that subtraction, so returning a `Blank` (category `"blank"`) breaks
the promise. The actual fix is to let it become a **runtime
`ExpressionError`** — same treatment as dividing by zero, which has
always been a raised error rather than a silently propagated special
value:

```python
def numeric_subtract(a, b):
    return _guard_indeterminate(lambda: a - b)
```

If your new type's arithmetic can fail in a way you're tempted to
paper over with another type's sentinel value, raise `ExpressionError`
instead. `Blank` (or anything else) should only ever appear where a
user explicitly asked for it.

### 7. Decide which existing functions should accept it

`abs`, `min`, `max`, `sum`, `avg` all gate on an explicit set of
categories inside their `result_type` — adding your type to their
allowed sets is what makes `sum($1, $2)` and `sum(1+1i, 2+2i)` both
work through the same function. It's opt-in, not automatic: a new
category is invisible to every existing function until you add it to
the relevant `{...}` set, which is exactly what stops a half-finished
type from being silently accepted somewhere you didn't test.

### 8. Formatting

Add a branch to `format_result` (or `format_decimal`, if it's a
`Decimal`-shaped value used inside other types too, like `Complex`'s
real/imaginary parts — fixing it there fixes every caller at once).
Put type-specific branches **before** the generic fallbacks
(`isinstance(value, Decimal)`, `f"{value:,}"`) so they're actually
reached; `format_result` checks in order and returns on the first
match.

### 9. Tests

Add cases to the inline test cell in `src/notebook.py` — this is the
project's real test suite (`uv run marimo edit src/notebook.py`, or
run the file as a plain script and check for a non-zero exit code).
At minimum: one literal parses correctly, one arithmetic operation
that should work, one that should be a *type error* (`_expect_error`,
checking the message contains a distinctive fragment), and one
round-trip through `format_result`. The `ceil()` incident is the
cautionary tale here — code that was tested only in a doc comment, not
actually run, shipped broken.

---

## Checklist

- [ ] Representation: frozen dataclass, quantize/validate in
      `__post_init__` if needed
- [ ] `category_of` branch + `CATEGORY_LABELS` entry
- [ ] Literal syntax, if any: `TOKEN_RE` (before `NUMBER`) + parser branch
- [ ] Comparison bucket decided: full order / equality-only / none
- [ ] Arithmetic registered in `BINARY_RULES`/`UNARY_RULES` — or
      deliberately left unregistered
- [ ] `symmetric=True` only where the operation is actually commutative
- [ ] No function returns a category other than what `result_type`
      declared, even on an error path — raise `ExpressionError` instead
- [ ] Added to `abs`/`min`/`max`/`sum`/`avg`'s allowed-category sets,
      if it should work with them
- [ ] New cast targets registered in `CAST_RULES`, if relevant — check
      the conversion round-trips sensibly and decide truncate vs.
      round vs. reject deliberately (see "Adding a cast rule")
- [ ] `format_result` branch, placed before the generic fallbacks
- [ ] Tests in `src/notebook.py`: literal, valid op, type error, format
- [ ] `README.md`'s type and function tables updated
- [ ] Actually run the tests (`uv run marimo edit src/notebook.py` or
      `python src/notebook.py`) — not just write them
