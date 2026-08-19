"""Pytest suite for the calc expression engine.

Run from the project root with:

    pytest -q
    pytest -q tests/test_engine.py
    pytest -q tests/test_engine.py::test_currency_addition_is_exact

The suite is independent of marimo and is suitable for CI and pre-commit hooks.
"""

from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from src.engine import (
    Array,
    Blank,
    Char,
    Column,
    Complex,
    Duration,
    ExpressionError,
    Matrix,
    Quantity,
    Table,
    Type,
    Unit,
    category_of,
    evaluate_expression,
    format_result,
    parse,
    variables_in,
)


def assert_eval(
    expression: str,
    expected_value: Any,
    expected_category: str,
    variables: Mapping[str, Any] | None = None,
):
    """Evaluate an expression and assert both its category and value."""
    result = evaluate_expression(expression, {} if variables is None else variables)
    assert result.category == expected_category, (
        f"{expression!r}: expected category {expected_category!r}, "
        f"got {result.category!r} (value={result.value!r})"
    )
    assert result.value == expected_value, (
        f"{expression!r}: expected value {expected_value!r}, got {result.value!r}"
    )
    return result


def assert_expression_error(
    expression: str,
    fragment: str,
    variables: Mapping[str, Any] | None = None,
) -> ExpressionError:
    """Assert that evaluation raises ExpressionError containing fragment."""
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression(expression, {} if variables is None else variables)

    message = str(caught.value)
    assert fragment in message, (
        f"{expression!r}: expected an error containing {fragment!r}, got {message!r}"
    )
    return caught.value


# Months
#
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2026-01-31 + 1mo", date(2026, 2, 28)),
        ("2026-02-28 + 1mo", date(2026, 3, 31)),
        ("2026-01-31 + 2mo", date(2026, 3, 31)),
        ("2026-03-31 + 1mo", date(2026, 4, 30)),
        ("2026-04-30 + 1mo", date(2026, 5, 31)),
        ("2026-01-15 + 1mo", date(2026, 2, 15)),
    ],
)
def test_calendar_month_addition(expression, expected):
    assert_eval(expression, expected, "date")


# Numbers


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("1 + 2", 3, "int"),
        ("7 // 2", 3, "int"),
        ("1 / 2", Decimal("0.5"), "decimal"),
        ("2 ^ 10", Decimal(1024), "decimal"),
        ("7 % 3", 1, "int"),
        ("min(3, 1.5)", Decimal("1.5"), "decimal"),
        ("max(3, 1.5)", Decimal(3), "decimal"),
        ("2e3", Decimal("2E+3"), "decimal"),
        ("-5", -5, "int"),
        ("+5", 5, "int"),
        ("2 + 3 * 4", 14, "int"),
        ("(2 + 3) * 4", 20, "int"),
        ("2 ^ 3 ^ 2", Decimal(512), "decimal"),
    ],
)
def test_number_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize("expression", ["1 / 0", "1 // 0", "1 % 0"])
def test_division_by_zero_is_a_runtime_error(expression):
    assert_expression_error(expression, "divide by zero")


def test_exponent_too_large_is_rejected():
    assert_expression_error("2 ^ 1000", "too large")


def test_chained_comparisons_are_rejected():
    assert_expression_error("1 < 2 < 3", "Chained comparisons")


# Currency and tonnage


def test_currency_addition_is_exact():
    assert_eval(
        "$0.10 + $0.20",
        Quantity(Decimal("0.30"), Unit.CURRENCY),
        "currency",
    )


def test_currency_division_by_number_keeps_currency():
    assert_eval("$10 / 4", Quantity(Decimal("2.50"), Unit.CURRENCY), "currency")


def test_currency_division_by_currency_gives_ratio():
    assert_eval("$10 / $4", Decimal("2.5"), "decimal")


def test_currency_unary_minus():
    assert_eval("-$5", Quantity(Decimal(-5), Unit.CURRENCY), "currency")


def test_currency_and_plain_number_do_not_add():
    assert_expression_error("$5 + 3", "not defined")


def test_currency_and_tonnage_do_not_add():
    assert_expression_error(
        "price + shipment",
        "not defined",
        {
            "price": Quantity(Decimal("12.50"), Unit.CURRENCY),
            "shipment": Quantity(Decimal("2.4"), Unit.TONNAGE),
        },
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("$5", "$5.00"), ("-$5", "-$5.00")],
)
def test_currency_formatting(expression, expected):
    assert format_result(evaluate_expression(expression).value) == expected


def test_tonnage_basic_arithmetic():
    assert_eval("3 * 1.5t", Quantity(Decimal("4.5"), Unit.TONNAGE), "tonnage")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("2.4t", "2.400 t"), ("2t", "2.000 t")],
)
def test_tonnage_formatting(expression, expected):
    assert format_result(evaluate_expression(expression).value) == expected


