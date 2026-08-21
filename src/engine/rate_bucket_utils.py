# ---- rate_bucket() --------------------------------------------------
from datetime import date, datetime, time, timedelta

from .calendar_utils import timedelta_to_duration
from .values import Duration, Type

_RATE_BUCKET_SCHEMA = (
    ("total_duration", Type("duration")),
    ("normal", Type("duration")),
    ("overtime_150", Type("duration")),
    ("overtime_200", Type("duration")),
)

_RATE_BUCKET_REQUIRED_FIELDS = {
    "date": Type("date"),
    "start_time": Type("time"),
    "end_time": Type("time"),
    "day_name": Type("text"),
}

_RATE_BUCKET_NORMAL_CUTOFF = time(17, 0)
_RATE_BUCKET_SPECIAL_CUTOFF = time(16, 0)

# Normalize names because date::DAYNAME currently gives "Saturday",
# whereas existing data may contain "Sat".
_RATE_BUCKET_SPECIAL_DAYS = frozenset(
    {
        "sun",
        "sunday",
        "ph",
    }
)


def _rate_bucket_is_special(day_name: str) -> bool:
    return day_name.strip().casefold() in _RATE_BUCKET_SPECIAL_DAYS


def _rate_bucket_segment(
    start: datetime,
    end: datetime,
    lower: datetime,
    upper: datetime,
) -> timedelta:
    """Duration of [start, end) overlapping [lower, upper)."""

    overlap_start = max(start, lower)
    overlap_end = min(end, upper)

    if overlap_end <= overlap_start:
        return timedelta()

    return overlap_end - overlap_start


def _rate_bucket_values(
    service_date: date,
    start_time: time,
    end_time: time,
    day_name: str,
) -> tuple[Duration, Duration, Duration, Duration]:
    crossed_midnight = end_time < start_time

    start = datetime.combine(
        service_date,
        start_time,
    )

    end_date = service_date + timedelta(days=1) if crossed_midnight else service_date

    end = datetime.combine(
        end_date,
        end_time,
    )

    is_special = _rate_bucket_is_special(day_name)

    cutoff_time = (
        _RATE_BUCKET_SPECIAL_CUTOFF if is_special else _RATE_BUCKET_NORMAL_CUTOFF
    )

    cutoff = datetime.combine(
        service_date,
        cutoff_time,
    )

    midnight = datetime.combine(
        service_date + timedelta(days=1),
        time(),
    )

    pre_cutoff = _rate_bucket_segment(
        start,
        end,
        start,
        cutoff,
    )

    cutoff_to_midnight = _rate_bucket_segment(
        start,
        end,
        cutoff,
        midnight,
    )

    after_midnight = _rate_bucket_segment(
        start,
        end,
        midnight,
        end,
    )

    zero = timedelta()

    normal = zero if is_special else pre_cutoff

    overtime_150 = pre_cutoff if is_special else cutoff_to_midnight

    overtime_200 = after_midnight + (cutoff_to_midnight if is_special else zero)

    return (
        timedelta_to_duration(end - start),
        timedelta_to_duration(normal),
        timedelta_to_duration(overtime_150),
        timedelta_to_duration(overtime_200),
    )
