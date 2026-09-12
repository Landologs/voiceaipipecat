from datetime import datetime
from typing import Protocol

from app.calendar.interface import CalendarBackend
from app.calendar.models import (
    AppointmentRequest,
    AvailabilityQuery,
    AvailabilityResult,
    CalendarOperationResult,
    CalendarStatus,
)


class GoogleCalendarGateway(Protocol):
    """Narrow boundary to be implemented with an authenticated Google client."""

    async def check_availability(self, query: AvailabilityQuery) -> AvailabilityResult: ...
    async def create_appointment(self, request: AppointmentRequest) -> CalendarOperationResult: ...
    async def get_appointment(self, appointment_id: str) -> CalendarOperationResult: ...
    async def cancel_appointment(self, appointment_id: str) -> CalendarOperationResult: ...
    async def reschedule_appointment(
        self, appointment_id: str, new_start: datetime
    ) -> CalendarOperationResult: ...


class GoogleCalendarAdapter(CalendarBackend):
    """Provider-neutral adapter; credentials remain inside the future gateway."""

    def __init__(self, gateway: GoogleCalendarGateway | None = None):
        self.gateway = gateway

    def _unconfigured(self) -> CalendarOperationResult:
        return CalendarOperationResult(
            status=CalendarStatus.FAILED,
            reason="Google Calendar is not configured",
        )

    async def check_availability(self, query: AvailabilityQuery) -> AvailabilityResult:
        if not self.gateway:
            return AvailabilityResult(
                status=CalendarStatus.FAILED, reason="Google Calendar is not configured"
            )
        return await self.gateway.check_availability(query)

    async def create_appointment(self, request: AppointmentRequest) -> CalendarOperationResult:
        if not self.gateway:
            return self._unconfigured()
        availability = await self.gateway.check_availability(AvailabilityQuery(
            start=request.start,
            end=request.end,
            duration_minutes=request.duration_minutes,
        ))
        exact_slot = any(
            slot.start <= request.start and request.end <= slot.end
            for slot in availability.slots
        )
        if availability.status != CalendarStatus.SUCCEEDED or not exact_slot:
            return CalendarOperationResult(
                status=CalendarStatus.NO_AVAILABILITY,
                reason=availability.reason or "The requested time is unavailable",
            )
        return await self.gateway.create_appointment(request)

    async def get_appointment(self, appointment_id: str) -> CalendarOperationResult:
        return self._unconfigured() if not self.gateway else await self.gateway.get_appointment(appointment_id)

    async def cancel_appointment(
        self, appointment_id: str, *, phone_normalized: str = ""
    ) -> CalendarOperationResult:
        if not self.gateway:
            return self._unconfigured()
        existing = await self.gateway.get_appointment(appointment_id)
        if existing.status != CalendarStatus.SUCCEEDED or not existing.appointment:
            return existing
        if phone_normalized and existing.appointment.phone_normalized != phone_normalized:
            return CalendarOperationResult(
                status=CalendarStatus.NEEDS_CLARIFICATION,
                reason="Phone number does not match the appointment",
            )
        return await self.gateway.cancel_appointment(appointment_id)

    async def reschedule_appointment(
        self, appointment_id: str, new_start: datetime, *, phone_normalized: str = ""
    ) -> CalendarOperationResult:
        if not self.gateway:
            return self._unconfigured()
        existing = await self.gateway.get_appointment(appointment_id)
        if existing.status != CalendarStatus.SUCCEEDED or not existing.appointment:
            return existing
        if phone_normalized and existing.appointment.phone_normalized != phone_normalized:
            return CalendarOperationResult(
                status=CalendarStatus.NEEDS_CLARIFICATION,
                reason="Phone number does not match the appointment",
            )
        duration_minutes = int(
            (existing.appointment.end - existing.appointment.start).total_seconds() // 60
        )
        availability = await self.gateway.check_availability(AvailabilityQuery(
            start=new_start,
            end=new_start + (existing.appointment.end - existing.appointment.start),
            duration_minutes=duration_minutes,
        ))
        requested_end = new_start + (existing.appointment.end - existing.appointment.start)
        exact_slot = any(
            slot.start <= new_start and requested_end <= slot.end
            for slot in availability.slots
        )
        if availability.status != CalendarStatus.SUCCEEDED or not exact_slot:
            return CalendarOperationResult(
                status=CalendarStatus.NO_AVAILABILITY,
                reason=availability.reason or "The requested time is unavailable",
            )
        return await self.gateway.reschedule_appointment(appointment_id, new_start)