@pytest.mark.parametrize("expression", ["$450 * 2.4t", "2.4t * $450"])
def test_currency_times_tonnage_acts_as_per_tonne_rate(expression):
    assert_eval(expression, Quantity(Decimal(1080), Unit.CURRENCY), "currency")


# Percent


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("1.5%", Quantity(Decimal("0.015"), Unit.PERCENT), "percent"),
        ("$5.2 * 1.5%", Quantity(Decimal("0.08"), Unit.CURRENCY), "currency"),
        ("200 * 10%", Decimal(20), "decimal"),
        ("5% + 2.5%", Quantity(Decimal("0.075"), Unit.PERCENT), "percent"),
        ("50% * 10%", Quantity(Decimal("0.05"), Unit.PERCENT), "percent"),
        ("10% / 5%", Decimal(2), "decimal"),
        ("7 % 3", 1, "int"),
    ],
)
def test_percent_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


def test_percent_formatting():
    assert format_result(evaluate_expression("5% + 2.5%").value) == "7.5%"


def test_grow_by_is_deliberately_ambiguous_and_rejected():
    assert_expression_error("$100 + 5%", "not defined")


# Dates, times, datetimes, and durations


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("2026-05-01 - 2026-04-28", Duration(days=4), "duration"),
        ("2026-01-31 + 1mo", date(2026, 2, 28), "date"),
        ("10:30 + 45min", time(11, 15), "time"),
        ("2026-01-05 14:30:00", datetime(2026, 1, 5, 14, 30), "datetime"),
    ],
)
def test_temporal_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


def test_date_cannot_take_hours():
    assert_expression_error("2026-01-01 + 2h", "Use a datetime instead")


def test_datetime_display_round_trips_through_parser():
    original = evaluate_expression("2026-01-05 14:30:45").value
    displayed = format_result(original)
    assert evaluate_expression(displayed).value == original


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("3h + 20min", Duration(seconds=12_000)),
        ("2d * 3", Duration(days=6)),
    ],
)
def test_duration_arithmetic(expression, expected):
    assert_eval(expression, expected, "duration")


def test_durations_have_no_total_order():
    assert_expression_error("3h < 1mo", "not defined")


def test_duration_multiplied_by_decimal_is_rejected():
    assert_expression_error("3h * 1.5", "not defined")


# Complex numbers


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("4i", Complex(Decimal(0), Decimal(4)), "complex"),
        ("3 + 4i", Complex(Decimal(3), Decimal(4)), "complex"),
        ("2i * 2i", Complex(Decimal(-4), Decimal(0)), "complex"),
        ("(3+4i) / (1+2i)", Complex(Decimal("2.2"), Decimal("-0.4")), "complex"),
        ("re(3+4i)", Decimal(3), "decimal"),
        ("im(3+4i)", Decimal(4), "decimal"),
        ("conj(3+4i)", Complex(Decimal(3), Decimal(-4)), "complex"),
        ("abs(3+4i)", Decimal(5), "decimal"),
    ],
)
def test_complex_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


def test_bare_i_is_an_ordinary_variable_name():
    assert_eval("i + t", 7, "int", {"i": 3, "t": 4})


def test_complex_numbers_have_no_total_order():
    assert_expression_error("(1+2i) < (3+4i)", "not defined")


def test_complex_functions_reject_non_complex_arguments():
    assert_expression_error("re(5)", "requires a complex number")


# Constants


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("pi()", Decimal("3.14159265358979323846264338327950288419716939937511")),
        ("e()", Decimal("2.71828182845904523536028747135266249775724709369996")),
    ],
)
def test_constants(expression, expected):
    assert_eval(expression, expected, "decimal")


def test_function_names_must_be_lowercase():
    assert_expression_error("PI()", "must be lowercase")
    assert_expression_error("Sum(1, 2)", "must be lowercase")


def test_constants_do_not_shadow_same_named_variables():
    assert_eval("pi", Decimal(3), "decimal", {"pi": Decimal(3)})


# Blank


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("blank() = blank()", True, "boolean"),
        ("isblank(blank())", True, "boolean"),
        ("isblank(5)", False, "boolean"),
        ("isblank($5)", False, "boolean"),
        ("coalesce(blank(), $5)", Quantity(Decimal(5), Unit.CURRENCY), "currency"),
        ("coalesce($3, $5)", Quantity(Decimal(3), Unit.CURRENCY), "currency"),
    ],
)
def test_blank_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("blank() + 5", "not defined"),
        ("blank() = 5", "not defined"),
        ("coalesce(5, $5)", "same type as the default"),
    ],
)
def test_blank_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


def test_blank_formatting():
    assert format_result(Blank()) == "blank"


# Infinity


