"""Calendar interfaces and safe MVP implementations."""

from app.calendar.interface import CalendarBackend
from app.calendar.mock import MockCalendar
from app.calendar.models import (
    Appointment,
    AppointmentRequest,
    AvailabilityQuery,
    AvailabilityResult,
    CalendarOperationResult,
    CalendarStatus,
    TimeRange,
)

__all__ = [
    "Appointment",
    "AppointmentRequest",
    "AvailabilityQuery",
    "AvailabilityResult",
    "CalendarBackend",
    "CalendarOperationResult",
    "CalendarStatus",
    "MockCalendar",
    "TimeRange",
]
