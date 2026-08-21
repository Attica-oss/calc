"""Runtime semantics for Calc type descriptors and structural casts.

The checker has always known ``type_of()`` returns ``type``. This module
makes the runtime agree with that promise: ``type_of()`` returns a ``Type``
value instead of ordinary text, type values support equality/inequality, and
column headers are removed from the *public* type descriptor.

It also extends the cast registry with structural resolution. A column carries
its element type in its compound category, so ``column{T}::ARRAY`` can derive
``array{T}`` without registering one cast for every possible ``T``.
"""

from collections.abc import Iterator, MutableMapping, Sequence
from dataclasses import replace
from typing import Any
import sys

from . import casts as casts_module
from . import evaluator as evaluator_module
from . import formatting as formatting_module
from .functions import FUNCTIONS
from .operators import register_binary
from .values import Array, CATEGORY_LABELS, Column, Type, Value, category_of


def _public_type(value: Value) -> Type:
    """Return the first-class type descriptor exposed by ``type_of()``.

    A column's name is metadata used by tables and row-scope lookup; it is not
    part of the homogeneous column's public type identity.
    """

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


def _column_to_array(value: Value) -> Array:
    assert isinstance(value, Column)
    return Array(values=value.values, element_type=value.element_type)


class _StructuralCastRules(MutableMapping[Any, Any]):
    """Mapping-compatible cast resolver with structural fallbacks.

    ``check_types`` uses ``CAST_RULES.get(...)`` while ``evaluate_node`` uses
    ``CAST_RULES[...]``. Implementing both through one mapping keeps the two
    passes on exactly the same resolution path.
    """

    def __init__(self, backing: MutableMapping[Any, Any]) -> None:
        self._backing = backing

    def _structural_rule(self, key: Any) -> Any | None:
        if not isinstance(key, tuple) or len(key) != 2:
            return None

        source, target = key

        if (
            target == "array"
            and isinstance(source, Type)
            and str.__eq__(source, "column")
            and source.fields
            and len(source.fields) == 1
        ):
            _, element_type = source.fields[0]
            return (
                Type("array", fields=((None, element_type),)),
                _column_to_array,
            )

        return None

    def __getitem__(self, key: Any) -> Any:
        try:
            return self._backing[key]
        except KeyError:
            rule = self._structural_rule(key)
            if rule is None:
                raise
            return rule

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key: Any, value: Any) -> None:
        self._backing[key] = value

    def __delitem__(self, key: Any) -> None:
        del self._backing[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._backing)

    def __len__(self) -> int:
        return len(self._backing)


def _install_type_formatter() -> None:
    """Ensure ``Type`` is formatted before the generic ``str`` branch."""

    original = formatting_module.format_result

    # Avoid wrapping twice if a notebook reloads the package.
    if getattr(original, "_calc_type_aware", False):
        return

    def format_result(value: Value) -> str:
        if isinstance(value, Type):
            return str(value)
        return original(value)

    format_result._calc_type_aware = True  # type: ignore[attr-defined]
    formatting_module.format_result = format_result

    # src.engine imported format_result before this installer ran; update that
    # public binding too so ``from src.engine import format_result`` sees the
    # same type-aware formatter.
    package = sys.modules.get(__package__)
    if package is not None:
        setattr(package, "format_result", format_result)


def _install_structural_casts() -> None:
    existing = casts_module.CAST_RULES

    if isinstance(existing, _StructuralCastRules):
        rules = existing
    else:
        rules = _StructuralCastRules(existing)
        casts_module.CAST_RULES = rules

    # evaluator.py imported CAST_RULES directly, so update its module-global
    # binding. Both checker and evaluator will now resolve through `rules`.
    evaluator_module.CAST_RULES = rules

    package = sys.modules.get(__package__)
    if package is not None:
        setattr(package, "CAST_RULES", rules)


def install_type_value_semantics() -> None:
    """Install runtime ``type`` behavior and structural cast resolution."""

    FUNCTIONS["type_of"] = replace(FUNCTIONS["type_of"], impl=_type_of_impl)

    register_binary("=", "type", "type", "boolean", _type_equal)
    register_binary("<>", "type", "type", "boolean", _type_not_equal)

    CATEGORY_LABELS["type"] = "a type"

    _install_type_formatter()
    _install_structural_casts()