def test_infinity_function_and_symbol_are_equivalent():
    assert evaluate_expression("infinity()").value == evaluate_expression("∞").value


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("infinity() + 5", Decimal("Infinity"), "decimal"),
        ("infinity() * -1", Decimal("-Infinity"), "decimal"),
        ("5 / infinity()", Decimal(0), "decimal"),
        ("infinity() > 999999999999999", True, "boolean"),
        ("min(infinity(), 5)", 5, "decimal"),
    ],
)
def test_infinity_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("infinity()", "∞"), ("-infinity()", "-∞")],
)
def test_infinity_formatting(expression, expected):
    assert format_result(evaluate_expression(expression).value) == expected


@pytest.mark.parametrize(
    "expression",
    ["infinity() - infinity()", "infinity() / infinity()", "0 * infinity()"],
)
def test_indeterminate_infinity_forms_raise(expression):
    assert_expression_error(expression, "indeterminate")


def test_infinite_currency_is_rejected():
    assert_expression_error("$5 * infinity()", "can't be infinite")


def test_rounding_infinity_is_rejected():
    assert_expression_error("round(infinity())", "infinite")


# Casts


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("2026-05-01::DAY", 1, "int"),
        ("2026-05-01::MONTH", 5, "int"),
        ("2026-05-01::YEAR", 2026, "int"),
        ("01:00::HOUR", 1, "int"),
        ("01:05::MINUTE", 5, "int"),
        ("$5.15::DECIMAL", Decimal("5.15"), "decimal"),
        ("2026-01-05 01:00::DATE", date(2026, 1, 5), "date"),
        ("2026-01-05 14:30:45::TIME", time(14, 30, 45), "time"),
        ("2026-01-05::DATETIME", datetime(2026, 1, 5), "datetime"),
        ("2026-01-05 14:30:00::DATE::MONTH", 1, "int"),
        ("2 ^ 3::DECIMAL", Decimal(8), "decimal"),
        ("-5::DECIMAL", Decimal(-5), "decimal"),
        ("5::CURRENCY", Quantity(Decimal(5), Unit.CURRENCY), "currency"),
        ("2.4::TONNAGE", Quantity(Decimal("2.4"), Unit.TONNAGE), "tonnage"),
        ("7.9::INT", 7, "int"),
        ("-7.9::INT", -7, "int"),
        ("5::INT", 5, "int"),
        ("2026-01-05::DATE", date(2026, 1, 5), "date"),
    ],
)
def test_casts(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


def test_percent_casts_are_true_inverses():
    five_percent = evaluate_expression("5::PERCENT").value
    assert five_percent == Quantity(Decimal("0.05"), Unit.PERCENT)
    assert evaluate_expression("x::DECIMAL", {"x": five_percent}).value == Decimal(
        "0.05"
    )


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("5::DAY", "Cannot cast a whole number to day"),
        ("2026-01-05::NONSENSE", "Cannot cast a date to nonsense"),
        ("$5::TONNAGE", "Cannot cast a currency amount to tonnage"),
    ],
)
def test_invalid_casts(expression, fragment):
    assert_expression_error(expression, fragment)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("2026-05-01::day", "must be uppercase"),
        ("2026-05-01::Day", "must be uppercase"),
        ("5::decimal", "must be uppercase"),
    ],
)
def test_cast_targets_must_be_uppercase(expression, fragment):
    assert_expression_error(expression, fragment)


def test_table_field_access_is_exempt_from_cast_casing():
    # t::colname isn't a fixed-vocabulary cast keyword — it's the
    # table's own (user-chosen, usually lowercase) column name — so
    # unlike ::DECIMAL/::DATE/..., it stays case-insensitive.
    variables = {"t": evaluate_expression('table(column("qty", 1, 2))').value}
    assert evaluate_expression("t::qty", variables).value.values == (1, 2)
    assert evaluate_expression("t::QTY", variables).value.values == (1, 2)


# Other functions


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("ceil(7, 5)", 10, "int"),
        ("ceil(10, 5)", 10, "int"),
        ("ceil(-7, 5)", -5, "int"),
        ("ceil(3h + 20min, 1h)", Duration(seconds=14_400), "duration"),
        ("ceil(50min, 15min)", Duration(seconds=3_600), "duration"),
        ("ceil($12.30, $0.50)", Quantity(Decimal("12.50"), Unit.CURRENCY), "currency"),
        ("round(3.5)", 4, "int"),
        ("round(3.456, 2)", Decimal("3.46"), "decimal"),
        ("abs(-5)", 5, "int"),
        ("abs(-3h)", Duration(seconds=10_800), "duration"),
        ("sum(1, 2, 3)", 6, "int"),
        ("avg(1, 2)", Decimal("1.5"), "decimal"),
        (
            "sum($1.10, $2.20, $3.30)",
            Quantity(Decimal("6.60"), Unit.CURRENCY),
            "currency",
        ),
        ("if(1 = 1, 2, 1 // 0)", 2, "int"),
        ("days_between(2026-01-01, 2026-02-01)", 31, "int"),
    ],
)
def test_functions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("ceil(3h, 1mo)", "no fixed length"),
        ("ceil(5, 0)", "must be positive"),
        ("ceil($5, 2)", "same kind of value"),
        ("if(1, 2, 3)", "boolean condition"),
        ("if(1 = 1, 2, $2)", "same type"),
    ],
)
def test_function_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


