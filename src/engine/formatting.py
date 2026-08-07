"""Rendering evaluated values back to display text."""

from decimal import Decimal

from .values import (
    Blank,
    Complex,
    Duration,
    Quantity,
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


def format_result(value) -> str:
    # bool is a subclass of int: check it first.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"  # Check if we can change to lowercase true and false

    if isinstance(value, Blank):
        return "blank"  # Check if we can change to 'null'

    if isinstance(value, Complex):
        return format_complex(value)

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
