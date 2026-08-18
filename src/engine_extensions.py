import marimo

__generated_with = "0.23.16"
app = marimo.App(width="columns")

with app.setup:
    import io

    import marimo as mo
    import polars as pl
    from scan_google_sheet import ReadSheetError, scan_google_sheet

    from engine import (
        ExpressionError,
        Quantity,
        Table,
        Type,
        Unit,
        category_of,
        evaluate_script,
        format_result,
        to_decimal,
    )


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Calc Engine
    ---
    """)
    return


@app.function
def to_calc_value(raw, *, currency: bool):
    """One polars cell -> one calc value. calc has no native float —
    every decimal is a Decimal — so floats convert via their repr
    string, never the raw float, to avoid binary-fraction drift
    (Decimal(0.1) != Decimal("0.1")). int/str/date/datetime/bool are
    already calc-native and pass through unchanged.
    """

    if currency:
        return Quantity(to_decimal(str(raw)), Unit.CURRENCY)

    if isinstance(raw, bool):
        return raw

    if isinstance(raw, float):
        return to_decimal(str(raw))

    return raw


@app.function
def table_from_dataframe(df, currency_columns=frozenset()):
    """Build a calc Table straight from a polars DataFrame's rows — no
    table()/column() source string to generate, escape, or mis-format
    (a real risk once cell values are arbitrary data instead of a
    literal you typed yourself: commas, quotes, and NaNs in a
    hand-built DSL string all break the parser silently upstream of
    where the error shows up).

    Each column's calc type is read back off its own converted values
    with category_of() — the same rule the checker uses everywhere
    else — so this can never register a schema type the checker would
    then disagree with.

    Nulls are a hard error rather than an implicit blank: calc doesn't
    coerce silently (see ROADMAP.md's "loud per-cell failure" import
    goal). Fill or drop them in polars first — df.fill_null(...) or
    df.drop_nulls().
    """

    schema = []
    columns = []

    for name in df.columns:
        series = df[name]

        if series.null_count():
            raise ExpressionError(
                f"Column {name!r} has {series.null_count()} missing value(s); "
                "fill or drop them before loading — calc has no implicit blanks."
            )

        values = tuple(
            to_calc_value(raw, currency=name in currency_columns) for raw in series
        )
        schema.append((name, category_of(values[0]) if values else Type("text")))
        columns.append(values)

    return Table(schema=tuple(schema), columns=tuple(columns))


@app.cell
def _():
    csv_upload = mo.ui.file(filetypes=[".csv"], kind="area", label="Upload a CSV")
    sheet_url_input = mo.ui.text(
        label="...or a Google Sheets link",
        placeholder="https://docs.google.com/spreadsheets/d/...",
        full_width=True,
    )
    sheet_name_input = mo.ui.text(
        label="...and its tab name",
        placeholder="Sheet1",
    )
    currency_input = mo.ui.text(
        label="Currency columns (comma-separated)",
        placeholder="price, cost",
    )
    mo.vstack([csv_upload, sheet_url_input, sheet_name_input, currency_input])
    return csv_upload, currency_input, sheet_name_input, sheet_url_input


@app.cell
def _(df):
    mo.ui.table(df) if df is not None else None
    return


@app.cell
def _(csv_upload, currency_input, sheet_name_input, sheet_url_input):
    """Loads `data` from whichever source has something in it — an
    uploaded CSV takes priority over a pasted Google Sheets link, read
    via the scan_google_sheet package (public sheets only, no auth).
    Either way the result goes through the same table_from_dataframe()
    and is exposed to the code editor below as `variables["data"]`, so
    `data` and `data::<column>` both resolve there. Neither source
    filled in -> variables stays empty.
    """

    currency_columns = {
        name.strip() for name in currency_input.value.split(",") if name.strip()
    }

    sheet_url = sheet_url_input.value.strip()
    sheet_name = sheet_name_input.value.strip()

    if csv_upload.value:
        source = csv_upload.value[0].name
    elif sheet_url and sheet_name:
        source = f"Google Sheet ({sheet_name})"
    else:
        source = None

    if source is None:
        hint = (
            "Enter the sheet's tab name too (as shown on the tab in Google Sheets)."
            if sheet_url
            else "Upload a CSV, or paste a Google Sheets link and its tab name, "
            "to make it available as `data`."
        )
        mo.output.replace(mo.callout(hint, kind="info"))
        df, variables = None, {}
    else:
        try:
            if csv_upload.value:
                df = pl.read_csv(io.BytesIO(csv_upload.value[0].contents), try_parse_dates=True)
            else:
                df = scan_google_sheet(sheet_name, url=sheet_url).collect()

            data = table_from_dataframe(df, currency_columns=currency_columns)
        except (ExpressionError, pl.exceptions.PolarsError, ReadSheetError) as error:
            mo.output.replace(mo.callout(str(error), kind="warn"))
            df, variables = None, {}
        else:
            variables = {"data": data}
            mo.output.replace(mo.md(f"Loaded `{source}` as `data` — e.g. `data::<column>`"))
    return df, variables


@app.cell
def _():
    """A CodeMirror 6 editor with syntax highlighting for the calc language.

    code goes straight to evaluate_script(): newlines are just
    whitespace there, so wrapping one expression across lines works,
    but ';' is still the real statement separator between let bindings.
    """

    import pathlib

    import anywidget
    import traitlets

    _HERE = pathlib.Path(__file__).parent


    class CalcEditor(anywidget.AnyWidget):
        # Passing paths rather than strings opts into anywidget's hot reloading,
        # so edits to the .js and .css land without restarting the kernel — handy
        # while tuning the palette.
        _esm = _HERE / "dsl_editor.js"
        _css = _HERE / "dsl_editor.css"

        code = traitlets.Unicode("").tag(sync=True)

        # Milliseconds to wait after the last keystroke before notifying Python.
        # 0 syncs on every character, which re-runs the evaluator on every partial
        # expression — and calc rejects partial expressions loudly.
        debounce_ms = traitlets.Int(250).tag(sync=True)

        disabled = traitlets.Bool(False).tag(sync=True)

    return (CalcEditor,)


@app.cell
def _(CalcEditor):
    expr_input = mo.ui.anywidget(CalcEditor(code=""))
    expr_input
    return (expr_input,)


@app.function
def render_value(value, category):
    """Render one evaluated value: a sortable grid for a Table, a code
    fence for other multi-line renderings (matrices), a stat card for
    everything else.
    """

    if isinstance(value, Table):
        headers = [name for name, _ in value.schema]
        data = {
            name: [format_result(value.columns[i][row]) for row in range(value.row_count)]
            for i, name in enumerate(headers)
        }
        return mo.ui.table(
            pl.DataFrame(data),
            selection=None,
            pagination=False,
            show_data_types=False,
        )

    text = format_result(value)

    if "\n" in text:
        return mo.md(f"```text\n{text}\n```")

    return mo.stat(value=text, caption=str(category), bordered=True)


@app.function
def process(expr, variables=None):
    mo.output.clear()
    try:
        result = evaluate_script(expr, variables=variables)

        for name, value in result.bindings.items():
            mo.output.append(mo.md(f"**let {name}**"))
            mo.output.append(render_value(value, category_of(value)))

        mo.output.append(render_value(result.value, result.category))
    except ExpressionError as error:
        mo.output.append(mo.callout(error.message, kind="warn"))
        if error.position is not None:
            pointer = " " * error.position + "^"
            mo.output.append(mo.md(f"```text\n{expr}\n{pointer}\n```"))


@app.cell
def _(expr_input, variables):
    process(expr=expr_input.value["code"], variables=variables)
    return


@app.cell
def _():
    "test"[2]
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
