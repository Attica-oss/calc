import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")

with app.setup:
    import io
    import json
    import pathlib
    import re
    from contextlib import redirect_stdout
    from datetime import date, datetime, time
    from decimal import Decimal
    from io import StringIO

    import anywidget
    import marimo as mo
    import polars as pl
    import traitlets
    from scan_google_sheet import ReadSheetError, scan_google_sheet

    from engine import (
        CAST_RULES,
        FUNCTIONS,
        Array,
        Blank,
        Char,
        Column,
        Complex,
        ContainerNumber,
        Duration,
        ExpressionError,
        FunctionSpec,
        Matrix,
        Number,
        Quantity,
        Table,
        Type,
        Unit,
        Value,
        category_of,
        evaluate_script,
        format_result,
        register_cast,
        to_decimal,
    )

    EDITOR_TYPES = {
        "text": Type("text"),
        "int": Type("int"),
        "decimal": Type("decimal"),
        "boolean": Type("boolean"),
        "date": Type("date"),
        "datetime": Type("datetime"),
        "time": Type("time"),
        "currency": Type("currency"),
        "tonnage": Type("tonnage"),
        "percent": Type("percent"),
        "char": Type("char"),
    }
    EDITOR_TYPE_OPTIONS = [{"key": key, "label": key} for key in EDITOR_TYPES]

    HERE = pathlib.Path(__file__).parent
    SAVED_TABLE_DIR = HERE / ".calc_tables"


@app.cell
def _():
    header = mo.md(
        r"""
        # Calc Workbench

        Four focused workspaces for loading data, building tables, running Calc scripts,
        and prototyping engine extensions.
        """
    )
    return (header,)


@app.function
def to_calc_value(raw: Number, *, currency: bool = False, tonnage: bool = False):
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
def table_from_dataframe(df, currency_columns=frozenset(), tonnage_columns=frozenset()):
    schema = []
    columns = []
    for name in df.columns:
        series = df[name]
        if series.null_count():
            raise ExpressionError(
                f"Column {name!r} has {series.null_count()} missing value(s); "
                "fill or drop them before loading - Calc has no implicit blanks."
            )
        values = tuple(
            to_calc_value(
                raw,
                currency=name in currency_columns,
                tonnage=name in tonnage_columns,
            )
            for raw in series
        )
        schema.append((name, category_of(values[0]) if values else Type("text")))
        columns.append(values)
    return Table(schema=tuple(schema), columns=tuple(columns))


@app.function
def editor_type_name(category):
    return str(category)


@app.function
def table_editor_data(table):
    schema = [
        {"name": name, "type": editor_type_name(category)}
        for name, category in table.schema
    ]
    rows = [
        [format_result(table.columns[column][row]) for column in range(len(table.schema))]
        for row in range(table.row_count)
    ]
    return schema, rows


@app.function
def cast_editor_value(raw: str, target: Type):
    source = category_of(raw)
    if source == target:
        return raw
    target_name = str(target).lower()
    rule = CAST_RULES.get((source, target_name))
    if rule is None:
        raise ExpressionError(f"Cannot cast {source} to {target_name}.")
    result_type, impl = rule
    value = impl(raw)
    actual_type = category_of(value)
    if actual_type != result_type:
        raise ExpressionError(
            f"Cast to {target_name} produced {actual_type}, expected {result_type}."
        )
    return value


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

    for draft_row_index, raw_row in enumerate(draft.get("rows", [])):
        output_row = base.row_count + draft_row_index
        if len(raw_row) != len(base.schema):
            errors.append(
                {
                    "row": output_row,
                    "column": 0,
                    "message": f"Expected {len(base.schema)} values, got {len(raw_row)}.",
                }
            )
            continue
        for column_index, ((name, category), raw) in enumerate(zip(base.schema, raw_row)):
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

    if errors:
        raise TableEditorValidationError(errors)

    total_rows = len(columns[0]) if columns else len(draft.get("rows", []))
    known_names = {name.lower() for name, _ in schema}

    for draft_column in draft.get("columns", []):
        name = draft_column["name"].strip()
        type_key = draft_column["type"]
        raw_values = draft_column["values"]
        output_column = len(schema)

        if not name:
            errors.append({"row": 0, "column": output_column, "message": "Column name cannot be empty."})
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
                    "message": f"Column {name!r} has {len(raw_values)} values; expected {total_rows}.",
                }
            )
            continue

        values = []
        for row_index, raw in enumerate(raw_values):
            try:
                values.append(cast_editor_value(raw, category))
            except ExpressionError as error:
                errors.append(
                    {
                        "row": row_index,
                        "column": output_column,
                        "message": f"{name}: {error.message}",
                    }
                )
        if len(values) == total_rows:
            schema.append((name, category))
            columns.append(values)
            known_names.add(name.lower())

    if errors:
        raise TableEditorValidationError(errors)

    return Table(schema=tuple(schema), columns=tuple(tuple(column) for column in columns))


