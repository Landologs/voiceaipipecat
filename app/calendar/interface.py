from datetime import datetime
from typing import Protocol

from app.calendar.models import (
    AppointmentRequest,
    AvailabilityQuery,
    AvailabilityResult,
    CalendarOperationResult,
)


class CalendarBackend(Protocol):
    async def check_availability(self, query: AvailabilityQuery) -> AvailabilityResult: ...

    async def create_appointment(self, request: AppointmentRequest) -> CalendarOperationResult: ...

    async def get_appointment(self, appointment_id: str) -> CalendarOperationResult: ...

    async def cancel_appointment(
        self, appointment_id: str, *, phone_normalized: str = ""
    ) -> CalendarOperationResult: ...

    async def reschedule_appointment(
        self, appointment_id: str, new_start: datetime, *, phone_normalized: str = ""
    ) -> CalendarOperationResult: ...
