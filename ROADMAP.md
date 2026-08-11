# Roadmap

calc is growing from a typed expression evaluator into a table-first data
tool: typed relational tables over a domain-aware value system, with a
pure scripting layer. Not a replacement for Excel or Google Sheets — a
better experience for anyone whose data has *units*, where a tonnage
column can never silently absorb a plain number and `$0.10 + $0.20` is
exactly `$0.30`.

```
calc> 15t + 1
error: '+' is not defined for a tonnage and a whole number.
    15t + 1
        ^
```

That refusal to guess is the product. Everything below builds on it.

## Guiding decisions

- **One user first.** The initial target is a single concrete workflow:
  reconciling typed operational data (catch reports, sales, currency)
  currently living in error-prone spreadsheets. Personas beyond that
  (finance, engineering) come after the wedge works, via domain unit
  packs — not before.
- **One language everywhere.** Cell formulas, column expressions, and
  loaded script modules are the *same* pure DSL. No second imperative
  language bolted on (the VBA mistake). Scripts compute values; the
  host application applies them.
- **Explicit over implicit.** Ambiguity is a type error, not a guess.
  This extends to tables: no implicit broadcasting, no invisible
  evaluation context. Row scope in iterator functions is lexical and
  visible.
- **`src/engine_cli.py` stays the single source of truth**, imported by
  every surface (CLI, notebook, future table layer, future LSP). A fix
  lands once.
- **Files and terminal before UI.** CSV in → typed tables → computed
  columns → CSV out is a usable product before any grid exists.

## Phase 0 — Foundations *(1–2 weeks)*

Make the ground safe to refactor on.

- [ ] Extract the inline notebook tests into a real pytest suite; keep
      the notebook as a playground, not the safety net
- [ ] CI (GitHub Actions): pytest + ruff on every push
- [ ] `VISION.md`: the one-user/one-workflow decision, written down
- [ ] License, changelog

**Exit:** `pytest` green in CI, covering every current behavior.

## Phase 1 — Structured types *(3–5 weeks)*

The load-bearing refactor. Categories today are flat strings
(`"currency"`, `"duration"`); tables and structs need parameterized
types (`table{vessel: text, qty: tonnage}`) that the checker can
inspect, compare, and print in error messages.

- [ ] `Type` objects replace category strings throughout: `check_types`,
      `BINARY_RULES`/`UNARY_RULES`/`CAST_RULES` dispatch, every
      `FunctionSpec.result_type`, error rendering
- [ ] No user-visible behavior change; the Phase 0 suite proves it
- [ ] First visible win to validate the machinery: **record/struct
      values** — `{name: "Elena", rate: $45.00}`, typed field access,
      struct types in `^` error messages

**Exit:** all existing tests pass unchanged; structs work end to end.

*This phase is deliberately feature-poor and is the one that must not be
skipped or rushed — every later phase compounds on it.*

## Phase 2 — Tables *(4–6 weeks)*

The table becomes the primitive. Growing by rows is trivial; growing by
columns happens only through declared computation.

- [ ] Table value: columnar store of engine values + schema (own
      implementation — no pandas/polars; their type systems fight ours)
- [ ] Typed CSV import: explicit column type declarations, **loud**
      per-cell failure on mismatch, `blank` only by explicit opt-in.
      The error UX here is half the product
- [ ] Columns as typed vectors; elementwise ops registered through the
      existing dispatch tables — explicit, never auto-broadcast
- [ ] Six composable verbs: `filter`, `select`, `extend`, `lookup`,
      `groupby` + aggregates, `sort`
- [ ] Iterator functions (`sumx`-style) with lexical `[column]` row
      scope — no DAX-style implicit context, ever
- [ ] Nested tables: one-to-many lookups returning a table in a cell
- [ ] CSV export

**Exit — the milestone that matters:** one real spreadsheet workflow
replaced end to end from the CLI/REPL: import, join, compute typed
columns, aggregate, export. From this point on the project *is* a
product.

## Phase 3 — First users *(2–3 weeks, overlapping Phase 2)*

3–5 real people running the target workflow on their own data. Watching,
not launching. Three bets under test:

- Do typed errors on *their* messy files feel protective or annoying?
- Are six verbs enough, or is a seventh immediately demanded?
- Does import survive their actual CSVs?

Findings feed Phase 2 polish before any new surface is built.

## Phase 4 — Scripting *(3–4 weeks)*

The same language, loadable from files. What VBA should have been.

- [ ] User-defined functions: typed parameters, bodies checked at
      definition time; recursion allowed, closures deferred
- [ ] Modules: files of `let` bindings and function definitions;
      `load` them into the application, apply them to tables
- [ ] Determinism: `now()`/`today()` pinned once per evaluation pass —
      same script + same data = same table, always
- [ ] Language spec doc + deprecation policy (modules make syntax a
      compatibility surface, even at v0.x)

## Phase 5 — Surfaces *(ongoing, in this order)*

1. **LSP server** — the existing position-tracked errors make this
   cheap; red squiggles from *our* checker in any IDE is the scripting
   story made visible
2. **Textual TUI grid** — browse and edit tables in the terminal, where
   this tool already lives
3. **Web UI** — only when users ask; biggest cost, least differentiated

## Later / open questions

- User-*defined* units: declare `kWh` the way tonnage is built in —
  turns one industry into domain packs
- Vector and matrix types (columns already are vectors; a rectangular
  range is a matrix)
- Persistence format for tables beyond CSV round-tripping
- Open-core sustainability question — deferred until Phase 3 says
  people want this

## Non-goals

- Replacing Excel/Sheets for general-purpose grid work
- A freeform cell grid — tables are deterministic on purpose
- Implicit type coercion of any kind, anywhere, for any reason
- A second scripting language
- Performance work before a profiler demands it

---

*Roughly 4–6 months of consistent part-time work to the Phase 2/3
milestone; 8–12 to the full scripted-and-IDE vision. When in doubt, cut
from the end of the plan, never from Phase 1.*
