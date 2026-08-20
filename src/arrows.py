import marimo

__generated_with = "0.24.0"
app = marimo.App(width="columns")

with app.setup:
    from src.engine import (
        ExpressionError,
        Number, # type hint
        Quantity,
        Table,
        Type,
        Unit,
        # cast_value,
        category_of,
        evaluate_script,
        format_result,
        to_decimal,
        CAST_RULES,
    )
    from dataclasses import dataclass
    from decimal import Decimal

    import pyarrow as pa


@app.cell
def _():
    format_result(Table(
        schema=(
            ("a", Type("int")),
            ("b", Type("int")),
        ),
        columns=(
            (1, 2, 3),
            (4,),
        ),
    ))
    return


@app.cell
def _():
    days = pa.array([1, 12, 17, 23, 28], type=pa.int8())

    days
    return


@app.cell
def _(Value):


    @dataclass(frozen=True)
    class Literal:
        """A literal value baked directly into the AST at parse time (a
        number, string, date, duration, ...). See parse_primary().
        """

        value: Value
        position: int


    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
