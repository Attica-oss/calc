// CodeMirror 6 editor for the calc expression language.
//
// The tokenizer below is a direct port of TOKEN_RE in
// src/engine/lexer.py, kept in the same alternation order. That order is
// load-bearing in the Python original ("DATETIME before DATE, POWER before
// MULTIPLY, LE before LT") and it's load-bearing here for the same reason:
// a longer alternative must get first refusal.
//
// Imports go through esm.sh with ?deps= pinning every shared CodeMirror
// package. @codemirror/view, /language and /commands each depend on
// @codemirror/state, and without the pins esm.sh serves several copies.
// CodeMirror uses instanceof checks internally, so duplicates fail at
// runtime rather than at load.

import { EditorState } from "https://esm.sh/@codemirror/state@6";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
} from "https://esm.sh/@codemirror/view@6?deps=@codemirror/state@6";
import {
  defaultKeymap,
  history,
  historyKeymap,
} from "https://esm.sh/@codemirror/commands@6?deps=@codemirror/state@6,@codemirror/view@6";
import {
  StreamLanguage,
  HighlightStyle,
  syntaxHighlighting,
  bracketMatching,
} from "https://esm.sh/@codemirror/language@6?deps=@codemirror/state@6,@codemirror/view@6,@lezer/highlight@1";
import { tags } from "https://esm.sh/@lezer/highlight@1";

// ---------------------------------------------------------------------------
// Vocabulary, lifted from the engine.
//
// ---------------------------------------------------------------------------

// FUNCTIONS registry in src/engine/functions.py.
const FUNCTIONS = new Set([
  "abs",
  "and",
  "append",
  "array",
  "at",
  "avg",
  "blank",
  "capitalize",
  "ceil",
  "coalesce",
  "colcount",
  "column",
  "concat",
  "conj",
  "dayname",
  "days_between",
  "e",
  "eomonth",
  "eoquarter",
  "eoyear",
  "extend",
  "filter",
  "format",
  "groupby",
  "hours_between",
  "if",
  "im",
  "infinity",
  "isblank",
  "is_public_holiday",
  "left",
  "len",
  "lower",
  "matrix",
  "max",
  "min",
  "mid",
  "not",
  "now",
  "or",
  "pi",
  "re",
  "right",
  "round",
  "rowcount",
  "select",
  "sort",
  "somonth",
  "soquarter",
  "soyear",
  "sum",
  "table",
  "time",
  "title",
  "today",
  "type_of",
  "upper"
]);

// Arity 0,0 in the same registry. They still require call syntax — pi() not
// pi — but they read as literals, so they're coloured as constants.
const NULLARY = new Set(["today", "now", "pi", "e", "infinity"]);

// register_cast() targets in src/engine/casts.py. Written uppercase by
// convention (2026-08-08::DAYNAME) but matched case-insensitively.
const CAST_TARGETS = new Set([
  "boolean",
  "char",
  "date",
  "datetime",
  "day",
  "dayname",
  "decimal",
  "duration",
  "eomonth",
  "hour",
  "int",
  "minute",
  "month",
  "year",
  "monthname",
  "percent",
  "second",
  "text",
  "time",
]);

// ---------------------------------------------------------------------------
// Token patterns, in TOKEN_RE order.
// ---------------------------------------------------------------------------

const DATETIME =
  /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]) (?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?/;
const DATE = /^\d{4}-\d{2}-\d{2}/;
const TIME = /^\d{2}:\d{2}(?::\d{2})?/;
const DURATION = /^(?:\d+(?:\.\d+)?|\.\d+)(?:min|mo|[dhwsy])/;
const CURRENCY = /^\$(?:\d+(?:\.\d*)?|\.\d+)/;
const TONNAGE = /^(?:\d+(?:\.\d*)?|\.\d+)t/;
const PERCENT = /^(?:\d+(?:\.\d*)?|\.\d+)%/;
const IMAGINARY = /^(?:\d+(?:\.\d*)?|\.\d+)i/;
const INFINITY_SYMBOL = /^\u221E/;
// Deliberately looser than the engine: the closing quote is optional so a
// half-typed string doesn't turn the rest of the line red.
const STRING = /^"(?:[^"\\]|\\.)*"?/;
const HEX_CHAR = /^0[xX][0-9A-Fa-f]+/;
const NUMBER = /^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/;
const DOUBLECOLON = /^::/;
// POWER ^, FLOORDIV //, LE, GE, NE <>, then the single-character operators.
const OPERATOR = /^(?:\^|\/\/|<=|>=|<>|[<>=+\-*/%])/;
const BRACKET = /^[()[\]]/;
const SEPARATOR = /^[,;]/;
const CONTAINER = /^[A-Za-z]{3}[UuJjZz]\d{7}(?![A-Za-z0-9_])/;
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*/;

