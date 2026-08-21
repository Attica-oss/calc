"""Runtime semantics for Calc type descriptors.

The checker has always known ``type_of()`` returns ``type``. This module
makes the runtime agree with that promise: ``type_of()`` now returns a
``Type`` value instead of rendering the type to ordinary text, and type
values support equality/inequality through the normal operator registry.

Column headers are metadata, not part of a column's public type identity.
A named ``column("qty", 1, 2)`` therefore reports ``column{int}``, while a
table still keeps field names in its type because those names are its schema.
"""

from collections.abc import Sequence
from dataclasses import replace

from .functions import FUNCTIONS
from .operators import register_binary
from .values import CATEGORY_LABELS, Column, Type, Value, category_of


def _public_type(value: Value) -> Type:
    """Return the first-class type descriptor exposed by ``type_of()``."""

    if isinstance(value, Column):
        return Type("column", fields=((None, value.element_type),))

    return category_of(value)


def _type_of_impl(values: Sequence[Value]) -> Type:
    [value] = values
    return _public_type(value)


def _type_equal(left: Value, right: Value) -> bool:
    assert isinstance(left, Type)
    assert isinstance(right, Type)
    return left == right


def _type_not_equal(left: Value, right: Value) -> bool:
    return not _type_equal(left, right)


def install_type_value_semantics() -> None:
    """Install runtime ``type`` behavior into the shared registries."""

    FUNCTIONS["type_of"] = replace(FUNCTIONS["type_of"], impl=_type_of_impl)

    register_binary("=", "type", "type", "boolean", _type_equal)
    register_binary("<>", "type", "type", "boolean", _type_not_equal)

    CATEGORY_LABELS["type"] = "a type"
