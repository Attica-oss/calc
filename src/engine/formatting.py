"""Rendering evaluated values back to display text."""

from decimal import Decimal
from typing import Literal

from .values import (
    Array,
    Blank,
    Char,
    Column,
    Complex,
    ContainerNumber,
    Duration,
    Matrix,
    Quantity,
    Table,
    Unit,
    Value,
    category_of,
    is_date_only,
    is_datetime,
    is_time_only,
)


def format_container(value: ContainerNumber) -> str:
    """Render an ISO 6346 container number in canonical form."""

    return str(value)


def format_duration(value: Duration) -> str:
    """Render a Duration as e.g. "1y 2mo 3d 4h 5min 6s", omitting any
    zero component ("0s" if every component is zero).
    """

    components: list[str] = []

    months: int = value.months
    days: int = value.days
    seconds: int = value.seconds

    years, remaining_months = divmod(abs(months), 12)

    hours, seconds_remainder = divmod(abs(seconds), 3600)
    minutes, remaining_seconds = divmod(seconds_remainder, 60)

    if months < 0:
        years = -years
        remaining_months = -remaining_months

    if seconds < 0:
        hours = -hours
        minutes = -minutes
        remaining_seconds = -remaining_seconds

    if years:
        components.append(f"{years}y")

    if remaining_months:
        components.append(f"{remaining_months}mo")

    if days:
        components.append(f"{days}d")

    if hours:
        components.append(f"{hours}h")

    if minutes:
        components.append(f"{minutes}min")

    if remaining_seconds:
        components.append(f"{remaining_seconds}s")

    return " ".join(components) or "0s"


def format_decimal(value: Decimal) -> str:
    """Render a Decimal with thousands separators and no trailing
    zeros/decimal point (5.50 -> "5.5", 5.00 -> "5"), or ±∞.
    """

    if value.is_infinite():
        return "-∞" if value < 0 else "∞"
    text = f"{value:,f}"

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text or "0"


def format_complex(value: Complex) -> str:
    # Category stays "complex" regardless of the values (13+0i keeps
    # its type even though its imaginary part vanished), but display
    # drops a zero part for readability — matching how -$5 displays
    # as "-$5.00" rather than "$-5.00": the type system and the
    # formatter are allowed to disagree about what's worth showing.
    if value.imag == 0:
        return format_decimal(value.real)

    imaginary_text: str = format_decimal(value=abs(value.imag))
    sign: Literal["-", "+"] = "-" if value.imag < 0 else "+"

    if value.real == 0:
        prefix: Literal["-", ""] = "-" if value.imag < 0 else ""
        return f"{prefix}{imaginary_text}i"

    return f"{format_decimal(value=value.real)}{sign}{imaginary_text}i"


def format_table(value: Table) -> str:
    """Render a Table as a column-aligned text grid with a header rule."""

    headers: list[str] = [name for name, _ in value.schema]

    if not headers:
        return "(empty table)"

    rows: list[list[str]] = [
        [
            format_result(value=value.columns[column][row])
            for column in range(len(headers))
        ]
        for row in range(value.row_count)
    ]
    widths: list[int] = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        if rows
        else len(headers[i])
        for i in range(len(headers))
    ]

    lines: list[str] = [
        " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers))),
        "-+-".join("-" * widths[i] for i in range(len(headers))),
    ]
    lines.extend(
        " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in rows
    )

    return "\n".join(lines)


def format_column(value: Column) -> str:
    """Render a Column as "name: [v1, v2, ...]"."""

    return f"{value.name}: [{', '.join(format_result(value=v) for v in value.values)}]"


def format_array(value: Array) -> str:
    """Render an Array as "[v1, v2, ...]"."""

    return f"[{', '.join(format_result(value=v) for v in value.values)}]"


def format_matrix(value: Matrix) -> str:
    """Render a Matrix as a bracketed text grid."""

    if not value.rows:
        return f"[]\n{category_of(value)}"

    rows = [[format_result(value=cell) for cell in row] for row in value.rows]

    column_count = len(rows[0])

    widths = [max(len(row[col]) for row in rows) for col in range(column_count)]

    lines = []

    for row in rows:
        line = "  ".join(cell.rjust(widths[col]) for col, cell in enumerate(row))
        lines.append(line)

    if len(lines) == 1:
        body = f"[{lines[0]}]"
    else:
        body = f"[{lines[0]}\n" + "\n".join(lines[1:-1]) + f"\n{lines[-1]}]"

    return f"{body}\n{category_of(value)}"


# def format_matrix(value: Matrix) -> str:
#     """Render a Matrix as a right-aligned text grid, every cell padded
#     to the widest cell in the whole matrix (not per-column, unlike
#     format_table — a matrix is homogeneous, so one width fits all).
#     """

#     if not value.rows:
#         return "(empty matrix)\nmatrix{{{value.element_type}}}"

#     rows: list[list[str]] = [
#         [format_result(value=cell) for cell in row] for row in value.rows
#     ]
#     width: int = max(len(cell) for row in rows for cell in row)

#     body = "\n".join(" | ".join(cell.rjust(width) for cell in row) for row in rows)

#     return f"{body}\nmatrix{{{value.element_type}}}"


def format_result(value: Value) -> str:
    """Render any runtime value the engine can produce as display text
    — the single formatter the REPL, --bare CLI output, ::text casts,
    and every format_TYPE() helper above (for nested values) share.
    """

    # bool is a subclass of int: check it first.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, Blank):
        return "blank"  # Check if we can change to 'null'

    if isinstance(value, str):
        return value

    if isinstance(value, Char):
        return chr(value.codepoint)

    if isinstance(value, Complex):
        return format_complex(value)

    if isinstance(value, ContainerNumber):
        return format_container(value)

    if isinstance(value, Column):
        return format_column(value)

    if isinstance(value, Table):
        return format_table(value)

    if isinstance(value, Array):
        return format_array(value)

    if isinstance(value, Matrix):
        return format_matrix(value)

    if isinstance(value, Quantity):
        if value.unit is Unit.CURRENCY:
            text = f"${abs(value.value):,.2f}"
            return f"-{text}" if value.value < 0 else text

        if value.unit is Unit.PERCENT:
            return f"{format_decimal(value.value * 100)}%"

        return f"{value.value:,.3f} t"  # Tonnage

    if is_datetime(value):
        return value.isoformat(sep=" ", timespec="seconds")

    if is_date_only(value):
        return value.isoformat()

    if is_time_only(value):
        if value.second:
            return value.isoformat(timespec="seconds")

        return value.isoformat(timespec="minutes")

    if isinstance(value, Duration):
        return format_duration(value)

    if isinstance(value, Decimal):
        return format_decimal(value)

    return f"{value:,}"
