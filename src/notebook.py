import marimo

__generated_with = "0.23.16"
app = marimo.App(auto_download=["html"])

with app.setup:
    from datetime import date, datetime, time
    from decimal import Decimal

    import marimo as mo

    from engine import (
        Blank,
        Complex,
        Duration,
        ExpressionError,
        Quantity,
        Unit,
        category_of,
        evaluate_expression,
        format_result,
    )


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # calc engine playground

    This notebook exercises the same expression engine the CLI uses
    (`engine_cli.py`). Nothing here is redefined — everything below
    is imported, so there's a single source of truth for the
    tokenizer, parser, type checker, and evaluator. It runs the
    engine's inline test suite on load, then gives you a live UI to
    try expressions against a handful of sample variables.
    """)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Inline tests:
    a tiny executable spec of the engine's behaviour.<br>
    They run on notebook load; the cell renders a checkmark when green.
    """)
    return


@app.cell
def _():
    def _check(expression, expected_value, expected_category, variables=None):
        result = evaluate_expression(expression, variables or {})

        assert result.category == expected_category, (
            expression,
            result.category,
            expected_category,
        )
        assert result.value == expected_value, (
            expression,
            result.value,
            expected_value,
        )

    def _expect_error(expression, fragment, variables=None):
        try:
            evaluate_expression(expression, variables or {})
        except ExpressionError as error:
            assert fragment in error.message, (expression, error.message)
        else:
            raise AssertionError(f"{expression!r} should have failed")

    # Numbers: int stays int, division is exact Decimal, never float.
    _check("1 + 2", 3, "int")
    _check("7 // 2", 3, "int")
    _check("1 / 2", Decimal("0.5"), "decimal")
    _check("2 ** 10", Decimal(1024), "decimal")
    _check("min(3, 1.5)", Decimal("1.5"), "decimal")

    # Quantities.
    _check("$5 + $2.50", Quantity(Decimal("7.50"), Unit.CURRENCY), "currency")
    _check("$10 / 4", Quantity(Decimal("2.50"), Unit.CURRENCY), "currency")
    _check("$10 / $4", Decimal("2.5"), "decimal")
    _check("3 * 1.5t", Quantity(Decimal("4.5"), Unit.TONNAGE), "tonnage")
    _check("-$5", Quantity(Decimal(-5), Unit.CURRENCY), "currency")
    _check(
        "sum($1.10, $2.20, $3.30)",
        Quantity(Decimal("6.60"), Unit.CURRENCY),
        "currency",
    )

    # Currency x tonnage: currency behaves as an implicit per-tonne rate.
    _check("$450 * 2.4t", Quantity(Decimal(1080), Unit.CURRENCY), "currency")
    _check("2.4t * $450", Quantity(Decimal(1080), Unit.CURRENCY), "currency")
    _check(
        "price * shipment",
        Quantity(Decimal(30), Unit.CURRENCY),
        "currency",
        {
            "price": Quantity(Decimal("12.50"), Unit.CURRENCY),
            "shipment": Quantity(Decimal("2.4"), Unit.TONNAGE),
        },
    )

    # Temporals: every difference is a duration now.
    _check("2026-05-01 - 2026-04-28", Duration(days=3), "duration")
    _check("2026-01-31 + 1mo", date(2026, 2, 28), "date")
    _check("10:30 + 45min", time(11, 15), "time")
    _check("days_between(2026-01-01, 2026-02-01)", 31, "int")

    # Functions and variables.
    _check("sum(1, 2, 3)", 6, "int")
    _check("avg(1, 2)", Decimal("1.5"), "decimal")
    _check(
        "price * qty",
        Quantity(Decimal("37.50"), Unit.CURRENCY),
        "currency",
        {"price": Quantity(Decimal("12.50"), Unit.CURRENCY), "qty": 3},
    )

    # if() is lazy: the untaken branch is never evaluated.
    _check("if(2 > 1, $5, $6)", Quantity(Decimal(5), Unit.CURRENCY), "currency")
    _check("if(1 = 1, 2, 1 // 0)", 2, "int")

    # ceil(): round up to the nearest multiple of the second argument
    # (Excel-style CEILING), not to a fixed number of decimal places.
    _check("ceil(7, 5)", 10, "int")
    _check("ceil(10, 5)", 10, "int")
    _check("ceil(-7, 5)", -5, "int")
    _check("ceil(3h + 20min, 1h)", Duration(seconds=14_400), "duration")
    _check("ceil(50min, 15min)", Duration(seconds=3_600), "duration")
    _check("ceil($12.30, $0.50)", Quantity(Decimal("12.50"), Unit.CURRENCY), "currency")

    # Static type errors, caught before evaluation.
    _expect_error("$5 + 3", "not defined for a currency amount")
    _expect_error("if(1, 2, 3)", "boolean condition")
    _expect_error("if(1 = 1, 2, $2)", "same type")
    _expect_error("1 < 2 < 3", "Chained comparisons")
    _expect_error("2026-01-01 + 2h", "Use a datetime instead")
    _expect_error("missing + 1", "Unknown variable")
    _expect_error("ceil(3h, 1mo)", "no fixed length")
    _expect_error("ceil(5, 0)", "must be positive")
    _expect_error("ceil($5, 2)", "same kind of value")

    # Percentages: lexed as literals, applied via *.
    _check("$5.2 * 1.5%", Quantity(Decimal("0.08"), Unit.CURRENCY), "currency")
    _check("1.5% * $5.2", Quantity(Decimal("0.08"), Unit.CURRENCY), "currency")
    _check("200 * 10%", Decimal(20), "decimal")
    _check("5% + 2.5%", Quantity(Decimal("0.075"), Unit.PERCENT), "percent")
    _check("50% * 10%", Quantity(Decimal("0.05"), Unit.PERCENT), "percent")
    _check("3% / 2", Quantity(Decimal("0.015"), Unit.PERCENT), "percent")
    _check("10% / 5%", Decimal(2), "decimal")
    _check("avg(10%, 20%)", Quantity(Decimal("0.15"), Unit.PERCENT), "percent")
    _check("-5%", Quantity(Decimal("-0.05"), Unit.PERCENT), "percent")
    _check("7 % 3", 1, "int")  # modulo unchanged when % stands alone
    assert format_result(evaluate_expression("5% + 2.5%").value) == "7.5%"
    _expect_error("$5 + 5%", "not defined")  # grow-by is intentionally out

    # Complex numbers: 'i' glued to a number is a literal; bare 'i' is
    # still an ordinary variable name, the same rule as bare 't'.
    _check("4i", Complex(Decimal(0), Decimal(4)), "complex")
    _check("3 + 4i", Complex(Decimal(3), Decimal(4)), "complex")
    _check("2i * 2i", Complex(Decimal(-4), Decimal(0)), "complex")  # i^2 = -1
    _check("(3+4i) / (1+2i)", Complex(Decimal("2.2"), Decimal("-0.4")), "complex")
    _check("re(3+4i)", Decimal(3), "decimal")
    _check("im(3+4i)", Decimal(4), "decimal")
    _check("conj(3+4i)", Complex(Decimal(3), Decimal(-4)), "complex")
    _check("abs(3+4i)", Decimal(5), "decimal")  # 3-4-5 triangle
    _check("i + t", 7, "int", {"i": 3, "t": 4})
    _expect_error("(1+2i) < (3+4i)", "not defined")  # no total order
    _expect_error("re(5)", "requires a complex number")

    # Constants: zero-arg functions, same shape as today()/now().
    _check(
        "pi()",
        Decimal("3.14159265358979323846264338327950288419716939937511"),
        "decimal",
    )
    _check(
        "e()",
        Decimal("2.71828182845904523536028747135266249775724709369996"),
        "decimal",
    )

    # Blank: the one "missing value" marker (stands in for null, a
    # blank cell, and NaN all at once). No arithmetic, no cross-type
    # comparison — isblank() and coalesce() are the sanctioned ways
    # to interact with it, which is the "type safe" part.
    _check("blank()", Blank(), "blank")
    _check("blank() = blank()", True, "boolean")
    _check("isblank(blank())", True, "boolean")
    _check("isblank(5)", False, "boolean")
    _check("coalesce(blank(), $5)", Quantity(Decimal(5), Unit.CURRENCY), "currency")
    _check("coalesce($3, $5)", Quantity(Decimal(3), Unit.CURRENCY), "currency")
    _check(
        "coalesce(price, $0)",
        Quantity(Decimal(0), Unit.CURRENCY),
        "currency",
        {"price": Blank()},
    )
    _expect_error("blank() + 5", "not defined")
    _expect_error("blank() = 5", "not defined")
    _expect_error("coalesce(5, $5)", "same type as the default")

    # Infinity: Decimal has genuine IEEE-854 infinity built in, so
    # this is just a Decimal value — every existing decimal rule
    # (comparisons, unary minus, min/max) already works on it.
    # Indeterminate forms (inf - inf, inf / inf, 0 * inf) are runtime
    # errors, not a silently propagated special value — the checker
    # already promised a definite category, and honoring that promise
    # is the point.
    _check("infinity()", Decimal("Infinity"), "decimal")
    _check("\u221e", Decimal("Infinity"), "decimal")
    _check("-\u221e", Decimal("-Infinity"), "decimal")
    _check("infinity() + 5", Decimal("Infinity"), "decimal")
    _check("5 / infinity()", Decimal(0), "decimal")
    _check("infinity() > 999999999999999", True, "boolean")
    assert format_result(evaluate_expression("infinity()").value) == "\u221e"
    _expect_error("infinity() - infinity()", "indeterminate")
    _expect_error("infinity() / infinity()", "indeterminate")
    _expect_error("round(infinity())", "infinite")
    _expect_error("$5 * infinity()", "can't be infinite")

    # Casts (value::target): field extraction and type conversion.
    # Both singular and plural read naturally on a date/time, so both
    # are registered (::day and ::days both work).
    _check("2026-05-01::DAY", 1, "int")
    _check("2026-05-01::MONTH", 5, "int")
    _check("2026-05-01::YEAR", 2026, "int")
    _check("01:00::HOUR", 1, "int")
    _check("01:05::MINUTES", 5, "int")
    _check("01:05::MINUTE", 5, "int")
    _check("$5.15::DECIMAL", Decimal("5.15"), "decimal")
    # A space is accepted as a datetime separator (not just T), so
    # this round-trips with how format_result already displays one.
    _check("2026-01-05 01:00::DATE", date(2026, 1, 5), "date")
    _check("2026-01-05T14:30:45::TIME", time(14, 30, 45), "time")
    _check("2026-01-05::DATETIME", datetime(2026, 1, 5), "datetime")
    # :: is case-insensitive, same as function names.
    _check("2026-05-01::day", 1, "int")
    # Casts chain: cast to datetime, then extract month from that.
    _check("2026-01-05T14:30:00::DATE::MONTH", 1, "int")
    # :: binds tighter than ** and unary minus.
    _check("2 ** 3::decimal", Decimal(8), "decimal")
    _check("-5::decimal", Decimal(-5), "decimal")
    # Numeric <-> quantity, both directions.
    _check("5::CURRENCY", Quantity(Decimal(5), Unit.CURRENCY), "currency")
    _check("5::PERCENT", Quantity(Decimal("0.05"), Unit.PERCENT), "percent")
    _check("5%::DECIMAL", Decimal("0.05"), "decimal")  # raw ratio, not 5
    _check("7.9::INT", 7, "int")  # truncates toward zero
    _check("-7.9::INT", -7, "int")
    _expect_error("5::DAY", "Cannot cast a whole number to day")
    _expect_error("2026-01-05::NONSENSE", "Cannot cast a date to nonsense")
    _expect_error("$5::TONNAGE", "Cannot cast a currency amount to tonnage")

    # Runtime errors still surface cleanly, with a position attached.
    _expect_error("1 / 0", "divide by zero")

    mo.md("✅ All inline tests passed.")
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Sample variable bindings
    """)
    return


@app.cell
def _():
    # Sample variable bindings — edit these to experiment. In the real
    # spreadsheet these become other cells' evaluated results.
    variables = {
        "price": Quantity(Decimal("12.50"), Unit.CURRENCY),
        "qty": 3,
        "shipment": Quantity(Decimal("2.4"), Unit.TONNAGE),
        "start": date(2026, 1, 15),
        "z": Complex(Decimal(2), Decimal(3)),
    }

    _rows = "\n".join(
        f"| `{name}` | `{format_result(value)}` | {category_of(value)} |"
        for name, value in variables.items()
    )

    mo.md(f"**Variables**\n\n| Name | Value | Type |\n| --- | --- | --- |\n{_rows}")
    return (variables,)


@app.cell
def _():
    expression_input = mo.ui.text(
        value="if(qty > 2, price * qty, price)",
        label="Expression",
        placeholder="Enter a calculation",
        full_width=True,
    )

    expression_input
    return (expression_input,)


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

        _output = mo.vstack(_parts)
    except ExpressionError as _error:
        if _error.position is not None:
            _pointer = " " * _error.position + "^"
            _output = mo.vstack(
                [
                    mo.callout(_error.message, kind="warn"),
                    mo.md(f"```text\n{expression_input.value}\n{_pointer}\n```"),
                ]
            )
        else:
            _output = mo.callout(_error.message, kind="warn")

    _output
    return


@app.cell
def _():
    mo.md("""
    **Try:**
    `price * qty + $4.99` ·
    `price * shipment` ·
    `shipment * 4` ·
    `avg($10, $12, $15.50)` ·
    `start + 6mo - 1d` ·
    `days_between(start, today())` ·
    `if(shipment > 2t, $100, $150)` ·
    `sum(2h, 45min, 30min)` ·
    `$5.2 * 1.5%` ·
    `price - price * 15%` ·
    `ceil(3h + 20min, 1h)`
    `z * conj(z)` ·
    `abs(3 + 4i)` ·
    `pi() * radius ** 2` ·
    `coalesce(blank(), $0)` ·
    `infinity() > 10 ** 100`

    **These fail the type check (on purpose):**
    `$5 + 3` ·
    `price + shipment` ·
    `if(1, 2, 3)` ·
    `2026-01-01 + 2h` ·
    `$5 + 5%`
    `blank() + 5` ·
    `z < 3+4i`
    """)
    return


if __name__ == "__main__":
    app.run()
