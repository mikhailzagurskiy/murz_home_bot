from calendar import FEBRUARY, JANUARY
from enum import Enum
from mongoengine import *  # type: ignore


class RecurringUnit(Enum):
    """Unit of regular recurrence"""

    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"


class RecurrenceWeekDay(Enum):
    """Name of the day of weekly recurrence"""

    SUNDAY = "sunday"
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"


class RecurrenceMonth(Enum):
    """Name of the month of monthly recurrence"""

    JANUARY = "january"
    FEBRUARY = "february"
    MARCH = "march"
    APRIL = "april"
    MAY = "may"
    JUNE = "june"
    JULY = "july"
    AUGUST = "august"
    SEPTEMBER = "september"
    OCTOBER = "october"
    NOVEMBER = "november"
    DECEMBER = "december"


class RecurrenceMonthDay(Enum):
    """Name of the day of complex monthly recurrence"""

    SUNDAY = "sunday"
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    DAY = "day"
    WEEKDAY = "weekday"
    WEEKEND = "weekend"


class RecurrenceMonthUnit(Enum):
    """Unit of complex monthly recurrence"""

    FIRST = "first"
    SECOND = "second"
    THIRD = "third"
    LAST = "last"


class Recurrence(EmbeddedDocument):
    """Base class for all types of recurrence"""

    # How many times an event should be fired before expiration
    repetition_count = IntField(required=False)
    # When to start schedule an event
    start_after = DateTimeField(required=False)
    # When to expire an event
    stop_after = DateTimeField(required=False)


class Regular(Recurrence):
    """Regular recurrence

    Fired once per N numbers of hours/minutes/seconds

    Examples:
        - every 1 hour
        - every 28 minutes
        - every 375 seconds
    """

    # Every N chosen units an event will be fired
    every = IntField(required=True)
    # Chosen unit
    unit = EnumField(RecurringUnit, required=True)


class Daily(Recurrence):
    """Daily recurrence

    Fired every N days

    Examples:
        - every 1 day
        - every 5 days
        - every 900 days
    """

    # Every N day an event will be fired
    every = IntField(required=True)


class Weekly(Recurrence):
    """Weekly recurrence

    Fired every N week on chosen days of the week

    Examples:
        - every 1 week on Mon
        - every 2 weeks on Sat and Sun
        - every 50 weeks on Sun, Mon, Fri and Sat
    """

    # Every N week an event will be fired
    every = IntField(required=True)

    # Every chosen day of the week
    on = ListField(EnumField(RecurrenceWeekDay), required=False)


class Monthly(Recurrence):
    """Base class for monthly recurrence"""

    # Every N month an event will be fired
    every = IntField(required=True)


class MonthlyOn(Monthly):
    """Simple monthly recurrence

    Fired every N months at chosen date

    Examples:
        - every 1 month on 12 day of the month
        - every 3 months on 5 day of the month
        - every 73 months on 28 day of the month
    """

    # Every chosen day of the month
    on = IntField(required=True)


class MonthlyComplex(Monthly):
    """Complex monthly recurrence

    Fired every N month at specific day

    Examples:
        - every 1 month on first day
        - every 3 months on last friday
        - every 73 months on second weekend
    """

    # Every specific day of a month
    day = EnumField(RecurrenceMonthUnit, required=True)

    # Every specific day type
    day_type = EnumField(RecurrenceMonthDay, required=True)


class Annual(Recurrence):
    """Base class for an annual event"""

    # Every N years
    every = IntField(required=True)


class AnnualOn(Annual):
    """Simple annual event

    Fired every N years on specified date

    Examples:
        - Every 1 year on 12 of October
        - Every 3 years on 1 of July
    """

    # Name of the month
    month = EnumField(RecurrenceMonth, required=True)

    # Date of the month
    date = IntField(required=True)


class AnnualWeek(Annual):
    """Annual recurrence every specified weeks

    Fired every N years in M week of the year on specified days of the week

    Examples:
        - every 1 year in 12 week on Sun
        - every 3 years in 1 week on Mon and Sat
        - every 10 years in 5 week on Sun, Mon, Fri and Sat
    """

    # In N week an event will be fired
    week = IntField(required=True)

    # Every chosen day of the week
    on = ListField(EnumField(RecurrenceWeekDay), required=False)


class AnnualMonth(Annual):
    """Base class for an annual event for specific months"""

    # Every chosen months of the year
    months = ListField(EnumField(RecurrenceMonth), required=True)


class AnnualMonthOn(AnnualMonth):
    """Simple annual recurrence within specified months

    Fired every N years in chosen months on specified date

    Examples:
        - every 1 year in September, October on 12
        - every 3 years in June, July, August on 1
        - every 10 years in January, February on 28
    """

    # Chosen date of the month
    date = IntField(required=True)


class AnnualMonthComplex(AnnualMonth):
    """Complex annual recurrence within specified months

    Fired every N years in chosen months on specific day of the month

    Examples:
        - every 1 year in September, October on last day
        - every 3 years in June, July, August on second Mon
        - every 10 years in January, February on third weekday
    """

    # Every specific day of a month
    day = EnumField(RecurrenceMonthUnit, required=True)

    # Every specific day type
    day_type = EnumField(RecurrenceMonthDay, required=True)
