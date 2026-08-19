"""Calendar-aware arithmetic on dates, datetimes, and times."""

import calendar
from datetime import date, datetime, time, timedelta
from functools import cache

from .values import Duration, ExpressionError

# --- Public Holiday Utilities

NEW_FIXED_HOLIDAY_START: int = 2026
NEW_FIXED_HOLIDAY_MONTH: int = 2
NEW_FIXED_HOLIDAY_DAY: int = 1

@cache
def public_holidays(year: int) -> frozenset[date]:
    """Calculates the public holidays for a given year."""
    holidays: set[date] = set()

    fixed_holidays = {
        date(year, 1, 1),
        date(year, 1, 2),
        date(year, 5, 1),
        date(year, 6, 18),
        date(year, 6, 29),
        date(year, 8, 15),
        date(year, 11, 1),
        date(year, 12, 8),
        date(year, 12, 25),
    }

    if year >= NEW_FIXED_HOLIDAY_START:
        fixed_holidays.add(date(year, NEW_FIXED_HOLIDAY_MONTH, NEW_FIXED_HOLIDAY_DAY))

    holidays = set(fixed_holidays)

    # One-time holidays (only add for that year)
    one_time_holidays = {
        2025: {
            date(2025, 10, 11),
            date(2025, 10, 13),
            date(2025, 10, 27),
        },
        2026: {
            date(2026, 6, 30),
        },
    }
    holidays.update(one_time_holidays.get(year, set()))

    # Gregorian Easter calculation.
    a = year % 19
    b = year // 100
    c = year % 100
    d = (19 * a + b - b // 4 - ((b - (b + 8) // 25 + 1) // 3) + 15) % 30
    e = (32 + 2 * (b % 4) + 2 * (c // 4) - d - (c % 4)) % 7
    f = d + e - 7 * ((a + 11 * d + 22 * e) // 451) + 114
    month = f // 31
    day = f % 31 + 1

    easter = date(year, month, day)
    holidays.update(
        {
            easter,  # Easter Sunday
            easter + timedelta(days=1),  # Easter Monday
            easter - timedelta(days=1),  # Holy Saturday
            easter - timedelta(days=2),  # Good Friday
            easter + timedelta(days=60),  # Corpus Christi
        }
    )

    # Monday-after if fixed holiday is Sunday
    for holiday in fixed_holidays:
        if holiday.weekday() == 6:  # Sunday
            holidays.add(holiday + timedelta(days=1))

    return frozenset(holidays)


def is_public_holiday(value: date) -> bool:
    """Return whether `value` is a public holiday."""

    return value in public_holidays(value.year)


# --- Date Arithmetic Utilities


def add_months(value: date, months: int) -> date:
    """Add calendar months while keeping the date valid and preserving end-of-month semantics."""

    total_months = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(total_months, 12)
    month = zero_based_month + 1

    if not 1 <= year <= 9999:
        raise ExpressionError("The resulting date is outside the valid range.")

    source_last_day = calendar.monthrange(
        value.year,
        value.month,
    )[1]

    target_last_day = calendar.monthrange(
        year,
        month,
    )[1]

    # End-of-month is sticky.
    if value.day == source_last_day:
        final_day = target_last_day
    else:
        final_day = min(
            value.day,
            target_last_day,
        )

    return value.replace(
        year=year,
        month=month,
        day=final_day,
    )


def add_duration_to_date(value: date, duration: Duration) -> date:
    """Add a duration to a date.

    Strict typing decision: a duration with hours/minutes/seconds
    cannot be added to a bare date. (The previous behaviour silently
    promoted the result to a midnight datetime, which would make the
    static result type depend on a runtime value.)
    """

    if duration.seconds:
        raise ExpressionError(
            "Cannot add hours, minutes, or seconds to a date. Use a datetime instead."
        )

    adjusted = add_months(value, duration.months)

    try:
        return adjusted + timedelta(days=duration.days)
    except OverflowError as error:
        raise ExpressionError(
            "The resulting date is outside the valid range."
        ) from error


def add_duration_to_datetime(
    value: datetime,
    duration: Duration,
) -> datetime:
    """Add a duration to a datetime, applying months calendar-aware
    (via add_months) and days/seconds as a plain timedelta.
    """

    adjusted_date = add_months(value.date(), duration.months)

    adjusted = value.replace(
        year=adjusted_date.year,
        month=adjusted_date.month,
        day=adjusted_date.day,
    )

    try:
        return adjusted + timedelta(
            days=duration.days,
            seconds=duration.seconds,
        )
    except OverflowError as error:
        raise ExpressionError(
            "The resulting datetime is outside the valid range."
        ) from error


def add_duration_to_time(value: time, duration: Duration) -> time:
    """Add a duration's days/seconds to a clock time, wrapping around
    midnight (a bare time has no date to carry the overflow into).
    """

    if duration.months:
        raise ExpressionError(
            "Calendar months or years cannot be added to a clock time."
        )

    # A time has no date, so arithmetic wraps around midnight.
    reference = datetime.combine(date(2000, 1, 1), value)

    result = reference + timedelta(
        days=duration.days,
        seconds=duration.seconds,
    )

    return result.time()


def timedelta_to_duration(value: timedelta) -> Duration:
    """Convert a plain timedelta (e.g. from a datetime subtraction)
    into a months=0 Duration of whole days and seconds.
    """

    total_seconds = int(value.total_seconds())

    days, seconds = divmod(abs(total_seconds), 86_400)

    if total_seconds < 0:
        days = -days
        seconds = -seconds

    return Duration(days=days, seconds=seconds)


def time_to_seconds(value: time) -> int:
    """Seconds since midnight, used to diff two `time` values."""

    return value.hour * 3600 + value.minute * 60 + value.second


# ---- Time-intelligence date boundaries -----------------------------
#
# Pure date -> date building blocks (DAX's vocabulary — STARTOFMONTH,
# ENDOFMONTH, ... — reimplemented as ordinary explicit functions, no
# implicit filter context). A DAX-style year-to-date total is just
# sum(filter(t, [date] >= start_of_year(asof) and [date] <= asof)::amount)
# composed from these plus the table verbs, not a separate mechanism.


def start_of_month(value: date) -> date:
    """The first day of `value`'s month."""

    return value.replace(day=1)


def end_of_month(value: date) -> date:
    """The last day of `value`'s month."""

    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def start_of_quarter(value: date) -> date:
    """The first day of `value`'s calendar quarter (Jan/Apr/Jul/Oct 1)."""

    quarter_start_month = 3 * ((value.month - 1) // 3) + 1
    return value.replace(month=quarter_start_month, day=1)


def end_of_quarter(value: date) -> date:
    """The last day of `value`'s calendar quarter."""

    quarter_start_month = 3 * ((value.month - 1) // 3) + 1
    end_month = quarter_start_month + 2
    return date(value.year, end_month, calendar.monthrange(value.year, end_month)[1])


def start_of_year(value: date) -> date:
    """January 1st of `value`'s year."""

    return value.replace(month=1, day=1)


def end_of_year(value: date) -> date:
    """December 31st of `value`'s year."""

    return value.replace(month=12, day=31)