# Text


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ('"hello"', "hello", "text"),
        ('""', "", "text"),
        ('"a\\"b"', 'a"b', "text"),
        ('"a\\\\b"', "a\\b", "text"),
        ('"line1\\nline2"', "line1\nline2", "text"),
        # ('"foo" + "bar"', "foobar", "text"),
        ('"a" = "a"', True, "boolean"),
        ('"a" <> "b"', True, "boolean"),
        ('"a" < "b"', True, "boolean"),
        ('"b" > "a"', True, "boolean"),
        ('"a" <= "a"', True, "boolean"),
        ('"a" >= "a"', True, "boolean"),
    ],
)
def test_text_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ('"a" - "b"', "not defined"),
        ('-"a"', "cannot be applied"),
        ('"unterminated', "Unexpected character"),
    ],
)
def test_text_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


@pytest.mark.parametrize(
    ("expression", "expected_value"),
    [
        ("5::TEXT", "5"),
        ("$5.20::TEXT", "$5.20"),
        ("2.4t::TEXT", "2.400 t"),
        ("2026-05-01::TEXT", "2026-05-01"),
        ("(1 = 1)::TEXT", "TRUE"),
        ('"already text"::TEXT', "already text"),
    ],
)
def test_casts_to_text(expression, expected_value):
    assert_eval(expression, expected_value, "text")


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ('"5"::INT', 5, "int"),
        ('"5.9"::INT', 5, "int"),
        ('"5.5"::DECIMAL', Decimal("5.5"), "decimal"),
        ('"12.50"::CURRENCY', Quantity(Decimal("12.50"), Unit.CURRENCY), "currency"),
        ('"2.4"::TONNAGE', Quantity(Decimal("2.4"), Unit.TONNAGE), "tonnage"),
        ('"5"::PERCENT', Quantity(Decimal("0.05"), Unit.PERCENT), "percent"),
        ('"true"::BOOLEAN', True, "boolean"),
        ('"FALSE"::BOOLEAN', False, "boolean"),
        ('"2026-01-05"::DATE', date(2026, 1, 5), "date"),
        ('"2026-01-05 14:30:00"::DATETIME', datetime(2026, 1, 5, 14, 30), "datetime"),
        ('"14:30:00"::TIME', time(14, 30), "time"),
    ],
)
def test_casts_from_text(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ('"abc"::INT', "not a valid decimal number"),
        ('"abc"::DECIMAL', "not a valid decimal number"),
        ('"maybe"::BOOLEAN', "not a valid boolean"),
        ('"not-a-date"::DATE', "not a valid date"),
    ],
)
def test_casts_from_text_errors(expression, fragment):
    assert_expression_error(expression, fragment)


def test_percent_text_cast_matches_numeric_percent_cast():
    # "5"::PERCENT should mean the same thing as 5::PERCENT ("5
    # percent"), not the raw ratio 5.0 — both round-trip through the
    # same registered decimal->percent implementation.
    assert (
        evaluate_expression('"5"::PERCENT').value
        == evaluate_expression("5::PERCENT").value
    )


# Table and Column


def test_table_and_column_construction():
    result = evaluate_expression(
        'table(column("vessel", "Njord", "Selkie"), column("qty", 3.4t, 2.1t))'
    )
    assert result.category == Type(
        "table",
        fields=(("vessel", Type("text")), ("qty", Type("tonnage"))),
    )
    table = result.value
    assert isinstance(table, Table)
    assert table.row_count == 2
    assert table.columns == (
        ("Njord", "Selkie"),
        (
            Quantity(Decimal("3.4"), Unit.TONNAGE),
            Quantity(Decimal("2.1"), Unit.TONNAGE),
        ),
    )


def test_column_construction():
    result = evaluate_expression('column("qty", 1, 2, 3)')
    assert result.category == Type("column", fields=(("qty", Type("int")),))
    assert result.value == Column(
        name="qty", values=(1, 2, 3), element_type=Type("int")
    )


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("column(name, 1, 2)", "Unknown variable"),
        ('column("x", 1, "y")', "same type"),
        ('column("x")', "requires at least 2 arguments"),
        ('table(column("a", 1), 5)', "must all be column()"),
        ('table(column("a", 1), column("A", 2))', "duplicate column name"),
    ],
)
def test_table_and_column_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


