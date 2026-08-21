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
        "duration": Type("duration"),
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
def storage_text(value, category):
    category_name = str(category)
    if category_name == "percent" and isinstance(value, Quantity):
        return str(value.value * 100)
    if category_name in {"currency", "tonnage"} and isinstance(value, Quantity):
        return str(value.value)
    if category_name == "boolean":
        return "true" if value else "false"
    if category_name == "date":
        return value.isoformat()
    if category_name == "datetime":
        return value.isoformat(sep=" ")
    if category_name == "time":
        return value.isoformat()
    if category_name in {"decimal", "duration", "char"}:
        return format_result(value)
    return str(value)


@app.function
def table_editor_state(table, formulas=None):
    formulas = formulas or {}
    return {
        "schema": [
            {
                "name": name,
                "type": editor_type_name(category),
                "formula": formulas.get(name.lower(), ""),
            }
            for name, category in table.schema
        ],
        "rows": [
            [
                storage_text(table.columns[column][row], table.schema[column][1])
                for column in range(len(table.schema))
            ]
            for row in range(table.row_count)
        ],
    }


@app.function
def display_rows(table):
    return [
        [format_result(table.columns[column][row]) for column in range(len(table.schema))]
        for row in range(table.row_count)
    ]


@app.function
def cast_editor_value(raw: str, target: Type):
    source = category_of(raw)
    target_name = str(target).lower()

    if source == target:
        return raw

    rule = CAST_RULES.get((source, target_name))
    if rule is not None:
        result_type, impl = rule
        value = impl(raw)
        actual_type = category_of(value)
        if actual_type != result_type:
            raise ExpressionError(
                f"Cast to {target_name} produced {actual_type}, expected {result_type}."
            )
        return value

    # Some Calc literals (notably duration) deliberately do not have a
    # text -> type cast. Let the engine parse the cell as a Calc literal
    # before rejecting it, so a duration cell can contain e.g. 1h 30min.
    try:
        result = evaluate_script(raw)
    except ExpressionError as error:
        raise ExpressionError(f"Cannot cast text to {target_name}: {error.message}") from error

    actual_type = category_of(result.value)
    if actual_type != target:
        raise ExpressionError(f"Expected {target_name}, got {actual_type}.")
    return result.value


@app.class_definition
class TableEditorValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Invalid table data")


@app.function
def normalize_table_formula(formula: str, column_names):
    """Allow a light spreadsheet shorthand for field casts.

    Calc's canonical row reference is ``[date]::DAYNAME``. Inside the
    table builder only, ``date::DAYNAME`` is normalized to that form when
    ``date`` is an actual column name. Other expressions keep normal Calc
    syntax, so arithmetic still reads naturally as
    ``[end_time] - [start_time]``.
    """
    names = {name.lower() for name in column_names}
    pattern = re.compile(r"(?<![\w\]])([A-Za-z_][A-Za-z0-9_]*)::")

    def replace(match):
        name = match.group(1)
        if name.lower() in names:
            return f"[{name}]::"
        return match.group(0)

    return pattern.sub(replace, formula)


