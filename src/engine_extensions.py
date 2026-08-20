import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")

with app.setup:
    import io
    import pathlib

    import anywidget
    import marimo as mo
    import polars as pl
    import traitlets
    from scan_google_sheet import ReadSheetError, scan_google_sheet

    from engine import (
        CAST_RULES,
        ExpressionError,
        Number,
        Quantity,
        Table,
        Type,
        Unit,
        # cast_value,
        category_of,
        evaluate_script,
        format_result,
        to_decimal,
    )

    # This is the only mapping the table editor needs to know about Calc types.
    # Add new editor-facing types here; casting remains owned by engine.cast_value.

    EDITOR_TYPES = {
        "text": Type("text"),
        "int": Type("int"),
        "decimal": Type("decimal"),
        "bool": Type("bool"),
        "chrono": Type("chrono"),
    }

    EDITOR_TYPE_OPTIONS = [{"key": key, "label": key} for key in EDITOR_TYPES]


@app.cell
def _():
    header = mo.md(
        r"""
        # Calc Engine
        Load data, build a typed table, then manipulate it with Calc.
        """
    )
    return (header,)


@app.function
def to_calc_value(
    raw: Number, *, currency: bool = False, tonnage: bool = False
):
    """One polars cell -> one calc value. calc has no native float —
    every decimal is a Decimal — so floats convert via their repr
    string, never the raw float, to avoid binary-fraction drift
    (Decimal(0.1) != Decimal("0.1")). int/str/date/datetime/bool are
    already calc-native and pass through unchanged.
    """

    if currency and tonnage:
        raise ValueError("A column cannot be both currency and tonnage.")

    if currency:
        return Quantity(to_decimal(raw), Unit.CURRENCY)

    if tonnage:
        return Quantity(to_decimal(raw), Unit.TONNAGE)

    if isinstance(raw, bool):
        return raw

    if isinstance(raw, float):
        return to_decimal(raw)

    return raw


@app.function
def table_from_dataframe(
    df, currency_columns=frozenset(), tonnage_columns=frozenset()
):
    """Build a calc Table straight from a polars DataFrame"""

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
            to_calc_value(
                raw,
                currency=name in currency_columns,
                tonnage=name in tonnage_columns,
            )
            for raw in series
        )
        schema.append(
            (name, category_of(values[0]) if values else Type("text"))
        )
        columns.append(values)

    return Table(schema=tuple(schema), columns=tuple(columns))


@app.function
def editor_type_name(category):
    """Return a JSON-safe name for a Calc type."""
    return getattr(category, "name", str(category))


@app.function
def table_editor_data(table):
    schema = [
        {
            "name": name,
            "type": editor_type_name(category),
        }
        for name, category in table.schema
    ]

    rows = [
        [
            format_result(table.columns[column][row])
            for column in range(len(table.schema))
        ]
        for row in range(table.row_count)
    ]

    return schema, rows


@app.function
def cast_editor_value(raw: str, target: Type):
    """Cast raw widget text using Calc's normal cast dispatch table."""

    source = category_of(raw)

    # No cast needed.
    if source == target:
        return raw

    target_name = str(target).lower()
    rule = CAST_RULES.get((source, target_name))

    if rule is None:
        raise ExpressionError(f"Cannot cast {source} to {target_name}.")

    result_type, impl = rule
    value = impl(raw)

    # Preserve Calc's central type-checker/evaluator invariant.
    actual_type = category_of(value)

    if actual_type != result_type:
        raise ExpressionError(
            f"Cast to {target_name} produced {actual_type}, "
            f"expected {result_type}."
        )

    return value


@app.cell
def _():
    return


@app.class_definition
class TableEditorValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Invalid table data")