def test_table_mismatched_column_lengths_is_a_runtime_error():
    assert_expression_error(
        'table(column("a", 1, 2), column("b", 1))',
        "must all have the same number of rows",
    )


def test_table_field_access_and_rowcount():
    variables = {
        "t": evaluate_expression(
            'table(column("vessel", "Njord", "Selkie"), column("qty", 3.4t, 2.1t))'
        ).value
    }

    column_result = evaluate_expression("t::qty", variables)
    assert column_result.category == Type("column", fields=(("qty", Type("tonnage")),))
    assert column_result.value.values == (
        Quantity(Decimal("3.4"), Unit.TONNAGE),
        Quantity(Decimal("2.1"), Unit.TONNAGE),
    )

    # Field access is case-insensitive, same as cast targets generally.
    assert evaluate_expression("t::VESSEL", variables).value.name == "vessel"

    assert evaluate_expression("rowcount(t)", variables).value == 2

    assert_expression_error("t::bogus", "Cannot cast", variables)


def test_aggregate_functions_over_a_column():
    variables = {
        "t": evaluate_expression('table(column("qty", 3.4t, 2.1t, 4.4t))').value
    }

    assert_eval(
        "sum(t::qty)", Quantity(Decimal("9.9"), Unit.TONNAGE), "tonnage", variables
    )
    assert_eval(
        "avg(t::qty)", Quantity(Decimal("3.3"), Unit.TONNAGE), "tonnage", variables
    )
    assert_eval(
        "min(t::qty)", Quantity(Decimal("2.1"), Unit.TONNAGE), "tonnage", variables
    )
    assert_eval(
        "max(t::qty)", Quantity(Decimal("4.4"), Unit.TONNAGE), "tonnage", variables
    )


def test_aggregate_functions_over_an_int_column_stay_int():
    variables = {"t": evaluate_expression('table(column("n", 1, 2, 3))').value}
    assert_eval("sum(t::n)", 6, "int", variables)
    assert_eval("min(t::n)", 1, "int", variables)
    assert_eval("max(t::n)", 3, "int", variables)
    assert_eval("avg(t::n)", Decimal(2), "decimal", variables)


def test_rowcount_requires_a_table():
    assert_expression_error("rowcount(5)", "rowcount() requires a table")


# Table verbs: filter / select / extend / sort / groupby


def _catch_report():
    return evaluate_expression(
        'table(column("vessel", "Njord", "Njord", "Selkie"), '
        'column("qty", 3.4t, 1.2t, 2.1t), '
        'column("price", $450, $380, $455))'
    ).value


def test_row_ref_outside_any_verb_is_an_error():
    assert_expression_error(
        "[qty] > 1", "can only be used inside filter()/extend()/sort()"
    )


def test_row_ref_unknown_column_is_an_error():
    assert_expression_error(
        "filter(t, [bogus] > 1t)", "Unknown column 'bogus'", {"t": _catch_report()}
    )


def test_row_ref_never_collides_with_a_same_named_outer_variable():
    # ROADMAP.md: "lexical [column] row scope — no DAX-style implicit
    # context, ever." [qty] must resolve to the row's value; the bare
    # `qty` in the same expression must still resolve to the outer
    # variable, not the row.
    t = _catch_report()
    outer_qty = Quantity(Decimal("2.5"), Unit.TONNAGE)
    result = evaluate_expression(
        "filter(t, [qty] > qty)", {"t": t, "qty": outer_qty}
    ).value
    assert result.columns[0] == ("Njord",)  # only the 3.4t row beats 2.5t


def test_filter():
    t = _catch_report()
    result = evaluate_expression("filter(t, [qty] > 2t)", {"t": t}).value
    assert result.columns[0] == ("Njord", "Selkie")
    assert result.schema == t.schema


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("filter(t, [qty])", "must be a boolean"),
        ("filter(5, [qty] > 1t)", "first argument must be a table"),
    ],
)
def test_filter_type_errors(expression, fragment):
    assert_expression_error(expression, fragment, {"t": _catch_report()})


def test_select_reorders_and_subsets():
    t = _catch_report()
    result = evaluate_expression('select(t, "price", "vessel")', {"t": t}).value
    assert [name for name, _ in result.schema] == ["price", "vessel"]
    assert result.columns[1] == t.columns[0]


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ('select(t, "nope")', "is not a column"),
        ('select(t, "qty", "qty")', "duplicate column name"),
    ],
)
def test_select_type_errors(expression, fragment):
    assert_expression_error(expression, fragment, {"t": _catch_report()})


def test_extend_adds_a_computed_column():
    t = _catch_report()
    result = evaluate_expression('extend(t, "value", [qty] * [price])', {"t": t}).value
    assert [name for name, _ in result.schema][-1] == "value"
    assert result.columns[-1] == (
        Quantity(Decimal("1530.00"), Unit.CURRENCY),
        Quantity(Decimal("456.00"), Unit.CURRENCY),
        Quantity(Decimal("955.50"), Unit.CURRENCY),
    )


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ('extend(t, "qty", [qty])', "already exists"),
        ("extend(t, name, [qty])", "literal text column name"),
    ],
)
def test_extend_type_errors(expression, fragment):
    assert_expression_error(expression, fragment, {"t": _catch_report(), "name": "x"})


