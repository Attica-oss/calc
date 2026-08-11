"""Calendar-aware arithmetic on dates, datetimes, and times."""

import calendar
from datetime import date, datetime, time, timedelta

from .values import Duration, ExpressionError


def add_months(value: date, months: int) -> date:
    """Add calendar months while keeping the date valid."""

    total_months = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(total_months, 12)
    month = zero_based_month + 1

    if not 1 <= year <= 9999:
        raise ExpressionError("The resulting date is outside the valid range.")

    final_day = min(
        value.day,
        calendar.monthrange(year, month)[1],
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
    total_seconds = int(value.total_seconds())

    days, seconds = divmod(abs(total_seconds), 86_400)

    if total_seconds < 0:
        days = -days
        seconds = -seconds

    return Duration(days=days, seconds=seconds)


def time_to_seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


# ---- Time-intelligence date boundaries -----------------------------
#
# Pure date -> date building blocks (DAX's vocabulary — STARTOFMONTH,
# ENDOFMONTH, ... — reimplemented as ordinary explicit functions, no
# implicit filter context). A DAX-style year-to-date total is just
# sum(filter(t, [date] >= start_of_year(asof) and [date] <= asof)::amount)
# composed from these plus the table verbs, not a separate mechanism.


def start_of_month(value: date) -> date:
    return value.replace(day=1)


def end_of_month(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def start_of_quarter(value: date) -> date:
    quarter_start_month = 3 * ((value.month - 1) // 3) + 1
    return value.replace(month=quarter_start_month, day=1)


def end_of_quarter(value: date) -> date:
    quarter_start_month = 3 * ((value.month - 1) // 3) + 1
    end_month = quarter_start_month + 2
    return date(value.year, end_month, calendar.monthrange(value.year, end_month)[1])


def start_of_year(value: date) -> date:
    return value.replace(month=1, day=1)


def end_of_year(value: date) -> date:
    return value.replace(month=12, day=31)
