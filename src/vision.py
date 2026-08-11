import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium", auto_download=["html"])

with app.setup:
    import csv
    import io
    from datetime import date
    from decimal import Decimal

    import marimo as mo

    from engine import (
        FUNCTIONS,
        Complex,
        ExpressionError,
        Quantity,
        Unit,
        category_of,
        compare_key,
        evaluate_expression,
        format_result,
    )


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Testing ROADMAP.md
    """)
    return


@app.cell(hide_code=True)
def _():
    overview = mo.md(r"""
    Two parts, both driving the real engine (`src/engine`) — nothing here
    is reimplemented or mocked at the type-checking level.

    **Scalar playground** exercises the engine as it exists today, with
    widget-driven inputs instead of hand-typed variables.

    **Table workflow** is a UI mockup of the Phase 2 milestone (typed CSV
    import → filter/select/extend/groupby/sort → export) built *on top
    of* the engine's real `table`/`column`/`text` types, which now exist
    (see `column()`, `table()`, `rowcount()`, `::colname` field access,
    and `sum`/`avg`/`min`/`max` over a column in `src/engine`). The row
    → row filter/extend step and the six verbs (select, groupby, sort,
    ...) are still this notebook's own bookkeeping, not engine
    primitives — the engine doesn't have those verbs yet, only the
    `Table` value and field access. Every typed cell, every filter/extend
    expression, and the tables shown under "real engine Table" are
    genuine engine values, not Python dicts standing in for them.

    Older revision of this notebook (before `text`/`table` landed) had to
    exclude text columns from row-scope expressions, because the engine
    had no string type — that gap is closed now, and it shows below:
    every declared column type, including `text`, participates in
    filter/extend on equal footing.
    """)
    return (overview,)


@app.cell
def _():
    price_amt = mo.ui.number(value=12.50, step=0.01, label="price ($/t)")
    qty = mo.ui.number(value=3, step=1, label="qty")
    shipment_t = mo.ui.number(value=2.4, step=0.1, label="shipment (t)")
    start_date = mo.ui.date(value=date(2026, 1, 15), label="start")
    z_re = mo.ui.number(value=2, step=1, label="z — real part")
    z_im = mo.ui.number(value=3, step=1, label="z — imaginary part")
    vessel_name = mo.ui.text(value="Njord", label="vessel (text)")

    variable_widgets_ui = mo.hstack(
        [
            mo.vstack([mo.md("**Currency / tonnage**"), price_amt, qty, shipment_t]),
            mo.vstack([mo.md("**Date / text**"), start_date, vessel_name]),
            mo.vstack([mo.md("**Complex**"), z_re, z_im]),
        ],
        justify="start",
        gap=2,
    )
    return (
        price_amt,
        qty,
        shipment_t,
        start_date,
        variable_widgets_ui,
        vessel_name,
        z_im,
        z_re,
    )


@app.cell
def _(price_amt, qty, shipment_t, start_date, vessel_name, z_im, z_re):
    # These widgets drive the *same* variables dict the original
    # notebook.py hardcodes — tweak a slider, every example below
    # re-evaluates against the new value.
    variables = {
        "price": Quantity(Decimal(str(price_amt.value)), Unit.CURRENCY),
        "qty": int(qty.value),
        "shipment": Quantity(Decimal(str(shipment_t.value)), Unit.TONNAGE),
        "start": start_date.value,
        "vessel": vessel_name.value,
        "z": Complex(Decimal(str(z_re.value)), Decimal(str(z_im.value))),
    }

    _rows = "\n".join(
        f"| `{name}` | `{format_result(value)}` | {category_of(value)} |"
        for name, value in variables.items()
    )

    variables_table_ui = mo.md(
        f"**Live variable bindings**\n\n| Name | Value | Type |\n| --- | --- | --- |\n{_rows}"
    )
    return variables, variables_table_ui


@app.cell
def _():
    EXAMPLES = [
        "price * qty + $4.99",
        "price * shipment",
        "shipment * 4",
        "avg($10, $12, $15.50)",
        "start + 6mo - 1d",
        "days_between(start, today())",
        "if(shipment > 2t, $100, $150)",
        "sum(2h, 45min, 30min)",
        "price - price * 15%",
        "ceil(3h + 20min, 1h)",
        "z * conj(z)",
        "abs(z)",
        "coalesce(blank(), $0)",
        "infinity() > 10 ** 100",
        'vessel + " (" + qty::text + " boxes)"',
        'table(column("vessel", vessel), column("qty", qty))',
        "$5 + 3  # fails: currency + plain number",
        "price + shipment  # fails: currency + tonnage isn't addition",
        "if(1, 2, 3)  # fails: condition must be boolean",
        "blank() + 5  # fails: blank has no arithmetic",
        'vessel - "x"  # fails: text has no subtraction',
    ]
    return (EXAMPLES,)


@app.cell
def _(EXAMPLES):
    get_expr, set_expr = mo.state("price * qty + $4.99")

    example_picker = mo.ui.dropdown(
        options=EXAMPLES,
        value=None,
        label="Load an example (last few fail on purpose)",
        on_change=lambda choice: set_expr(choice.split("  #")[0]) if choice else None,
    )

    expression_input = mo.ui.text(
        value=get_expr(),
        label="Expression",
        placeholder="Enter a calculation",
        full_width=True,
        on_change=set_expr,
    )

    expression_ui = mo.vstack([example_picker, expression_input])
    return expression_input, expression_ui


@app.cell
def _(expression_input, variables):
    try:
        _result = evaluate_expression(expression_input.value, variables)

        _parts = [
            mo.md(f"### Result: `{format_result(_result.value)}`"),
            mo.md(f"Type: `{_result.category}`"),
        ]

        if _result.variables:
            _names = ", ".join(f"`{name}`" for name in _result.variables)
            _parts.append(mo.md(f"Depends on: {_names}"))

        scalar_result_ui = mo.vstack(_parts)
    except ExpressionError as _error:
        if _error.position is not None:
            _pointer = " " * _error.position + "^"
            scalar_result_ui = mo.vstack(
                [
                    mo.callout(_error.message, kind="warn"),
                    mo.md(f"```text\n{expression_input.value}\n{_pointer}\n```"),
                ]
            )
        else:
            scalar_result_ui = mo.callout(_error.message, kind="warn")
    return (scalar_result_ui,)


@app.cell
def _(
    expression_ui,
    scalar_result_ui,
    variable_widgets_ui,
    variables_table_ui,
):
    scalar_tab = mo.vstack(
        [variable_widgets_ui, variables_table_ui, expression_ui, scalar_result_ui],
        gap=1.5,
    )
    return (scalar_tab,)


@app.cell
def _():
    SAMPLE_CSV = (
        "vessel,species,catch_date,qty,price\n"
        "Njord,cod,2026-01-05,3.4,450\n"
        "Njord,haddock,2026-01-05,1.2,380\n"
        "Selkie,cod,2026-01-06,2.1,455\n"
        "Selkie,herring,2026-01-06,5.0,120\n"
        "Mira,cod,2026-01-07,4.4,448\n"
        "Mira,haddock,2026-01-07,,390\n"
        "Njord,herring,2026-01-08,3.0,125\n"
        "Selkie,cod,2026-01-08,bad,460\n"
    )
    # The last two rows are deliberately broken (a blank qty, a
    # non-numeric qty) — this is a "catch report" in the roadmap's
    # target persona sense, and messy operational data is the point.
    TYPE_OPTIONS = ["text", "int", "decimal", "currency", "tonnage", "percent", "date"]
    return SAMPLE_CSV, TYPE_OPTIONS


@app.cell
def _():
    csv_upload = mo.ui.file(
        filetypes=[".csv"],
        label="Upload a CSV (optional — sample catch-report data is used otherwise)",
    )
    return (csv_upload,)


@app.cell
def _(SAMPLE_CSV, csv_upload):
    if csv_upload.value:
        _raw_text = csv_upload.value[0].contents.decode("utf-8")
    else:
        _raw_text = SAMPLE_CSV

    _reader = csv.DictReader(io.StringIO(_raw_text))
    raw_rows = list(_reader)
    columns = _reader.fieldnames or []

    raw_preview_ui = mo.vstack(
        [
            mo.md("**Raw import** — typed strings only, nothing evaluated yet."),
            mo.ui.table(raw_rows, selection=None, page_size=10),
        ]
    )
    return columns, raw_preview_ui, raw_rows


@app.cell
def _(TYPE_OPTIONS, columns):
    _default_guess = {"qty": "tonnage", "price": "currency", "catch_date": "date"}

    column_types = mo.ui.dictionary(
        {
            col: mo.ui.dropdown(
                options=TYPE_OPTIONS,
                value=_default_guess.get(col, "text"),
                label=col,
            )
            for col in columns
        }
    )
    return (column_types,)


@app.cell
def _(column_types, raw_rows):
    def _literal(raw, declared):
        raw = raw.strip()

        if declared == "text":
            # calc's own string-literal syntax, quoting whatever the
            # cell contains — this is the change from the pre-`text`
            # revision of this notebook, which passed text cells
            # through as raw Python strings, untouched by the engine.
            escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        if declared == "currency":
            return f"${raw}"

        if declared == "tonnage":
            return f"{raw}t"

        if declared == "percent":
            return f"{raw}%"

        return raw  # int / decimal / date all use the engine's own literal syntax

    typed_rows = []
    import_errors = []

    for _i, _row in enumerate(raw_rows):
        _typed = {}

        for _col, _raw in _row.items():
            _declared = column_types.value[_col]

            try:
                _typed[_col] = evaluate_expression(_literal(_raw, _declared)).value
            except ExpressionError as _err:
                _typed[_col] = None
                import_errors.append(
                    {
                        "row": _i,
                        "column": _col,
                        "raw": _raw,
                        "declared": _declared,
                        "error": _err.message,
                    }
                )

        typed_rows.append(_typed)
    return import_errors, typed_rows


@app.cell
def _(columns, import_errors, typed_rows):
    def _display(value):
        if value is None:
            return "—"
        if isinstance(value, str):
            return value
        try:
            return format_result(value)
        except (ValueError, TypeError):
            return str(value)

    _display_rows = [{col: _display(row.get(col)) for col in columns} for row in typed_rows]

    _parts = [
        mo.md(
            "**Typed import** — every cell, including `text`, went "
            "through `evaluate_expression`; nothing here is faked."
        ),
        mo.ui.table(_display_rows, selection=None, page_size=10),
    ]

    if import_errors:
        _msg = "\n".join(
            f"- row {e['row']}, `{e['column']}` (declared `{e['declared']}`): "
            f"`{e['raw']!r}` — {e['error']}"
            for e in import_errors
        )
        _parts.append(
            mo.callout(
                mo.md(
                    f"**{len(import_errors)} cell(s) failed to import — loud, "
                    f"not silently blanked (roadmap: \"blank only by explicit "
                    f"opt-in\"):**\n\n{_msg}"
                ),
                kind="warn",
            )
        )
    else:
        _parts.append(mo.callout("All cells imported cleanly.", kind="success"))

    typed_preview_ui = mo.vstack(_parts)
    return (typed_preview_ui,)


@app.cell
def _(columns, import_errors, typed_rows):
    # A real engine Table, built via the actual column()/table()
    # functions from src/engine — not a Python dict standing in for
    # one. Only attempted once every cell in every column imported
    # cleanly, since a Table's columns can't have holes in them.
    if not typed_rows or import_errors:
        raw_table = None
        raw_table_ui = mo.callout(
            "Fix the failed cell(s) above to build a real engine `Table` "
            "from this data.",
            kind="info",
        )
    else:
        _cols = [
            FUNCTIONS["column"].impl([name, *(row[name] for row in typed_rows)])
            for name in columns
        ]
        raw_table = FUNCTIONS["table"].impl(_cols)
        raw_table_ui = mo.vstack(
            [
                mo.md(
                    f"**A real `engine.Table`**, type `{category_of(raw_table)}`, "
                    f"{raw_table.row_count} rows — this is `format_result()` "
                    "rendering the *actual* engine value, the same function "
                    "the CLI/REPL uses:"
                ),
                mo.md(f"```text\n{format_result(raw_table)}\n```"),
            ]
        )
    return (raw_table_ui,)


@app.cell
def _(columns):
    filter_expr = mo.ui.text(
        value="qty > 2t",
        label="filter — row expression (boolean)",
        placeholder="e.g. qty > 2t",
        full_width=True,
    )
    extend_name = mo.ui.text(value="value", label="extend — new column name")
    extend_expr = mo.ui.text(
        value="qty * price",
        label="extend — row expression",
        full_width=True,
    )

    filter_extend_ui = mo.vstack(
        [
            mo.md(
                "**Row-scope columns available to filter/extend:** "
                + ", ".join(f"`{c}`" for c in columns)
                + " — every declared type participates now, `text` included."
            ),
            filter_expr,
            extend_name,
            extend_expr,
        ]
    )
    return extend_expr, extend_name, filter_expr, filter_extend_ui


@app.cell
def _(columns, extend_expr, extend_name, filter_expr, typed_rows):
    def _row_env(row):
        return {c: row[c] for c in columns if row.get(c) is not None}

    processed_rows = []
    process_errors = []

    for _i, _row in enumerate(typed_rows):
        _row = dict(_row)
        _env = _row_env(_row)
        _dropped = False

        if extend_expr.value.strip():
            try:
                _val = evaluate_expression(extend_expr.value, _env).value
                _row[extend_name.value] = _val
                _env[extend_name.value] = _val
            except ExpressionError as _err:
                process_errors.append({"row": _i, "stage": "extend", "error": _err.message})
                _dropped = True

        if not _dropped and filter_expr.value.strip():
            try:
                _keep = evaluate_expression(filter_expr.value, _env).value
            except ExpressionError as _err:
                process_errors.append({"row": _i, "stage": "filter", "error": _err.message})
                _dropped = True
            else:
                _dropped = _keep is not True

        if not _dropped:
            processed_rows.append(_row)

    result_columns = list(columns) + (
        [extend_name.value]
        if extend_expr.value.strip() and extend_name.value not in columns
        else []
    )
    return process_errors, processed_rows, result_columns


@app.cell
def _(result_columns):
    sort_col = mo.ui.dropdown(
        options=result_columns, value=result_columns[0] if result_columns else None, label="sort by"
    )
    sort_desc = mo.ui.checkbox(value=False, label="descending")
    select_cols = mo.ui.multiselect(
        options=result_columns, value=result_columns, label="select — columns to keep"
    )
    sort_select_ui = mo.hstack([sort_col, sort_desc, select_cols])
    return select_cols, sort_col, sort_desc, sort_select_ui


@app.cell
def _(process_errors, processed_rows, select_cols, sort_col, sort_desc):
    def _display(value):
        if value is None:
            return "—"
        if isinstance(value, str):
            return value
        try:
            return format_result(value)
        except (ValueError, TypeError):
            return str(value)

    sorted_rows = (
        sorted(
            processed_rows,
            key=lambda r: compare_key(r.get(sort_col.value)),
            reverse=sort_desc.value,
        )
        if sort_col.value
        else processed_rows
    )

    _visible_cols = select_cols.value or []
    _display_rows = [{c: _display(r.get(c)) for c in _visible_cols} for r in sorted_rows]

    _parts = [
        mo.md(
            f"**Result** — {len(sorted_rows)} of "
            f"{len(processed_rows) + len(process_errors)} row(s) survived filter/extend."
        ),
        mo.ui.table(_display_rows, selection=None, page_size=10),
    ]

    if process_errors:
        _msg = "\n".join(f"- row {e['row']} ({e['stage']}): {e['error']}" for e in process_errors)
        _parts.append(
            mo.callout(
                mo.md(f"**{len(process_errors)} row(s) dropped by a filter/extend error:**\n\n{_msg}"),
                kind="warn",
            )
        )

    result_ui = mo.vstack(_parts)
    return result_ui, sorted_rows


@app.cell
def _(select_cols, sorted_rows):
    # The other end of the workflow: filter/extend/sort/select happen
    # in this notebook's own Python, not the engine (no verbs there
    # yet) — but the *result* is, again, rebuilt into a genuine engine
    # Table via column()/table(), not left as a Python list of dicts.
    _cols_wanted = select_cols.value or []

    if not sorted_rows or not _cols_wanted:
        final_table_ui = mo.callout("Nothing to build a table from yet.", kind="info")
    else:
        _cols = [
            FUNCTIONS["column"].impl([name, *(row[name] for row in sorted_rows)])
            for name in _cols_wanted
            if all(row.get(name) is not None for row in sorted_rows)
        ]

        if len(_cols) != len(_cols_wanted):
            final_table_ui = mo.callout(
                "Some selected columns have missing values in this result "
                "set, so a real Table can't be built from all of them.",
                kind="info",
            )
        else:
            final_table = FUNCTIONS["table"].impl(_cols)
            final_table_ui = mo.md(
                f"**Final result as a real `engine.Table`** "
                f"(`{category_of(final_table)}`):\n\n"
                f"```text\n{format_result(final_table)}\n```"
            )
    return (final_table_ui,)


@app.cell
def _(processed_rows, result_columns):
    groupby_col = mo.ui.dropdown(
        options=result_columns, value=result_columns[0] if result_columns else None, label="group by"
    )
    _agg_candidates = [
        c for c in result_columns if processed_rows and not isinstance(processed_rows[0].get(c), str)
    ]
    agg_col = mo.ui.dropdown(
        options=_agg_candidates,
        value=_agg_candidates[0] if _agg_candidates else None,
        label="aggregate column",
    )
    agg_fn = mo.ui.dropdown(
        options=["sum", "avg", "min", "max", "count"], value="sum", label="aggregate function"
    )
    groupby_widgets_ui = mo.hstack([groupby_col, agg_col, agg_fn])
    return agg_col, agg_fn, groupby_col, groupby_widgets_ui


@app.cell
def _(agg_col, agg_fn, groupby_col, processed_rows):
    if not groupby_col.value:
        groupby_ui = mo.md("_No rows to group._")
    else:
        _groups: dict = {}
        for _row in processed_rows:
            _groups.setdefault(_row.get(groupby_col.value), []).append(_row)

        _summary = []
        _agg_errors = []
        for _key, _rows in _groups.items():
            _display_val = "—"
            if agg_fn.value == "count":
                _display_val = format_result(len(_rows))
            elif agg_col.value:
                _values = [r[agg_col.value] for r in _rows if r.get(agg_col.value) is not None]
                if _values:
                    # Routed through a real engine Column, not a bare
                    # list, so this hits the same column-aware branch
                    # sum()/avg()/min()/max() use for sum(t::col) —
                    # the exact machinery this workflow is meant to
                    # demonstrate, not a parallel implementation of it.
                    _column = FUNCTIONS["column"].impl([agg_col.value, *_values])
                    try:
                        _display_val = format_result(FUNCTIONS[agg_fn.value].impl([_column]))
                    except (TypeError, ExpressionError) as _err:
                        _display_val = "error"
                        _agg_errors.append(f"`{_key}`: {_err}")

            _label = f"{agg_fn.value}({agg_col.value})" if agg_col.value else agg_fn.value
            _summary.append({groupby_col.value: _key, _label: _display_val})

        _parts = [
            mo.md(
                "**Group-by summary** — `sum`/`avg`/`min`/`max` run on a "
                "real `Column` built via `column()`, using the same "
                "column-aggregation path `sum(t::qty)` uses in the engine."
            ),
            mo.ui.table(_summary, selection=None, page_size=10),
        ]
        if _agg_errors:
            _parts.append(
                mo.callout(
                    mo.md(
                        f"**{agg_fn.value}({agg_col.value}) isn't defined for this "
                        f"type — a real engine error, not a crash:**\n\n"
                        + "\n".join(f"- {e}" for e in _agg_errors)
                    ),
                    kind="warn",
                )
            )

        groupby_ui = mo.vstack(_parts)
    return (groupby_ui,)


@app.cell
def _(result_columns, select_cols, sorted_rows):
    def _to_csv():
        _cols = select_cols.value or result_columns
        _buf = io.StringIO()
        _writer = csv.DictWriter(_buf, fieldnames=_cols)
        _writer.writeheader()
        for _row in sorted_rows:
            _writer.writerow(
                {
                    c: (
                        _row.get(c)
                        if isinstance(_row.get(c), str) or _row.get(c) is None
                        else format_result(_row[c])
                    )
                    for c in _cols
                }
            )
        return _buf.getvalue().encode("utf-8")

    export_ui = mo.download(data=_to_csv, filename="calc_export.csv", label="Export CSV", mimetype="text/csv")
    return (export_ui,)


@app.cell
def _(
    column_types,
    csv_upload,
    export_ui,
    filter_extend_ui,
    final_table_ui,
    groupby_ui,
    groupby_widgets_ui,
    raw_preview_ui,
    raw_table_ui,
    result_ui,
    sort_select_ui,
    typed_preview_ui,
):
    table_tab = mo.accordion(
        {
            "1. Import (CSV upload, column types, typed cells, real Table)": mo.vstack(
                [
                    csv_upload,
                    raw_preview_ui,
                    mo.md("**Declare each column's type:**"),
                    column_types,
                    typed_preview_ui,
                    raw_table_ui,
                ],
                gap=1,
            ),
            "2. Filter & extend (row-scope expressions)": filter_extend_ui,
            "3. Sort, select & result (+ real Table)": mo.vstack(
                [sort_select_ui, result_ui, final_table_ui], gap=1
            ),
            "4. Group by & aggregate": mo.vstack([groupby_widgets_ui, groupby_ui], gap=1),
            "5. Export": export_ui,
        },
        multiple=True,
    )
    return (table_tab,)


@app.cell
def _(overview, scalar_tab, table_tab):
    mo.ui.tabs(
        {
            "Overview": overview,
            "Scalar playground": scalar_tab,
            "Table workflow": table_tab,
        },
        lazy=True,
    )
    return


if __name__ == "__main__":
    app.run()