@app.function
def table_from_editor_state(state):
    schema_state = state.get("schema", [])
    rows = state.get("rows", [])
    errors = []
    fields = []
    seen = set()

    for column_index, field in enumerate(schema_state):
        name = str(field.get("name", "")).strip()
        type_name = str(field.get("type", "")).lower()
        formula = str(field.get("formula", "")).strip()

        if not name:
            errors.append(
                {"row": -1, "column": column_index, "message": "Column name cannot be empty."}
            )
            continue

        key = name.lower()
        if key in seen:
            errors.append(
                {
                    "row": -1,
                    "column": column_index,
                    "message": f"Duplicate column name {name!r}.",
                }
            )
            continue

        category = EDITOR_TYPES.get(type_name)
        if category is None:
            errors.append(
                {
                    "row": -1,
                    "column": column_index,
                    "message": f"Unknown Calc type {type_name!r}.",
                }
            )
            continue

        seen.add(key)
        fields.append((column_index, name, category, formula))

    if len(fields) != len(schema_state):
        raise TableEditorValidationError(errors)

    for row_index, row in enumerate(rows):
        if len(row) != len(fields):
            errors.append(
                {
                    "row": row_index,
                    "column": 0,
                    "message": f"Expected {len(fields)} values, got {len(row)}.",
                }
            )

    if errors:
        raise TableEditorValidationError(errors)

    declared_schema = tuple((name, category) for _, name, category, _ in fields)

    # Empty tables retain their declared schema. Formula validation begins
    # once there is a row to evaluate against.
    if not rows:
        return Table(
            schema=declared_schema,
            columns=tuple(() for _ in fields),
        )

    literal_fields = [field for field in fields if not field[3]]
    formula_fields = [field for field in fields if field[3]]

    if formula_fields and not literal_fields:
        raise TableEditorValidationError(
            [
                {
                    "row": -1,
                    "column": formula_fields[0][0],
                    "message": "A computed table needs at least one input column.",
                }
            ]
        )

    literal_schema = []
    literal_columns = []

    for column_index, name, category, _ in literal_fields:
        values = []
        for row_index, row in enumerate(rows):
            try:
                values.append(cast_editor_value(str(row[column_index]), category))
            except ExpressionError as error:
                errors.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "message": f"{name}: {error.message}",
                    }
                )
        literal_schema.append((name, category))
        literal_columns.append(tuple(values))

    if errors:
        raise TableEditorValidationError(errors)

    table = Table(schema=tuple(literal_schema), columns=tuple(literal_columns))

    # Computed columns are evaluated through Calc's own extend() row scope.
    # This keeps the spreadsheet editor from inventing a second expression
    # language: [date]::DAYNAME and [end_time] - [start_time] behave exactly
    # as they do in ordinary Calc source.
    for column_index, name, expected_type, formula in formula_fields:
        normalized_formula = normalize_table_formula(
            formula,
            [field_name for field_name, _ in table.schema],
        )
        try:
            result = evaluate_script(
                f"extend(data, {json.dumps(name)}, {normalized_formula})",
                variables={"data": table},
            )
        except ExpressionError as error:
            errors.append(
                {
                    "row": -1,
                    "column": column_index,
                    "message": f"{name}: {error.message}",
                }
            )
            break

        if not isinstance(result.value, Table):
            errors.append(
                {
                    "row": -1,
                    "column": column_index,
                    "message": f"{name}: formula did not produce a table column.",
                }
            )
            break

        actual_type = result.value.schema[-1][1]
        if actual_type != expected_type:
            errors.append(
                {
                    "row": -1,
                    "column": column_index,
                    "message": (
                        f"{name}: formula returns {actual_type}, "
                        f"but the column is declared {expected_type}."
                    ),
                }
            )
            break

        table = result.value

    if errors:
        raise TableEditorValidationError(errors)

    by_name = {name.lower(): i for i, (name, _) in enumerate(table.schema)}
    return Table(
        schema=declared_schema,
        columns=tuple(table.columns[by_name[name.lower()]] for _, name, _, _ in fields),
    )


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
def table_to_payload(state):
    return {
        "version": 2,
        "state": state,
    }


@app.function
def state_from_payload(payload):
    if payload.get("version") == 2:
        state = payload.get("state")
        if not isinstance(state, dict):
            raise ExpressionError("Saved table has an invalid state payload.")
        return state

    # Backward compatibility with the first typed-JSON format, which saved
    # materialized values only and had no formula metadata.
    schema = payload.get("schema", [])
    rows = payload.get("rows", [])
    return {
        "schema": [
            {
                "name": field["name"],
                "type": field["type"],
                "formula": "",
            }
            for field in schema
        ],
        "rows": [[str(value) for value in row] for row in rows],
    }


@app.function
def save_table(state, name: str):
    clean_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    if not clean_name:
        raise ExpressionError("Give the table a name before saving it.")
    SAVED_TABLE_DIR.mkdir(parents=True, exist_ok=True)
    path = SAVED_TABLE_DIR / f"{clean_name}.json"
    path.write_text(json.dumps(table_to_payload(state), indent=2), encoding="utf-8")
    return path


