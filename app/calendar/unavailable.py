from datetime import datetime

from app.calendar.models import (
    AppointmentRequest,
    AvailabilityQuery,
    AvailabilityResult,
    CalendarOperationResult,
    CalendarStatus,
)


class UnconfiguredCalendar:
    def _operation(self) -> CalendarOperationResult:
        return CalendarOperationResult(
            status=CalendarStatus.FAILED, reason="Calendar is not configured"
        )

    async def check_availability(self, query: AvailabilityQuery) -> AvailabilityResult:
        return AvailabilityResult(
            status=CalendarStatus.FAILED, reason="Calendar is not configured"
        )

    async def create_appointment(self, request: AppointmentRequest) -> CalendarOperationResult:
        return self._operation()

    async def get_appointment(self, appointment_id: str) -> CalendarOperationResult:
        return self._operation()

    async def cancel_appointment(
        self, appointment_id: str, *, phone_normalized: str = ""
    ) -> CalendarOperationResult:
        return self._operation()

    async def reschedule_appointment(
        self, appointment_id: str, new_start: datetime, *, phone_normalized: str = ""
    ) -> CalendarOperationResult:
        return self._operation()