@app.function
def apply_table_editor(base, draft):
    schema = list(base.schema)
    columns = [list(column) for column in base.columns]

    errors = []

    # ---------------------------------------------------------
    # APPEND ROWS
    # ---------------------------------------------------------

    for draft_row_index, raw_row in enumerate(draft.get("rows", [])):
        output_row = base.row_count + draft_row_index

        if len(raw_row) != len(base.schema):
            errors.append(
                {
                    "row": output_row,
                    "column": 0,
                    "message": (
                        f"Expected {len(base.schema)} values, "
                        f"got {len(raw_row)}."
                    ),
                }
            )
            continue

        for column_index, ((name, category), raw) in enumerate(
            zip(base.schema, raw_row)
        ):
            try:
                value = cast_editor_value(raw, category)
            except ExpressionError as error:
                errors.append(
                    {
                        "row": output_row,
                        "column": column_index,
                        "message": f"{name}: {error.message}",
                    }
                )
            else:
                columns[column_index].append(value)

    # Don't continue into columns if the rows themselves are invalid.
    if errors:
        raise TableEditorValidationError(errors)

    total_rows = len(columns[0]) if columns else 0

    # ---------------------------------------------------------
    # EXTEND COLUMNS
    # ---------------------------------------------------------

    known_names = {name.lower() for name, _ in schema}

    for draft_column_index, draft_column in enumerate(
        draft.get("columns", [])
    ):
        name = draft_column["name"].strip()
        type_key = draft_column["type"]
        raw_values = draft_column["values"]

        output_column = len(schema)

        if not name:
            errors.append(
                {
                    "row": 0,
                    "column": output_column,
                    "message": "Column name cannot be empty.",
                }
            )
            continue

        if name.lower() in known_names:
            errors.append(
                {
                    "row": 0,
                    "column": output_column,
                    "message": f"Column {name!r} already exists.",
                }
            )
            continue

        category = EDITOR_TYPES.get(type_key)

        if category is None:
            errors.append(
                {
                    "row": 0,
                    "column": output_column,
                    "message": f"Unknown Calc type {type_key!r}.",
                }
            )
            continue

        if len(raw_values) != total_rows:
            errors.append(
                {
                    "row": 0,
                    "column": output_column,
                    "message": (
                        f"Column {name!r} has {len(raw_values)} values; "
                        f"expected {total_rows}."
                    ),
                }
            )
            continue

        values = []

        for row_index, raw in enumerate(raw_values):
            try:
                value = cast_editor_value(raw, category)
            except ExpressionError as error:
                errors.append(
                    {
                        "row": row_index,
                        "column": output_column,
                        "message": f"{name}: {error.message}",
                    }
                )
            else:
                values.append(value)

        if len(values) == total_rows:
            schema.append((name, category))
            columns.append(values)
            known_names.add(name.lower())

    if errors:
        raise TableEditorValidationError(errors)

    return Table(
        schema=tuple(schema),
        columns=tuple(tuple(column) for column in columns),
    )


@app.cell
def _():
    _HERE = pathlib.Path(__file__).parent

    class CalcTableEditor(anywidget.AnyWidget):
        _esm = _HERE / "table_editor.js"
        _css = _HERE / "table_editor.css"

        # Existing table, supplied by Python and read-only in JS.
        schema = traitlets.List(traitlets.Dict()).tag(sync=True)

        rows = traitlets.List(traitlets.List()).tag(sync=True)

        # Types the user may choose for new columns.
        types = traitlets.List(traitlets.Dict()).tag(sync=True)

        # Only user-created mutations live here.
        draft = traitlets.Dict(
            default_value={
                "rows": [],
                "columns": [],
            }
        ).tag(sync=True)

        # Python validation errors displayed by JS.
        errors = traitlets.List(traitlets.Dict()).tag(sync=True)

        disabled = traitlets.Bool(False).tag(sync=True)

    class CalcEditor(anywidget.AnyWidget):
        _esm = _HERE / "dsl_editor.js"
        _css = _HERE / "dsl_editor.css"

        code = traitlets.Unicode("").tag(sync=True)
        debounce_ms = traitlets.Int(250).tag(sync=True)
        disabled = traitlets.Bool(False).tag(sync=True)

    return CalcEditor, CalcTableEditor


@app.function
def render_value(value, category):
    if isinstance(value, Table):
        headers = [name for name, _ in value.schema]
        data = {
            name: [
                format_result(value.columns[i][row])
                for row in range(value.row_count)
            ]
            for i, name in enumerate(headers)
        }
        return mo.ui.table(
            pl.DataFrame(data),
            selection=None,
            pagination=True,
            show_data_types=False,
        )

    text = format_result(value)
    if "\n" in text:
        return mo.md(f"```text\n{text}\n```")

    return mo.stat(value=text, caption=str(category), bordered=True)


