"""Rendering evaluated values back to display text."""

from decimal import Decimal

from .values import (
    Array,
    Blank,
    Char,
    Column,
    Complex,
    Duration,
    Matrix,
    Quantity,
    Table,
    Unit,
    is_date_only,
    is_datetime,
    is_time_only,
)


def format_duration(value: Duration) -> str:
    components = []

    months = value.months
    days = value.days
    seconds = value.seconds

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

    imaginary_text = format_decimal(abs(value.imag))
    sign = "-" if value.imag < 0 else "+"

    if value.real == 0:
        prefix = "-" if value.imag < 0 else ""
        return f"{prefix}{imaginary_text}i"

    return f"{format_decimal(value.real)}{sign}{imaginary_text}i"


def format_table(value: Table) -> str:
    headers = [name for name, _ in value.schema]

    if not headers:
        return "(empty table)"

    rows = [
        [format_result(value.columns[column][row]) for column in range(len(headers))]
        for row in range(value.row_count)
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    lines = [
        " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers))),
        "-+-".join("-" * widths[i] for i in range(len(headers))),
    ]
    lines.extend(
        " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in rows
    )

    return "\n".join(lines)


def format_column(value: Column) -> str:
    return f"{value.name}: [{', '.join(format_result(v) for v in value.values)}]"


def format_array(value: Array) -> str:
    return f"[{', '.join(format_result(v) for v in value.values)}]"


def format_matrix(value: Matrix) -> str:
    if not value.rows:
        return "(empty matrix)"

    rows = [[format_result(cell) for cell in row] for row in value.rows]
    width = max(len(cell) for row in rows for cell in row)

    return "\n".join(" | ".join(cell.rjust(width) for cell in row) for row in rows)


def format_result(value) -> str:
    # bool is a subclass of int: check it first.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"  # Check if we can change to lowercase true and false

    if isinstance(value, Blank):
        return "blank"  # Check if we can change to 'null'

    if isinstance(value, str):
        return value

    if isinstance(value, Char):
        return chr(value.codepoint)

    if isinstance(value, Complex):
        return format_complex(value)

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