@app.function
def parse_schema_spec(spec: str) -> Table:
    fields = []
    seen = set()
    for part in (piece.strip() for piece in spec.split(",")):
        if not part:
            continue
        if ":" not in part:
            raise ExpressionError(f"Schema item {part!r} must use name:type.")
        name, type_name = (piece.strip() for piece in part.split(":", 1))
        if not name:
            raise ExpressionError("Column name cannot be empty.")
        key = name.lower()
        if key in seen:
            raise ExpressionError(f"Duplicate column name {name!r}.")
        category = EDITOR_TYPES.get(type_name.lower())
        if category is None:
            choices = ", ".join(EDITOR_TYPES)
            raise ExpressionError(f"Unknown type {type_name!r}. Choose from: {choices}.")
        seen.add(key)
        fields.append((name, category))
    if not fields:
        raise ExpressionError("Define at least one column, for example name:text, qty:tonnage.")
    return Table(schema=tuple(fields), columns=tuple(() for _ in fields))


@app.function
def _storage_text(value, category):
    if category == "percent" and isinstance(value, Quantity):
        return str(value.value * 100)
    if category in {"currency", "tonnage"} and isinstance(value, Quantity):
        return str(value.value)
    if category == "boolean":
        return "true" if value else "false"
    if category == "date":
        return value.isoformat()
    if category == "datetime":
        return value.isoformat(sep=" ")
    if category == "time":
        return value.isoformat()
    if category == "decimal":
        return str(value)
    if category == "char" and isinstance(value, Char):
        return chr(value.codepoint)
    return str(value)


@app.function
def table_to_payload(table):
    return {
        "version": 1,
        "schema": [{"name": name, "type": str(category)} for name, category in table.schema],
        "rows": [
            [
                _storage_text(table.columns[column][row], str(table.schema[column][1]))
                for column in range(len(table.schema))
            ]
            for row in range(table.row_count)
        ],
    }


@app.function
def table_from_payload(payload):
    schema = []
    for field in payload.get("schema", []):
        type_name = field["type"]
        if type_name not in EDITOR_TYPES:
            raise ExpressionError(f"Saved table uses unsupported editor type {type_name!r}.")
        schema.append((field["name"], EDITOR_TYPES[type_name]))
    rows = payload.get("rows", [])
    columns = [[] for _ in schema]
    for row_index, row in enumerate(rows):
        if len(row) != len(schema):
            raise ExpressionError(f"Saved row {row_index + 1} does not match the schema.")
        for column_index, ((_, category), raw) in enumerate(zip(schema, row)):
            columns[column_index].append(cast_editor_value(str(raw), category))
    return Table(schema=tuple(schema), columns=tuple(tuple(column) for column in columns))


@app.function
def save_table(table, name: str):
    clean_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    if not clean_name:
        raise ExpressionError("Give the table a name before saving it.")
    SAVED_TABLE_DIR.mkdir(parents=True, exist_ok=True)
    path = SAVED_TABLE_DIR / f"{clean_name}.json"
    path.write_text(json.dumps(table_to_payload(table), indent=2), encoding="utf-8")
    return path