@app.function
def load_saved_table(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return state_from_payload(payload)


@app.cell
def _():
    class CalcTableEditor(anywidget.AnyWidget):
        _esm = HERE / "table_editor.js"
        _css = HERE / "table_editor.css"
        state = traitlets.Dict(default_value={"schema": [], "rows": []}).tag(sync=True)
        display_rows = traitlets.List(traitlets.List()).tag(sync=True)
        types = traitlets.List(traitlets.Dict()).tag(sync=True)
        errors = traitlets.List(traitlets.Dict()).tag(sync=True)
        page_size = traitlets.Int(25).tag(sync=True)
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
    return (
        builder_controls,
        save_button,
        saved_table_select,
        schema_input,
        table_name_input,
    )


@app.cell
def _(saved_table_select, schema_input):
    builder_seed_state = None
    try:
        if saved_table_select.value != "(new table)":
            builder_seed_state = load_saved_table(
                SAVED_TABLE_DIR / f"{saved_table_select.value}.json"
            )
            builder_base_status = mo.callout(
                f"Opened saved table `{saved_table_select.value}`.", kind="success"
            )
        else:
            empty_table = parse_schema_spec(schema_input.value)
            builder_seed_state = table_editor_state(empty_table)
            builder_base_status = mo.callout(
                f"Empty table ready - {len(empty_table.schema)} typed columns.", kind="success"
            )
    except (ExpressionError, OSError, ValueError, json.JSONDecodeError) as error:
        builder_seed_state = None
        builder_base_status = mo.callout(str(error), kind="warn")
    return builder_base_status, builder_seed_state


@app.cell
def _(CalcTableEditor, builder_seed_state):
    if builder_seed_state is None:
        builder_editor = None
        builder_editor_widget = None
        builder_surface = mo.callout("Define a valid schema or open a saved table.", kind="info")
    else:
        builder_editor_widget = CalcTableEditor(
            state=builder_seed_state,
            display_rows=builder_seed_state.get("rows", []),
            types=EDITOR_TYPE_OPTIONS,
            page_size=25,
        )
        builder_editor = mo.ui.anywidget(builder_editor_widget)
        builder_surface = builder_editor
    return builder_editor, builder_editor_widget, builder_surface


@app.cell
def _(builder_editor, builder_editor_widget, builder_seed_state):
    if builder_seed_state is None or builder_editor is None or builder_editor_widget is None:
        builder_state = None
        builder_table = None
        builder_status = mo.callout("No editable table yet.", kind="info")
    else:
        builder_state = builder_editor.value["state"]
        try:
            builder_table = table_from_editor_state(builder_state)
        except TableEditorValidationError as error:
            builder_editor_widget.errors = error.errors
            builder_editor_widget.display_rows = builder_state.get("rows", [])
            builder_table = None
            builder_status = mo.callout(
                "Fix the highlighted cells or formulas before using or saving this table.",
                kind="warn",
            )
        else:
            builder_editor_widget.errors = []
            builder_editor_widget.display_rows = display_rows(builder_table)
            formula_count = sum(
                bool(str(field.get("formula", "")).strip())
                for field in builder_state.get("schema", [])
            )
            builder_status = mo.callout(
                f"Typed table: {builder_table.row_count:,} rows, "
                f"{len(builder_table.schema):,} columns"
                + (f", {formula_count} computed." if formula_count else "."),
                kind="success",
            )
    return builder_state, builder_status, builder_table


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
def _(builder_state, builder_table, save_button, table_name_input):
    if not save_button.value:
        save_status = mo.callout(
            f"Saved tables are stored in `{SAVED_TABLE_DIR.name}/` beside this notebook. "
            "Column formulas are persisted and recomputed when the table is reopened.",
            kind="info",
        )
    elif builder_table is None or builder_state is None:
        save_status = mo.callout("There is no valid table to save.", kind="warn")
    else:
        try:
            path = save_table(builder_state, table_name_input.value)
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
        value="Function",
        label="Extension kind",
    )
    extension_definition = mo.ui.code_editor(
        label="Python definition",
        language="python",
        value=function_template,
        max_height=668,
        show_copy_button=True,

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
    return (
        cast_template,
        extension_controls,
        extension_definition,
        extension_kind,
        extension_run,
        extension_test,
        function_template,
        type_template,
    )


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
                "Create an empty typed table from a schema, edit every value directly, and "
                "add computed columns with Calc formulas. Use row references such as "
                "`date::DAYNAME` (or canonical `[date]::DAYNAME`) or `[end_time] - [start_time]`. Formulas and input data "
                "are persisted together."
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
        ],
        heights="equal"
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
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