@app.function
def evaluate_panel(expr, variables=None):
    """Evaluate Calc source and return a UI element instead of writing output."""
    variables = variables or {}

    if not expr.strip():
        return mo.callout(
            "Write a Calc expression here. `data` is the table for this workspace.",
            kind="info",
        )

    if not variables:
        return mo.callout("Load a table first.", kind="info")

    try:
        result = evaluate_script(expr, variables=variables)
    except ExpressionError as error:
        parts = [mo.callout(error.message, kind="warn")]
        if error.position is not None:
            pointer = " " * error.position + "^"
            parts.append(mo.md(f"```text\n{expr}\n{pointer}\n```"))
        return mo.vstack(parts)

    parts = []
    for name, value in result.bindings.items():
        parts.append(mo.md(f"**let {name}**"))
        parts.append(render_value(value, category_of(value)))

    parts.append(render_value(result.value, result.category))
    return mo.vstack(parts)


@app.cell
def _():
    # -------------------------------------------------------------------------
    # LOAD WORKSPACE
    # -------------------------------------------------------------------------

    csv_upload = mo.ui.file(
        filetypes=[".csv"],
        kind="area",
        label="Upload a CSV",
    )
    sheet_url_input = mo.ui.text(
        label="...or a Google Sheets link",
        placeholder="https://docs.google.com/spreadsheets/d/...",
        full_width=True,
    )
    sheet_name_input = mo.ui.text(
        label="Sheet tab",
        placeholder="Sheet1",
    )
    currency_input = mo.ui.text(
        label="Currency columns (comma-separated)",
        placeholder="price, cost",
        full_width=True,
    )

    tonnage_input = mo.ui.text(
        label="Tonnage columns (comma-separated)",
        placeholder="weight, cargo, tonnage",
    )

    load_controls = mo.vstack(
        [
            csv_upload,
            sheet_url_input,
            sheet_name_input,
            currency_input,
            tonnage_input,
        ]
    )
    return (
        csv_upload,
        currency_input,
        load_controls,
        sheet_name_input,
        sheet_url_input,
        tonnage_input,
    )


@app.cell
def _(
    csv_upload,
    currency_input,
    sheet_name_input,
    sheet_url_input,
    tonnage_input,
):
    currency_columns = {
        name.strip()
        for name in currency_input.value.split(",")
        if name.strip()
    }

    tonnage_columns = {
        name.strip() for name in tonnage_input.value.split(",") if name.strip()
    }

    sheet_url = sheet_url_input.value.strip()
    sheet_name = sheet_name_input.value.strip()

    if csv_upload.value:
        source = csv_upload.value[0].name
    elif sheet_url and sheet_name:
        source = f"Google Sheet ({sheet_name})"
    else:
        source = None

    data = None
    df = None

    if source is None:
        hint = (
            "Enter the sheet tab name too."
            if sheet_url
            else "Upload a CSV, or paste a Google Sheets link and its tab name."
        )
        load_status = mo.callout(hint, kind="info")
    else:
        try:
            if csv_upload.value:
                df = pl.read_csv(
                    io.BytesIO(csv_upload.value[0].contents),
                    try_parse_dates=True,
                )
            else:
                df = scan_google_sheet(sheet_name, url=sheet_url).collect()

            data = table_from_dataframe(
                df,
                currency_columns=currency_columns,
                tonnage_columns=tonnage_columns,
            )
        except (
            ExpressionError,
            pl.exceptions.PolarsError,
            ReadSheetError,
        ) as error:
            data = None
            df = None
            load_status = mo.callout(str(error), kind="warn")
        else:
            load_status = mo.callout(
                f"Loaded {source} as `data` - {data.row_count:,} rows, "
                f"{len(data.schema):,} columns.",
                kind="success",
            )
    return data, load_status


@app.cell
def _(CalcEditor):
    load_expr_input = mo.ui.anywidget(CalcEditor(code="data", debounce_ms=300))
    return (load_expr_input,)


@app.cell
def _(data, load_expr_input):
    load_variables = {"data": data} if data is not None else {}
    load_code_result = evaluate_panel(
        load_expr_input.value["code"],
        variables=load_variables,
    )
    return (load_code_result,)


@app.cell
def _(data):
    source_preview = (
        render_value(data, category_of(data))
        if data is not None
        else mo.callout("No source table loaded yet.", kind="info")
    )
    return (source_preview,)


@app.cell
def _(CalcTableEditor, data):
    # -------------------------------------------------------------------------
    # BUILD WORKSPACE
    # -------------------------------------------------------------------------

    if data is None:
        data_editor = None
        data_editor_widget = None
        editor_surface = mo.callout(
            "Load a table in the Load tab before building a new table.",
            kind="info",
        )
    else:
        schema, rows = table_editor_data(data)
        data_editor_widget = CalcTableEditor(
            schema=schema,
            rows=rows,
            types=EDITOR_TYPE_OPTIONS,
            page_size=25,
        )
        data_editor = mo.ui.anywidget(data_editor_widget)
        editor_surface = data_editor
    return data_editor, data_editor_widget, editor_surface