@app.function
def load_saved_table(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return table_from_payload(payload)


@app.cell
def _():
    class CalcTableEditor(anywidget.AnyWidget):
        _esm = HERE / "table_editor.js"
        _css = HERE / "table_editor.css"
        schema = traitlets.List(traitlets.Dict()).tag(sync=True)
        rows = traitlets.List(traitlets.List()).tag(sync=True)
        types = traitlets.List(traitlets.Dict()).tag(sync=True)
        draft = traitlets.Dict(default_value={"rows": [], "columns": []}).tag(sync=True)
        errors = traitlets.List(traitlets.Dict()).tag(sync=True)
        disabled = traitlets.Bool(False).tag(sync=True)

    class CalcEditor(anywidget.AnyWidget):
        _esm = HERE / "dsl_editor.js"
        _css = HERE / "dsl_editor.css"
        code = traitlets.Unicode("").tag(sync=True)
        debounce_ms = traitlets.Int(250).tag(sync=True)
        disabled = traitlets.Bool(False).tag(sync=True)

    return CalcEditor, CalcTableEditor


@app.function
def render_value(value, category):
    if isinstance(value, Table):
        headers = [name for name, _ in value.schema]
        data = {
            name: [format_result(value.columns[i][row]) for row in range(value.row_count)]
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
def evaluate_panel(expr, variables=None, require_variables=False):
    variables = variables or {}
    if not expr.strip():
        return mo.callout("Write a Calc expression or script.", kind="info")
    if require_variables and not variables:
        return mo.callout("Load or build a table first.", kind="info")
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
    csv_upload = mo.ui.file(filetypes=[".csv"], kind="area", label="Upload a CSV")
    sheet_url_input = mo.ui.text(
        label="...or a Google Sheets link",
        placeholder="https://docs.google.com/spreadsheets/d/...",
        full_width=True,
    )
    sheet_name_input = mo.ui.text(label="Sheet tab", placeholder="Sheet1")
    currency_input = mo.ui.text(
        label="Currency columns (comma-separated)", placeholder="price, cost", full_width=True
    )
    tonnage_input = mo.ui.text(
        label="Tonnage columns (comma-separated)", placeholder="weight, cargo, tonnage", full_width=True
    )
    load_controls = mo.vstack(
        [csv_upload, sheet_url_input, sheet_name_input, currency_input, tonnage_input]
    )
    return csv_upload, currency_input, load_controls, sheet_name_input, sheet_url_input, tonnage_input


@app.cell
def _(csv_upload, currency_input, sheet_name_input, sheet_url_input, tonnage_input):
    currency_columns = {name.strip() for name in currency_input.value.split(",") if name.strip()}
    tonnage_columns = {name.strip() for name in tonnage_input.value.split(",") if name.strip()}
    sheet_url = sheet_url_input.value.strip()
    sheet_name = sheet_name_input.value.strip()

    if csv_upload.value:
        source = csv_upload.value[0].name
    elif sheet_url and sheet_name:
        source = f"Google Sheet ({sheet_name})"
    else:
        source = None

    data = None
    if source is None:
        hint = "Enter the sheet tab name too." if sheet_url else "Upload a CSV, or paste a Google Sheets link and its tab name."
        load_status = mo.callout(hint, kind="info")
    else:
        try:
            if csv_upload.value:
                df = pl.read_csv(io.BytesIO(csv_upload.value[0].contents), try_parse_dates=True)
            else:
                df = scan_google_sheet(sheet_name, url=sheet_url).collect()
            data = table_from_dataframe(
                df,
                currency_columns=currency_columns,
                tonnage_columns=tonnage_columns,
            )
        except (ExpressionError, pl.exceptions.PolarsError, ReadSheetError) as error:
            data = None
            load_status = mo.callout(str(error), kind="warn")
        else:
            load_status = mo.callout(
                f"Loaded {source} as `data` - {data.row_count:,} rows, {len(data.schema):,} columns.",
                kind="success",
            )
    return data, load_status


@app.cell
def _(CalcEditor):
    load_expr_input = mo.ui.anywidget(
        CalcEditor(
            code='data\n// Example: filter(data, [tonnage] > 10t)',
            debounce_ms=300,
        )
    )
    return (load_expr_input,)


@app.cell
def _(data, load_expr_input):
    load_variables = {"data": data} if data is not None else {}
    load_code_result = evaluate_panel(
        load_expr_input.value["code"],
        variables=load_variables,
        require_variables=True,
    )
    source_preview = (
        render_value(data, category_of(data))
        if data is not None
        else mo.callout("No source table loaded yet.", kind="info")
    )
    return load_code_result, source_preview


@app.cell
def _():
    SAVED_TABLE_DIR.mkdir(parents=True, exist_ok=True)
    saved_names = sorted(path.stem for path in SAVED_TABLE_DIR.glob("*.json"))
    saved_table_select = mo.ui.dropdown(
        options=["(new table)", *saved_names],
        value="(new table)",
        label="Open saved table",
    )
    schema_input = mo.ui.text(
        label="Empty table schema",
        value="date:date, vessel:text, tonnage:tonnage",
        placeholder="name:text, qty:tonnage, price:currency",
        full_width=True,
    )
    table_name_input = mo.ui.text(label="Save as", value="scratch_table")
    save_button = mo.ui.run_button(label="Save table")
    builder_controls = mo.vstack([saved_table_select, schema_input, table_name_input, save_button])
    return builder_controls, save_button, saved_table_select, schema_input, table_name_input


@app.cell
def _(saved_table_select, schema_input):
    builder_base = None
    try:
        if saved_table_select.value != "(new table)":
            builder_base = load_saved_table(SAVED_TABLE_DIR / f"{saved_table_select.value}.json")
            builder_base_status = mo.callout(
                f"Opened saved table `{saved_table_select.value}`.", kind="success"
            )
        else:
            builder_base = parse_schema_spec(schema_input.value)
            builder_base_status = mo.callout(
                f"Empty table ready - {len(builder_base.schema)} typed columns.", kind="success"
            )
    except (ExpressionError, OSError, ValueError, json.JSONDecodeError) as error:
        builder_base = None
        builder_base_status = mo.callout(str(error), kind="warn")
    return builder_base, builder_base_status


@app.cell
def _(CalcTableEditor, builder_base):
    if builder_base is None:
        builder_editor = None
        builder_editor_widget = None
        builder_surface = mo.callout("Define a valid schema or open a saved table.", kind="info")
    else:
        schema, rows = table_editor_data(builder_base)
        builder_editor_widget = CalcTableEditor(
            schema=schema,
            rows=rows,
            types=EDITOR_TYPE_OPTIONS,
        )
        builder_editor = mo.ui.anywidget(builder_editor_widget)
        builder_surface = builder_editor
    return builder_editor, builder_editor_widget, builder_surface


@app.cell
def _(builder_base, builder_editor, builder_editor_widget):
    if builder_base is None or builder_editor is None or builder_editor_widget is None:
        builder_table = None
        builder_status = mo.callout("No editable table yet.", kind="info")
    else:
        try:
            builder_table = apply_table_editor(builder_base, builder_editor.value["draft"])
        except TableEditorValidationError as error:
            builder_editor_widget.errors = error.errors
            builder_table = builder_base
            builder_status = mo.callout(
                "Some cells cannot be cast yet. The last fully valid table is still active.",
                kind="warn",
            )
        else:
            builder_editor_widget.errors = []
            builder_status = mo.callout(
                f"Typed table: {builder_table.row_count:,} rows, {len(builder_table.schema):,} columns.",
                kind="success",
            )
    return builder_status, builder_table


@app.cell
def _(CalcEditor):
    build_expr_input = mo.ui.anywidget(CalcEditor(code="data", debounce_ms=300))
    return (build_expr_input,)


@app.cell
def _(build_expr_input, builder_table):
    build_variables = {"data": builder_table} if builder_table is not None else {}
    build_code_result = evaluate_panel(
        build_expr_input.value["code"],
        variables=build_variables,
        require_variables=True,
    )
    return (build_code_result,)


@app.cell
def _(builder_table, save_button, table_name_input):
    if not save_button.value:
        save_status = mo.callout(
            f"Saved tables are stored in `{SAVED_TABLE_DIR.name}/` beside this notebook.",
            kind="info",
        )
    elif builder_table is None:
        save_status = mo.callout("There is no valid table to save.", kind="warn")
    else:
        try:
            path = save_table(builder_table, table_name_input.value)
        except (ExpressionError, OSError) as error:
            save_status = mo.callout(str(error), kind="warn")
        else:
            save_status = mo.callout(f"Saved `{path.name}`.", kind="success")
    return (save_status,)


@app.cell
def _(CalcEditor):
    script_expr_input = mo.ui.anywidget(
        CalcEditor(
            code="let rate = $38.75;\nlet qty = 12.5t;\nrate * qty::DECIMAL",
            debounce_ms=300,
        )
    )
    return (script_expr_input,)


@app.cell
def _(script_expr_input):
    script_code_result = evaluate_panel(script_expr_input.value["code"])
    return (script_code_result,)


@app.function
def run_extension_lab(definition: str, test_code: str):
    namespace = {
        "CAST_RULES": CAST_RULES,
        "FUNCTIONS": FUNCTIONS,
        "Array": Array,
        "Blank": Blank,
        "Char": Char,
        "Column": Column,
        "Complex": Complex,
        "ContainerNumber": ContainerNumber,
        "Decimal": Decimal,
        "Duration": Duration,
        "ExpressionError": ExpressionError,
        "FunctionSpec": FunctionSpec,
        "Matrix": Matrix,
        "Quantity": Quantity,
        "Table": Table,
        "Type": Type,
        "Unit": Unit,
        "Value": Value,
        "category_of": category_of,
        "evaluate_script": evaluate_script,
        "format_result": format_result,
        "register_cast": register_cast,
        "to_decimal": to_decimal,
        "date": date,
        "datetime": datetime,
        "time": time,
    }
    stream = StringIO()
    try:
        with redirect_stdout(stream):
            exec(definition, namespace, namespace)
            exec(test_code, namespace, namespace)
    except Exception as error:
        output = stream.getvalue()
        return mo.vstack(
            [
                mo.callout(f"{type(error).__name__}: {error}", kind="warn"),
                mo.md(f"```text\n{output}\n```") if output else mo.md(""),
            ]
        )
    output = stream.getvalue().rstrip() or "Extension loaded and tests completed without output."
    return mo.vstack([
        mo.callout("Extension test passed.", kind="success"),
        mo.md(f"```text\n{output}\n```")
    ])


@app.cell
def _():
    function_template = '''def _double_result(categories, node):
    if categories != ["decimal"]:
        raise ExpressionError("double() requires a decimal.")
    return "decimal"


def _double_impl(values):
    return values[0] * 2


FUNCTIONS["double"] = FunctionSpec(
    "double", 1, 1, False, _double_result, _double_impl
)
'''
    cast_template = '''register_cast(
    "text",
    "uppertext",
    "text",
    lambda value: value.upper(),
)
'''
    type_template = '''from dataclasses import dataclass

@dataclass(frozen=True)
class ExampleCode:
    value: str


def example_category(value):
    if isinstance(value, ExampleCode):
        return Type("example_code")
    return category_of(value)
'''

    extension_kind = mo.ui.dropdown(
        options={"Function": "function", "Cast": "cast", "Type prototype": "type"},
        value="function",
        label="Extension kind",
    )
    extension_definition = mo.ui.text_area(
        label="Python definition",
        value=function_template,
        rows=18,
        full_width=True,
    )
    extension_test = mo.ui.text_area(
        label="Test code",
        value='result = evaluate_script("double(2.5)")\nprint(result.value, result.category)',
        rows=7,
        full_width=True,
    )
    extension_run = mo.ui.run_button(label="Run extension test")
    extension_controls = mo.vstack(
        [
            extension_kind,
            mo.md(
                "**Templates:** Function registrations can be exercised through Calc immediately; "
                "casts use the live cast registry. New runtime types require coordinated engine changes, "
                "so this lab prototypes their value object/category behavior before moving code into `engine/`."
            ),
            extension_definition,
            extension_test,
            extension_run,
        ]
    )
    return cast_template, extension_controls, extension_definition, extension_kind, extension_run, extension_test, function_template, type_template


@app.cell
def _(cast_template, extension_kind, function_template, type_template):
    starter = {
        "function": function_template,
        "cast": cast_template,
        "type": type_template,
    }[extension_kind.value]
    extension_hint = mo.md(
        "### Starter\n"
        "Copy this into **Python definition** when you want a fresh template.\n\n"
        f"```python\n{starter}\n```"
    )
    return (extension_hint,)


@app.cell
def _(extension_definition, extension_run, extension_test):
    extension_result = (
        run_extension_lab(extension_definition.value, extension_test.value)
        if extension_run.value
        else mo.callout("Edit the definition and test, then run the experiment.", kind="info")
    )
    return (extension_result,)


@app.cell
def _(
    build_code_result,
    build_expr_input,
    builder_base_status,
    builder_controls,
    builder_status,
    builder_surface,
    extension_controls,
    extension_hint,
    extension_result,
    header,
    load_code_result,
    load_controls,
    load_expr_input,
    load_status,
    save_status,
    script_code_result,
    script_expr_input,
    source_preview,
):
    load_workspace = mo.vstack(
        [
            mo.md("## Load & manipulate"),
            mo.md("Load external data as `data`, inspect it, then transform it with Calc."),
            load_controls,
            load_status,
            mo.md("### Source table"),
            source_preview,
            mo.md("### Calc editor"),
            load_expr_input,
            load_code_result,
        ]
    )

    build_workspace = mo.vstack(
        [
            mo.md("## Table builder"),
            mo.md(
                "Create an empty typed table from a schema, add rows/columns in the grid, "
                "manipulate it as `data`, and persist the result to typed JSON."
            ),
            builder_controls,
            builder_base_status,
            builder_status,
            builder_surface,
            save_status,
            mo.md("### Calc editor"),
            build_expr_input,
            build_code_result,
        ]
    )

    script_workspace = mo.vstack(
        [
            mo.md("## Calc console"),
            mo.md("A code-only scratchpad with no implicit table variables."),
            script_expr_input,
            script_code_result,
        ]
    )

    extension_workspace = mo.vstack(
        [
            mo.md("## Extension lab"),
            mo.md("Prototype functions, casts, and new value types against the live engine APIs."),
            extension_controls,
            extension_hint,
            extension_result,
        ]
    )

    workspace_tabs = mo.ui.tabs(
        {
            "1. Load data": load_workspace,
            "2. Build table": build_workspace,
            "3. Code": script_workspace,
            "4. Extend engine": extension_workspace,
        }
    )

    mo.vstack([header, workspace_tabs])


if __name__ == "__main__":
    app.run()
