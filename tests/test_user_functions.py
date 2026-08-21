"""Tests for named statically typed Calc functions."""

from decimal import Decimal

import pytest

from src.engine import (
    Blank,
    ExpressionError,
    Quantity,
    Type,
    Unit,
    evaluate_script,
    parse_script,
)


def test_named_function_parses_and_runs():
    result = evaluate_script(
        "fn add(x: int, y: int) -> int = x + y; add(2, 5)"
    )

    assert result.value == 7
    assert result.category == "int"


def test_named_function_can_use_existing_builtin_functions():
    result = evaluate_script(
        'fn label(first: text, last: text) -> text = '
        'concat(first, " ", last); '
        'label("Garry", "Mounac")'
    )

    assert result.value == "Garry Mounac"
    assert result.category == "text"


def test_named_functions_can_call_earlier_user_functions():
    result = evaluate_script(
        "fn double(x: int) -> int = x * 2; "
        "fn quadruple(x: int) -> int = double(double(x)); "
        "quadruple(3)"
    )

    assert result.value == 12
    assert result.category == "int"


def test_named_function_supports_calc_quantity_types():
    result = evaluate_script(
        "fn invoice(price: currency, qty: tonnage) -> currency = price * qty; "
        "invoice($450, 2.4t)"
    )

    assert result.value == Quantity(Decimal("1080.00"), Unit.CURRENCY)
    assert result.category == "currency"


def test_function_declaration_itself_returns_blank():
    result = evaluate_script("fn add(x: int, y: int) -> int = x + y;")

    assert isinstance(result.value, Blank)
    assert result.category == "blank"


def test_function_return_type_is_checked_at_declaration():
    with pytest.raises(ExpressionError, match="declares return type text, but its body returns int"):
        evaluate_script("fn broken(x: int) -> text = x + 1; broken(2)")


def test_function_argument_types_are_checked_at_call_site():
    with pytest.raises(ExpressionError, match="argument 1 \(x\) must be int, got decimal"):
        evaluate_script("fn double(x: int) -> int = x * 2; double(2.5)")


def test_function_arity_uses_existing_function_spec_validation():
    with pytest.raises(ExpressionError, match="exactly 2 arguments"):
        evaluate_script("fn add(x: int, y: int) -> int = x + y; add(2)")


def test_unknown_annotation_is_rejected():
    with pytest.raises(ExpressionError, match="Unknown or unsupported parameter 'x' type 'mystery'"):
        evaluate_script("fn f(x: mystery) -> int = 1; f(2)")


def test_duplicate_parameter_names_are_rejected():
    with pytest.raises(ExpressionError, match="Duplicate parameter name 'x'"):
        parse_script("fn f(x: int, x: int) -> int = x;")


def test_existing_builtin_function_name_cannot_be_redefined():
    with pytest.raises(ExpressionError, match="Function 'sum' already exists"):
        evaluate_script("fn sum(x: int) -> int = x; sum(2)")


def test_recursive_function_is_rejected_for_now():
    with pytest.raises(ExpressionError, match="Recursive functions are not supported yet"):
        evaluate_script(
            "fn loop(x: int) -> int = loop(x); loop(1)"
        )


def test_function_cannot_capture_outer_script_variable_yet():
    with pytest.raises(ExpressionError, match="cannot capture outer variables yet"):
        evaluate_script(
            "let tax = 2; fn add_tax(x: int) -> int = x + tax; add_tax(5)"
        )


def test_parameter_names_are_local_and_do_not_change_outer_bindings():
    result = evaluate_script(
        "let x = 100; fn double(x: int) -> int = x * 2; double(4); x"
    )

    assert result.value == 100
    assert result.category == "int"


def test_fn_stays_available_as_an_ordinary_identifier_outside_declaration_shape():
    result = evaluate_script("let fn = 5; fn * 2")

    assert result.value == 10
    assert result.category == "int"


def test_annotation_is_a_real_calc_type_name():
    parsed = parse_script("fn double(x: int) -> int = x * 2;")
    declaration = parsed[0]

    assert declaration.parameters[0].annotation == Type("int")
    assert declaration.return_type == Type("int")