@app.cell
def _(data, data_editor, data_editor_widget):
    if data is None or data_editor is None or data_editor_widget is None:
        edited_data = None
        editor_status = mo.callout("No editable table yet.", kind="info")
    else:
        draft = data_editor.value["draft"]

        try:
            edited_data = apply_table_editor(data, draft)
        except TableEditorValidationError as error:
            data_editor_widget.errors = error.errors
            edited_data = data
            editor_status = mo.callout(
                "Some new cells cannot be cast yet. The Calc table is still using "
                "the last fully valid shape.",
                kind="warn",
            )
        else:
            data_editor_widget.errors = []
            editor_status = mo.callout(
                f"Typed table: {edited_data.row_count:,} rows, "
                f"{len(edited_data.schema):,} columns.",
                kind="success",
            )
    return edited_data, editor_status


@app.cell
def _(CalcEditor):
    build_expr_input = mo.ui.anywidget(
        CalcEditor(code="data", debounce_ms=300)
    )
    return (build_expr_input,)


@app.cell
def _(build_expr_input, data, edited_data):
    if edited_data is None:
        build_variables = {}
    else:
        build_variables = {
            "data": edited_data,
            "source": data,
        }

    build_code_result = evaluate_panel(
        build_expr_input.value["code"],
        variables=build_variables,
    )
    return (build_code_result,)


@app.cell
def _(CalcEditor):
    script_expr_input = mo.ui.anywidget(
        CalcEditor(debounce_ms=300)
    )


    script_code_result = scripts_output(
        script_expr_input.value["code"]
    )
    return script_code_result, script_expr_input


@app.cell
def _(script_expr_input):
    script_expr_input


@app.cell
def _(script_expr_input):
    eval = evaluate_script(format_result(script_expr_input.value["code"]))

    parts = []
    for name, value in eval.bindings.items():
        parts.append(mo.md(f"**let {name}**"))
        parts.append(render_value(value, category_of(value)))

    parts.append(render_value(eval.value, eval.category))

    parts


@app.cell
def _(script_code_result):
    script_code_result


@app.function
def scripts_output(expr):

    if not expr.strip():
        return mo.callout(
            "Write a Calc expression here. `data` is the table for this workspace.",
            kind="info",
        )

    try:
        result = evaluate_script(expr)
    except ExpressionError as error:
        parts = [mo.callout(error.message, kind="warn")]
        if error.position is not None:
            pointer = " " * error.position + "^"
            parts.append(mo.md(f"```text\n{expr}\n{pointer}\n```"))
        return mo.vstack(parts)

    parts = []
    for name, value in result.bindings.items():
        parts.append(mo.md(f"**let {name}**"))
        parts.append(render_value(value, category_of(value)))

    parts.append(render_value(result.value, result.category))
    return mo.vstack(parts)


@app.cell
def _(
    build_code_result,
    build_expr_input,
    editor_status,
    editor_surface,
    header,
    load_code_result,
    load_controls,
    load_expr_input,
    load_status,
    script_code_result,
    script_expr_input,
    source_preview,
):
    load_workspace = mo.vstack(
        [
            mo.md("## Load data"),
            mo.md(
                "Import a table, inspect the typed result, and use Calc against "
                "the loaded table as `data`."
            ),
            load_controls,
            load_status,
            mo.md("### Source table"),
            source_preview,
            mo.md("### Calc on loaded data"),
            load_expr_input,
            load_code_result,
        ]
    )

    build_workspace = mo.vstack(
        [
            mo.md("## Build table"),
            mo.md(
                "Append rows or extend columns. Imported cells stay read-only; "
                "all new input is cast by Calc before it becomes a Table."
            ),
            editor_status,
            editor_surface,
            mo.md("### Calc on built data"),
            mo.md(
                "Here `data` is the table produced by the editor and `source` "
                "is the originally loaded table."
            ),
            build_expr_input,
            build_code_result,
        ]
    )

    load_code_editor = mo.vstack([
        script_expr_input,
        script_code_result
    ])


    workspace_tabs = mo.ui.tabs(
        {
            "1. Load": load_workspace,
            "2. Build": build_workspace,
            "3. Scripts": load_code_editor
        }
    )

    mo.vstack([header, workspace_tabs])


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
