"""Regression tests for first-class Calc type values."""

from src.engine import Type, evaluate_expression, format_result


def assert_type_value(expression: str, expected: Type) -> Type:
    result = evaluate_expression(expression)

    assert result.category == "type"
    assert isinstance(result.value, Type)
    assert result.value == expected

    return result.value


def test_type_of_returns_a_type_value():
    assert_type_value("type_of(4)", Type("int"))
    assert_type_value("type_of(type_of(4))", Type("type"))


def test_column_type_ignores_the_column_header():
    expected = Type("column", fields=((None, Type("int")),))

    first = assert_type_value('type_of(column("name", 1, 2, 3, 5))', expected)
    second = assert_type_value('type_of(column("test", 5, 8, 9, 7))', expected)

    assert first == second
    assert format_result(first) == "column{int}"


def test_type_values_support_equality_and_inequality():
    same = evaluate_expression(
        'type_of(column("name", 1, 2, 3)) = '
        'type_of(column("test", 5, 8, 9))'
    )
    different = evaluate_expression(
        'type_of(column("name", 1, 2, 3)) <> '
        'type_of(column("test", 5., 8., 9.))'
    )

    assert same.category == "boolean"
    assert same.value is True
    assert different.category == "boolean"
    assert different.value is True


def test_arrays_and_matrices_keep_structural_type_descriptors():
    assert_type_value(
        "type_of(array(2, 5, 25))",
        Type("array", fields=((None, Type("int")),)),
    )
    assert_type_value(
        "type_of(matrix(array(5., 5.6, 5.2), array(-5., 5.8, 15.2)))",
        Type("matrix", fields=((None, Type("decimal")),)),
    )


def test_table_type_keeps_column_names_as_schema():
    expected = Type(
        "table",
        fields=(("name", Type("decimal")), ("age", Type("duration"))),
    )

    value = assert_type_value(
        'type_of(table(column("name", 1.21, 5.25), column("age", 5y, 15y)))',
        expected,
    )

    assert format_result(value) == "table{name: decimal, age: duration}"