def test_extend_on_an_empty_table_is_a_runtime_error():
    empty = evaluate_expression("filter(t, [qty] > 100t)", {"t": _catch_report()}).value
    assert empty.row_count == 0
    assert_expression_error(
        'extend(t, "value", [qty] * 2)',
        "cannot determine a new column's type on an empty table",
        {"t": empty},
    )


def test_sort_ascending_and_descending():
    t = _catch_report()
    ascending = evaluate_expression("sort(t, [qty])", {"t": t}).value
    assert ascending.columns[0] == ("Njord", "Selkie", "Njord")

    descending = evaluate_expression('sort(t, [qty], "DESC")', {"t": t}).value
    assert descending.columns[0] == ("Njord", "Selkie", "Njord")
    assert descending.columns[1][0] == Quantity(Decimal("3.4"), Unit.TONNAGE)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ('sort(t, [qty], "sideways")', 'must be "asc" or "desc"'),
    ],
)
def test_sort_type_errors(expression, fragment):
    assert_expression_error(expression, fragment, {"t": _catch_report()})


def test_groupby_sum_avg_min_max_count():
    t = _catch_report()
    variables = {"t": t}

    summed = evaluate_expression('groupby(t, "vessel", "qty", "sum")', variables).value
    assert dict(zip(summed.columns[0], summed.columns[1])) == {
        "Njord": Quantity(Decimal("4.600"), Unit.TONNAGE),
        "Selkie": Quantity(Decimal("2.100"), Unit.TONNAGE),
    }

    counted = evaluate_expression(
        'groupby(t, "vessel", "qty", "count")', variables
    ).value
    assert dict(zip(counted.columns[0], counted.columns[1])) == {
        "Njord": 2,
        "Selkie": 1,
    }


def test_groupby_reuses_the_real_column_aggregation_path():
    # groupby()'s aggregate is FUNCTIONS[agg_fn].impl on a real Column
    # built via column() — the same path sum(t::qty) already exercises
    # — so an int column's sum stays "int", matching sum(t::col).
    t = evaluate_expression(
        'table(column("g", "a", "a", "b"), column("n", 1, 2, 3))'
    ).value
    result = evaluate_expression('groupby(t, "g", "n", "sum")', {"t": t})
    assert result.category == Type(
        "table", fields=(("g", Type("text")), ("sum_n", Type("int")))
    )


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ('groupby(t, "vessel", "vessel", "sum")', "requires a compatible type"),
        ('groupby(t, "vessel", "qty", "median")', "must be one of"),
        ('groupby(t, "nope", "qty", "sum")', "is not a column"),
    ],
)
def test_groupby_type_errors(expression, fragment):
    assert_expression_error(expression, fragment, {"t": _catch_report()})


def test_min_max_now_work_on_text():
    # Regression check for the _ORDERABLE fix made alongside sort():
    # text has had full </<=/>/>= support since the text type landed,
    # but min()/max() didn't know it until this fix.
    assert_eval('min("b", "a", "c")', "a", "text")
    assert_eval('max("b", "a", "c")', "c", "text")


def test_verbs_compose():
    # Njord/1.2t is filtered out; the remaining two rows are extended,
    # sorted by the new column descending, then reduced to two columns
    # — filter -> extend -> sort -> select chained through one expression.
    t = _catch_report()
    result = evaluate_expression(
        "select("
        "  sort("
        '    extend(filter(t, [qty] > 1.5t), "value", [qty] * [price]),'
        '    [value], "desc"'
        "  ),"
        '  "vessel", "value"'
        ")",
        {"t": t},
    ).value
    assert [name for name, _ in result.schema] == ["vessel", "value"]
    assert result.columns[0] == ("Njord", "Selkie")
    assert result.columns[1] == (
        Quantity(Decimal("1530.00"), Unit.CURRENCY),
        Quantity(Decimal("955.50"), Unit.CURRENCY),
    )


# Char


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("0x2B", Char(0x2B), "char"),
        ("0X2b", Char(0x2B), "char"),
        ("0x2B < 0x2C", True, "boolean"),
        ("0x2B = 0x2B", True, "boolean"),
        ("0x2B::TEXT", "+", "text"),
        ("0x2B::INT", 43, "int"),
        ("43::CHAR", Char(0x2B), "char"),
        ('"+"::CHAR', Char(0x2B), "char"),
    ],
)
def test_char_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("0x110000", "not a valid Unicode codepoint"),
        ("0xD800", "not a valid Unicode codepoint"),
        ('"ab"::CHAR', "not a single character"),
        ("0x2B + 0x2C", "not defined"),
        ("1114112::CHAR", "not a valid Unicode codepoint"),
    ],
)
def test_char_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