// Mirrors parse_statement's 3-token lookahead. Bounded by the line, so a let
// split across lines won't be recognised — acceptable, since a statement is
// ';'-separated and normally written on one line.
const LET_AHEAD = /^\s+[A-Za-z_][A-Za-z0-9_]*\s*=(?!=)/;

const calcParser = {
  name: "calc",

  startState: () => ({
    expectBinding: false,
    expectCastTarget: false,
    inColumnRef: false,
  }),

  token(stream, state) {
    if (stream.eatSpace()) return null;

    // Consume the one-shot flags up front so no early return can leave one
    // set for the rest of the line.
    const expectBinding = state.expectBinding;
    const expectCastTarget = state.expectCastTarget;
    state.expectBinding = false;
    state.expectCastTarget = false;

    if (stream.match(DATETIME)) return "datetime";
    if (stream.match(DATE)) return "date";
    if (stream.match(TIME)) return "time";
    if (stream.match(DURATION)) return "duration";
    if (stream.match(CURRENCY)) return "currency";
    if (stream.match(TONNAGE)) return "tonnage";
    if (stream.match(PERCENT)) return "percent";
    if (stream.match(IMAGINARY)) return "imaginary";
    if (stream.match(INFINITY_SYMBOL)) return "constant";
    if (stream.match(STRING)) return "string";
    if (stream.match(HEX_CHAR)) return "number";
    if (stream.match(NUMBER)) return "number";

    if (stream.match(DOUBLECOLON)) {
      state.expectCastTarget = true;
      return "cast";
    }

    // '//' is a comment only at the start of a line or after ';'.
    // Otherwise it remains the floor-division operator.
    if (stream.match(/^\/\//, false)) {
      const before = stream.string.slice(0, stream.pos).trimEnd();

      const isLineStart = before.length === 0;
      const isAfterStatement = before.endsWith(";");

      if (isLineStart || isAfterStatement) {
        stream.skipToEnd();
        return "comment";
      }
    }
    if (stream.match(OPERATOR)) return "operator";

    if (stream.match(BRACKET)) {
      const bracket = stream.current();
      if (bracket === "[") state.inColumnRef = true;
      else if (bracket === "]") state.inColumnRef = false;
      return "bracket";
    }

    if (stream.match(SEPARATOR)) return "separator";
    if (stream.match(CONTAINER)) return "container";
    if (stream.match(IDENTIFIER)) {
      const word = stream.current();
      const lower = word.toLowerCase();

      // A cast target only after '::' — 'date' elsewhere is just a variable.
      if (expectCastTarget) {
        return CAST_TARGETS.has(lower) ? "type" : "unknownType";
      }

      // A column name only inside '[...]'.
      if (state.inColumnRef) return "column";

      // The name being bound by a let.
      if (expectBinding) return "binding";

      // 'let' is a keyword only when a NAME and '=' follow it.
      if (word === "let" && LET_AHEAD.test(stream.string.slice(stream.pos))) {
        state.expectBinding = true;
        return "keyword";
      }

      // parse_primary only reads a call when LPAREN follows; a bare
      // identifier is a variable reference even if it shares a name with a
      // built-in. That's why 'table' in groupby(table, ...) is a variable,
      // and why the constants need call syntax: pi() not pi.
      if (/^\s*\(/.test(stream.string.slice(stream.pos))) {
        if (NULLARY.has(lower)) return "constant";
        if (FUNCTIONS.has(lower)) return "call";
        return "unknownCall";
      }

      return "name";
    }

    // Anything left matches no alternative in TOKEN_RE, which is exactly the
    // "Unexpected character" the lexer would raise.
    stream.next();
    return "invalid";
  },

  // Every name the tokenizer emits is mapped to a lezer tag explicitly, so
  // nothing depends on CodeMirror's legacy CM5 token-name mapping.
  tokenTable: {
    // Quantities — Unit.CURRENCY / TONNAGE / PERCENT.
    currency: tags.unit,
    tonnage: tags.special(tags.unit),
    percent: tags.standard(tags.unit),
    // Temporals.
    date: tags.literal,
    datetime: tags.literal,
    time: tags.literal,
    duration: tags.special(tags.literal),
    // Numerics.
    number: tags.number,
    imaginary: tags.special(tags.number),
    // Text.
    string: tags.string,

    // ISO 6346 container number.
    container: tags.atom,
    // Comments.
    comment: tags.lineComment,
    // Names.
    name: tags.variableName,
    binding: tags.definition(tags.variableName),
    column: tags.propertyName,
    // Callables and constants.
    call: tags.function(tags.variableName),
    constant: tags.constant(tags.name),
    keyword: tags.keyword,
    // Casts.
    cast: tags.modifier,
    type: tags.typeName,
    unknownType: tags.special(tags.typeName),
    unknownCall: tags.special(tags.function(tags.variableName)),
    // Structure.
    operator: tags.operator,
    bracket: tags.paren,
    separator: tags.punctuation,
    invalid: tags.invalid,
  },
};

// ---------------------------------------------------------------------------
// Colours
//
// calc is strongly typed and every result reports a category, so the editor
// colours by type family rather than by syntactic role: quantities share one
// hue, temporals another, numerics a third. What you see while typing is the
// category the engine will infer.
//
// Values are CSS custom properties from dsl_editor.css, so light and dark
// both come from this one HighlightStyle.
// ---------------------------------------------------------------------------

const calcHighlight = HighlightStyle.define([
  // Quantities: brass. Differentiated by their own visible suffix.
  { tag: tags.unit, color: "var(--calc-quantity)", fontWeight: "600" },
  {
    tag: tags.special(tags.unit),
    color: "var(--calc-quantity)",
    fontWeight: "600",
  },
  {
    tag: tags.standard(tags.unit),
    color: "var(--calc-quantity)",
    fontWeight: "600",
  },

  // Comments: green
  { tag: tags.lineComment, color: "var(--calc-comment)", fontStyle: "italic" },

  // Temporals: navy.
  { tag: tags.literal, color: "var(--calc-temporal)", fontWeight: "600" },
  { tag: tags.special(tags.literal), color: "var(--calc-temporal)" },

  // Numerics: quiet slate. Plain numbers are the least interesting literal
  // in a language whose point is dimensioned values.
  { tag: tags.number, color: "var(--calc-numeric)" },
  { tag: tags.special(tags.number), color: "var(--calc-imaginary)" },

  { tag: tags.string, color: "var(--calc-text)" },

  // ISO 6346 container numbers.
  {
    tag: tags.atom,
    color: "var(--calc-container)",
    fontWeight: "600",
  },
  // Names: ink.
  { tag: tags.variableName, color: "var(--calc-name)" },
  {
    tag: tags.definition(tags.variableName),
    color: "var(--calc-name)",
    fontWeight: "600",
  },
  { tag: tags.propertyName, color: "var(--calc-name)", fontStyle: "italic" },

  // Callables and constants: plum.
  { tag: tags.function(tags.variableName), color: "var(--calc-call)" },
  {
    tag: tags.constant(tags.name),
    color: "var(--calc-call)",
    fontStyle: "italic",
  },
  { tag: tags.keyword, color: "var(--calc-keyword)", fontWeight: "600" },

  // Casts: the '::' recedes, the target reads as the type it names.
  { tag: tags.modifier, color: "var(--calc-punctuation)" },
  { tag: tags.typeName, color: "var(--calc-type)", fontStyle: "italic" },
  {
    tag: tags.special(tags.function(tags.variableName)),
    color: "var(--calc-invalid)",
    textDecoration: "underline dotted",
  },
  {
    tag: tags.special(tags.typeName),
    color: "var(--calc-invalid)",
    fontStyle: "italic",
    textDecoration: "underline dotted",
  },

  { tag: tags.operator, color: "var(--calc-operator)" },
  { tag: tags.paren, color: "var(--calc-punctuation)" },
  { tag: tags.punctuation, color: "var(--calc-punctuation)" },
  {
    tag: tags.invalid,
    color: "var(--calc-invalid)",
    textDecoration: "underline wavy",
  },
]);

// ---------------------------------------------------------------------------
// Widget
// ---------------------------------------------------------------------------

function render({ model, el }) {
  const wrapper = document.createElement("div");
  wrapper.className = "calc-editor";
  el.appendChild(wrapper);

  // Guards the echo loop: Python sets code -> change:code fires -> the editor
  // dispatches -> the update listener sets code again.
  let applyingFromModel = false;
  let debounceTimer = null;

  function pushToPython(text) {
    if (debounceTimer !== null) clearTimeout(debounceTimer);
    const commit = () => {
      debounceTimer = null;
      model.set("code", text);
      model.save_changes();
    };
    const ms = model.get("debounce_ms") ?? 0;
    if (ms > 0) debounceTimer = setTimeout(commit, ms);
    else commit();
  }

  const view = new EditorView({
    parent: wrapper,
    state: EditorState.create({
      doc: model.get("code") ?? "",
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        bracketMatching(),
        StreamLanguage.define(calcParser),
        syntaxHighlighting(calcHighlight),
        EditorView.lineWrapping,
        EditorView.editable.of(!model.get("disabled")),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged || applyingFromModel) return;
          pushToPython(update.state.doc.toString());
        }),
      ],
    }),
  });

  function onCodeChange() {
    const incoming = model.get("code") ?? "";
    if (incoming === view.state.doc.toString()) return;
    applyingFromModel = true;
    try {
      view.dispatch({
        changes: {
          from: 0,
          to: view.state.doc.length,
          insert: incoming,
        },
      });
    } finally {
      applyingFromModel = false;
    }
  }

  model.on("change:code", onCodeChange);

  // anywidget calls this when the cell re-runs, so editors don't accumulate
  // and listeners don't leak.
  return () => {
    if (debounceTimer !== null) clearTimeout(debounceTimer);
    model.off("change:code", onCodeChange);
    view.destroy();
  };
}

export default { render };
