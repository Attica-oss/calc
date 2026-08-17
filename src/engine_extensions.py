import marimo

__generated_with = "0.23.16"
app = marimo.App(width="columns")

with app.setup:
    from datetime import date
    import polars as pl
    from engine import Quantity,format_result,Unit,evaluate_expression,ExpressionError
    from decimal import Decimal
    import marimo as mo


@app.cell
def _():
    pl.DataFrame(
        data={
            "date": [date(2026, 6, 1), date(2026, 7, 23), date(2026, 8, 17)],
            "value": [25.5, 16, 18.9],
        }
    )
    return


@app.cell
def _():
    table_1 = 'table(column("date",2026-06-01,2026-07-23,2026-08-17) ,column("value",25.5,16.0,18.9))'
    return


@app.function
def process(expr):
    try:
        _result = evaluate_expression(expr)

        _parts = [
            mo.md(f"`{format_result(_result.value)}`"),
            mo.md(f"Type: `{_result.category}`"),
        ]

        _output = mo.vstack(_parts)
    except ExpressionError as _error:
        if _error.position is not None:
            _pointer = " " * _error.position + "^"
            _output = mo.vstack(
                [
                    mo.callout(_error.message, kind="warn"),
                    mo.md(f"```text\n{expr}\n{_pointer}\n```"),
                ]
            )
        else:
            _output = mo.callout(_error.message, kind="warn")

    return _output


@app.cell
def _():
    process('column("numbers",25.5,16.0,18.9)')
    return


@app.cell
def _(values):
    process(values)
    return


@app.cell
def _():
    values = 'array(25.5,16.0,18.9)'
    values2 = 'array(15.6,15.2,15.0)'
    return values, values2


@app.cell
def _(values, values2):
    from engine.operators import array_add

    array_add(values,values2)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