def test_char_formatting():
    assert format_result(Char(0x2B)) == "+"


# Array and Matrix


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        (
            "array(1, 2, 3)",
            Array(values=(1, 2, 3), element_type=Type("int")),
            Type("array", fields=((None, Type("int")),)),
        ),
        ("len(array(1, 2, 3))", 3, "int"),
        ("at(array(10, 20, 30), 1)", 20, "int"),
        ("sum(array(1t, 2t, 3t))", Quantity(Decimal(6), Unit.TONNAGE), "tonnage"),
        ("avg(array(1, 2, 3))", Decimal(2), "decimal"),
        ('min(array("b", "a", "c"))', "a", "text"),
        ('max(array("b", "a", "c"))', "c", "text"),
    ],
)
def test_array_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("array(1, $2)", "same type"),
        ("at(array(1, 2, 3),-1)", "out of range"),
        ("at(array(1, 2, 3), 4)", "out of range"),
        ("at(array(1, 2, 3), 4.5)", "whole number"),
        ("len(5)", "requires an array"),
    ],
)
def test_array_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


def test_matrix_construction_and_access():
    result = evaluate_expression("matrix(array(1, 2, 3), array(4, 5, 6))")
    assert result.category == Type("matrix", fields=((None, Type("int")),))
    matrix = result.value
    assert isinstance(matrix, Matrix)
    assert matrix.shape == (2, 3)
    assert matrix.rows == ((1, 2, 3), (4, 5, 6))

    assert evaluate_expression("at(matrix(array(1, 2), array(3, 4)), 1, 0)").value == 3
    assert evaluate_expression("rowcount(matrix(array(1, 2), array(3, 4)))").value == 2
    assert evaluate_expression("colcount(matrix(array(1, 2), array(3, 4)))").value == 2


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("matrix(array(1, 2), array(3, 4, 5))", "same number of elements"),
        ('matrix(array(1, 2), array("a", "b"))', "same element type"),
        ("matrix(array(1, 2), 5)", "must all be array()"),
        ("at(matrix(array(1, 2), array(3, 4)), 3, 1)", "out of range"),
    ],
)
def test_matrix_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


def test_array_and_matrix_formatting():
    assert format_result(evaluate_expression("array(1, 2, 3)").value) == "[1, 2, 3]"
    assert (
        format_result(evaluate_expression("matrix(array(1, 2), array(3, 4))").value)
        == "1 | 2\n3 | 4"
    )


def test_rowcount_and_colcount_still_work_on_a_table():
    t = evaluate_expression('table(column("a", 1, 2), column("b", 3, 4))').value
    assert evaluate_expression("rowcount(t)", {"t": t}).value == 2
    assert evaluate_expression("colcount(t)", {"t": t}).value == 2


# and / or / not


@pytest.mark.parametrize(
    ("expression", "expected_value"),
    [
        ("and(1 = 1, 2 = 2)", True),
        ("and(1 = 1, 2 = 3)", False),
        ("and(1 = 1, 1 = 1, 1 = 2)", False),
        ("or(1 = 2, 2 = 3)", False),
        ("or(1 = 2, 2 = 2)", True),
        ("not(1 = 1)", False),
        ("not(1 = 2)", True),
    ],
)
def test_and_or_not(expression, expected_value):
    assert_eval(expression, expected_value, "boolean")


def test_and_or_short_circuit_avoids_the_error():
    # and()/or() are lazy for the same reason if() is: an unreached
    # argument must never be evaluated, or and(x <> 0, 1/x > 5) would
    # divide by zero whenever x = 0 despite the whole thing being
    # trivially False.
    assert_eval("and(x <> 0, 1 / x > 5)", False, "boolean", {"x": 0})
    assert_eval("or(x = 0, 1 / x > 5)", True, "boolean", {"x": 0})


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("and(1, 2)", "must all be boolean"),
        ("or(1 = 1, 2)", "must all be boolean"),
        ("not(5)", "requires a boolean"),
    ],
)
def test_and_or_not_type_errors(expression, fragment):
    assert_expression_error(expression, fragment)


# dayname and time-intelligence date functions


@pytest.mark.parametrize(
    ("expression", "expected_value"),
    [
        ("dayname(2026-08-08)", "Sat"),
        ('dayname(2026-08-08, "%a")', "Sat"),
        ('dayname(2026-08-08, "%A")', "Saturday"),
    ],
)
def test_dayname(expression, expected_value):
    assert_eval(expression, expected_value, "text")


