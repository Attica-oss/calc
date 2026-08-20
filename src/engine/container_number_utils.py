"""Container number utilities."""

from __future__ import annotations

from typing import Final

_ISO6346_LETTER_VALUES: Final[dict[str, int]] = {
    "A": 10,
    "B": 12,
    "C": 13,
    "D": 14,
    "E": 15,
    "F": 16,
    "G": 17,
    "H": 18,
    "I": 19,
    "J": 20,
    "K": 21,
    "L": 23,
    "M": 24,
    "N": 25,
    "O": 26,
    "P": 27,
    "Q": 28,
    "R": 29,
    "S": 30,
    "T": 31,
    "U": 32,
    "V": 34,
    "W": 35,
    "X": 36,
    "Y": 37,
    "Z": 38,
}


def _container_check_digit(code: str) -> int:
    """Return the ISO 6346 check digit for the first 10 characters."""

    total = 0

    for position, character in enumerate(code):
        if "0" <= character <= "9":
            value = int(character)
        else:
            value = _ISO6346_LETTER_VALUES[character]

        total += value * (2**position)

    remainder = total % 11

    return 0 if remainder == 10 else remainder
