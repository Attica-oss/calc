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
    Blank,
    Complex,
    Duration,
    ExpressionError,
    Quantity,
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


# Numbers


@pytest.mark.parametrize(
    ("expression", "expected_value", "expected_category"),
    [
        ("1 + 2", 3, "int"),
        ("7 // 2", 3, "int"),
        ("1 / 2", Decimal("0.5"), "decimal"),
        ("2 ** 10", Decimal(1024), "decimal"),
        ("7 % 3", 1, "int"),
        ("min(3, 1.5)", Decimal("1.5"), "decimal"),
        ("max(3, 1.5)", Decimal(3), "decimal"),
        ("2e3", Decimal("2E+3"), "decimal"),
        ("-5", -5, "int"),
        ("+5", 5, "int"),
        ("2 + 3 * 4", 14, "int"),
        ("(2 + 3) * 4", 20, "int"),
        ("2 ** 3 ** 2", Decimal(512), "decimal"),
    ],
)
def test_number_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


@pytest.mark.parametrize("expression", ["1 / 0", "1 // 0", "1 % 0"])
def test_division_by_zero_is_a_runtime_error(expression):
    assert_expression_error(expression, "divide by zero")


def test_exponent_too_large_is_rejected():
    assert_expression_error("2 ** 1000", "too large")


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
        ("2026-05-01 - 2026-04-28", Duration(days=3), "duration"),
        ("2026-01-31 + 1mo", date(2026, 2, 28), "date"),
        ("10:30 + 45min", time(11, 15), "time"),
        ("2026-01-05T14:30:00", datetime(2026, 1, 5, 14, 30), "datetime"),
        ("2026-01-05 14:30:00", datetime(2026, 1, 5, 14, 30), "datetime"),
    ],
)
def test_temporal_expressions(expression, expected_value, expected_category):
    assert_eval(expression, expected_value, expected_category)


def test_date_cannot_take_hours():
    assert_expression_error("2026-01-01 + 2h", "Use a datetime instead")


def test_datetime_display_round_trips_through_parser():
    original = evaluate_expression("2026-01-05T14:30:45").value
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


def test_constant_function_names_are_case_insensitive():
    assert evaluate_expression("pi()").value == evaluate_expression("PI()").value


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
        ("01:05::MINUTES", 5, "int"),
        ("2026-05-01::DAYS", 1, "int"),
        ("2026-05-01::day", 1, "int"),
        ("2026-05-01::Day", 1, "int"),
        ("$5.15::DECIMAL", Decimal("5.15"), "decimal"),
        ("2026-01-05 01:00::DATE", date(2026, 1, 5), "date"),
        ("2026-01-05T14:30:45::TIME", time(14, 30, 45), "time"),
        ("2026-01-05::DATETIME", datetime(2026, 1, 5), "datetime"),
        ("2026-01-05T14:30:00::DATE::MONTH", 1, "int"),
        ("2 ** 3::decimal", Decimal(8), "decimal"),
        ("-5::decimal", Decimal(-5), "decimal"),
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
    ],
)
def test_general_formatting(value, expected):
    assert format_result(value) == expected


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