def test_dayname_type_errors():
    assert_expression_error('dayname(2026-08-08, "%Q")', 'must be "%a" or "%A"')
    assert_expression_error("dayname(5)", "requires a date or datetime")


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("somonth(2026-08-08)", date(2026, 8, 1), "date"),
        ("eomonth(2026-08-08)", date(2026, 8, 31), "date"),
        ("eomonth(2024-02-15)", date(2024, 2, 29), "date"),  # leap year
        ("soquarter(2026-08-08)", date(2026, 7, 1), "date"),
        ("eoquarter(2026-08-08)", date(2026, 9, 30), "date"),
        ("soyear(2026-08-08)", date(2026, 1, 1), "date"),
        ("eoyear(2026-08-08)", date(2026, 12, 31), "date"),
        (
            "somonth(2026-08-08 14:30:00)",
            datetime(2026, 8, 1),
            "datetime",
        ),
    ],
)
def test_time_intelligence_date_bounds(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


def test_time_intelligence_composes_into_a_ytd_total():
    t = evaluate_expression(
        'table(column("date", 2026-01-05, 2026-03-10, 2026-07-01, 2025-12-20), '
        'column("amount", $100, $200, $300, $400))'
    ).value
    asof = date(2026, 8, 8)

    result = evaluate_expression(
        "sum(filter(t, and([date] >= soyear(asof), [date] <= asof))::amount)",
        {"t": t, "asof": asof},
    )
    assert result.value == Quantity(Decimal(600), Unit.CURRENCY)


# Type object semantics


def test_flat_type_behaves_like_a_plain_string():
    flat = Type("int")
    assert flat == "int"
    assert "int" == flat
    assert flat in {"int", "decimal"}
    assert hash(flat) == hash("int")


def test_compound_types_compare_structurally():
    schema = (("vessel", Type("text")), ("qty", Type("tonnage")))
    a = Type("table", fields=schema)
    b = Type("table", fields=schema)
    c = Type("table", fields=(("vessel", Type("text")),))

    assert a == b
    assert a != c
    assert a != "table"
    assert a not in {"table"}


# Variables and dependency tracking


def test_variable_lookup():
    assert_eval(
        "price * qty",
        Quantity(Decimal("37.50"), Unit.CURRENCY),
        "currency",
        {"price": Quantity(Decimal("12.50"), Unit.CURRENCY), "qty": 3},
    )


def test_unknown_variable_is_a_type_error():
    assert_expression_error("missing + 1", "Unknown variable")


def test_dependency_tracking():
    result = evaluate_expression(
        "price * qty + shipping",
        {"price": Decimal(5), "qty": 2, "shipping": Decimal(1)},
    )
    assert result.variables == ("price", "qty", "shipping")


def test_dependency_tracking_walks_into_casts_and_calls():
    assert variables_in(parse("if(x::DAY > 1, sum(y, z), y)")) == ("x", "y", "z")


# category_of and formatting


@pytest.mark.parametrize(
    ("value", "expected_category"),
    [
        (True, "boolean"),
        (5, "int"),
        (Decimal("5.5"), "decimal"),
        (date(2026, 1, 1), "date"),
        (datetime(2026, 1, 1), "datetime"),
        (time(9, 30), "time"),
        (Duration(days=1), "duration"),
        (Complex(Decimal(1), Decimal(2)), "complex"),
        (Blank(), "blank"),
        (Quantity(Decimal(5), Unit.CURRENCY), "currency"),
        ("some text", "text"),
    ],
)
def test_category_of(value, expected_category):
    assert category_of(value) == expected_category


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "TRUE"),
        (False, "FALSE"),
        (Decimal("5.500"), "5.5"),
        (Decimal("5.000"), "5"),
        (1_234_567, "1,234,567"),
        ("hello", "hello"),
    ],
)
def test_general_formatting(value, expected):
    assert format_result(value) == expected


def test_column_formatting():
    column = evaluate_expression('column("qty", 1, 2, 3)').value
    assert format_result(column) == "qty: [1, 2, 3]"


def test_table_formatting():
    table = evaluate_expression(
        'table(column("vessel", "Njord", "Selkie"), column("qty", 3.4t, 2.1t))'
    ).value
    assert format_result(table) == (
        "vessel | qty    \n-------+--------\nNjord  | 3.400 t\nSelkie | 2.100 t"
    )


# Error positions


def test_static_type_error_points_at_operator():
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("$5 + 3")
    assert caught.value.position == 3


def test_runtime_error_has_a_position():
    with pytest.raises(ExpressionError) as caught:
        evaluate_expression("1 / 0")
    assert caught.value.position is not None


# Regressions


def test_regression_tonnage_formatting_does_not_crash():
    """A string was previously passed through a float format specifier."""
    assert format_result(evaluate_expression("2.4t").value) == "2.400 t"


def test_regression_ceil_is_registered():
    """ceil() was once implemented but omitted from the function registry."""
    assert_eval("ceil(7, 5)", 10, "int")
