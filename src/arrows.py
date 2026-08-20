import marimo

__generated_with = "0.24.0"
app = marimo.App(width="columns")

with app.setup:
    from dataclasses import dataclass

    import pyarrow as pa


@app.cell
def _():
    days = pa.array([1, 12, 17, 23, 28], type=pa.int8())

    days


@app.cell
def _(Value):


    @dataclass(frozen=True)
    class Literal:
        """A literal value baked directly into the AST at parse time (a
        number, string, date, duration, ...). See parse_primary().
        """

        value: Value
        position: int




@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
