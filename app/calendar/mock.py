from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.calendar.models import (
    Appointment,
    AppointmentRequest,
    AvailabilityQuery,
    AvailabilityResult,
    CalendarOperationResult,
    CalendarStatus,
    TimeRange,
)
from app.config.business_config import BusinessConfig
from app.phone.israel_phone import normalize_israeli_phone


class MockCalendar:
    """Deterministic in-memory calendar for demo and offline tests."""

    def __init__(
        self,
        business: BusinessConfig,
        *,
        appointments: list[Appointment] | None = None,
        availability_windows: list[TimeRange] | None = None,
    ):
        self.business = business
        self.appointments = {item.appointment_id: item for item in appointments or []}
        self.availability_windows = availability_windows
        self.fail_next_operation = False

    def _failure(self) -> CalendarOperationResult | None:
        if not self.fail_next_operation:
            return None
        self.fail_next_operation = False
        return CalendarOperationResult(
            status=CalendarStatus.FAILED, reason="Mock calendar operation failed"
        )

    def _busy(self, requested: TimeRange, *, exclude_id: str = "") -> bool:
        return any(
            item.appointment_id != exclude_id
            and item.status == "booked"
            and requested.overlaps(TimeRange(start=item.start, end=item.end))
            for item in self.appointments.values()
        )

    def _within_allowed_time(self, requested: TimeRange) -> bool:
        if self.availability_windows is not None:
            return any(
                window.start <= requested.start and requested.end <= window.end
                for window in self.availability_windows
            )
        local_start = requested.start.astimezone(ZoneInfo(self.business.timezone))
        local_end = requested.end.astimezone(ZoneInfo(self.business.timezone))
        zone = ZoneInfo(self.business.timezone)
        for date in (local_start.date(), local_start.date() - timedelta(days=1)):
            for start, end in self.business.business_hours.get(str(date.weekday()), []):
                opening = datetime.combine(date, time.fromisoformat(start), zone)
                closing_date = date + timedelta(days=1) if end < start else date
                closing = datetime.combine(closing_date, time.fromisoformat(end), zone)
                if opening <= local_start and local_end <= closing:
                    return True
        return False

    def _candidate_windows(self, query: AvailabilityQuery):
        if self.availability_windows is not None:
            yield from self.availability_windows
            return
        zone = ZoneInfo(self.business.timezone)
        local_start = query.start.astimezone(zone)
        local_end = query.end.astimezone(zone)
        date = local_start.date()
        while date <= local_end.date():
            for start, end in self.business.business_hours.get(str(date.weekday()), []):
                opening = datetime.combine(date, time.fromisoformat(start), zone)
                closing_date = date + timedelta(days=1) if end < start else date
                closing = datetime.combine(closing_date, time.fromisoformat(end), zone)
                yield TimeRange(start=opening, end=closing)
            date += timedelta(days=1)

    async def check_availability(self, query: AvailabilityQuery) -> AvailabilityResult:
        duration = timedelta(minutes=query.duration_minutes)
        slots: list[TimeRange] = []
        for window in self._candidate_windows(query):
            cursor = window.start
            while cursor + duration <= window.end:
                slot = TimeRange(start=cursor, end=cursor + duration)
                if slot.start >= query.start and slot.end <= query.end and not self._busy(slot):
                    slots.append(slot)
                cursor += duration
        if not slots:
            return AvailabilityResult(
                status=CalendarStatus.NO_AVAILABILITY,
                reason="No conflict-free slots in the requested range",
            )
        return AvailabilityResult(status=CalendarStatus.SUCCEEDED, slots=slots)

    async def create_appointment(self, request: AppointmentRequest) -> CalendarOperationResult:
        if failure := self._failure():
            return failure
        phone = normalize_israeli_phone(request.phone_raw)
        if not phone:
            return CalendarOperationResult(
                status=CalendarStatus.NEEDS_CLARIFICATION,
                reason="A valid Israeli phone number is required",
            )
        requested = TimeRange(start=request.start, end=request.end)
        if not self._within_allowed_time(requested) or self._busy(requested):
            return CalendarOperationResult(
                status=CalendarStatus.NO_AVAILABILITY,
                reason="The requested time is unavailable",
            )
        appointment = Appointment(
            appointment_id="mock-" + uuid4().hex,
            customer_name=request.customer_name,
            phone_normalized=phone,
            service=request.service,
            start=request.start,
            end=request.end,
            address=request.address,
            notes=request.notes,
            language=request.language,
        )
        self.appointments[appointment.appointment_id] = appointment
        return CalendarOperationResult(status=CalendarStatus.SUCCEEDED, appointment=appointment)

    async def get_appointment(self, appointment_id: str) -> CalendarOperationResult:
        appointment = self.appointments.get(appointment_id)
        if not appointment:
            return CalendarOperationResult(
                status=CalendarStatus.FAILED, reason="Appointment not found"
            )
        if appointment.status != "booked":
            return CalendarOperationResult(
                status=CalendarStatus.FAILED, reason="Appointment is not active"
            )
        return CalendarOperationResult(status=CalendarStatus.SUCCEEDED, appointment=appointment)

    async def cancel_appointment(
        self, appointment_id: str, *, phone_normalized: str = ""
    ) -> CalendarOperationResult:
        if failure := self._failure():
            return failure
        appointment = self.appointments.get(appointment_id)
        if not appointment:
            return CalendarOperationResult(
                status=CalendarStatus.FAILED, reason="Appointment not found"
            )
        if appointment.status != "booked":
            return CalendarOperationResult(
                status=CalendarStatus.FAILED, reason="Appointment is not active"
            )
        if phone_normalized and appointment.phone_normalized != phone_normalized:
            return CalendarOperationResult(
                status=CalendarStatus.NEEDS_CLARIFICATION,
                reason="Phone number does not match the appointment",
            )
        appointment.status = "cancelled"
        return CalendarOperationResult(status=CalendarStatus.SUCCEEDED, appointment=appointment)

    async def reschedule_appointment(
        self, appointment_id: str, new_start: datetime, *, phone_normalized: str = ""
    ) -> CalendarOperationResult:
        if failure := self._failure():
            return failure
        current = self.appointments.get(appointment_id)
        if not current:
            return CalendarOperationResult(
                status=CalendarStatus.FAILED, reason="Appointment not found"
            )
        if current.status != "booked":
            return CalendarOperationResult(
                status=CalendarStatus.FAILED, reason="Appointment is not active"
            )
        if phone_normalized and current.phone_normalized != phone_normalized:
            return CalendarOperationResult(
                status=CalendarStatus.NEEDS_CLARIFICATION,
                reason="Phone number does not match the appointment",
            )
        duration = current.end - current.start
        requested = TimeRange(start=new_start, end=new_start + duration)
        if not self._within_allowed_time(requested) or self._busy(
            requested, exclude_id=appointment_id
        ):
            return CalendarOperationResult(
                status=CalendarStatus.NO_AVAILABILITY,
                reason="The requested time is unavailable",
            )
        current.start = requested.start
        current.end = requested.end
        current.status = "booked"
        return CalendarOperationResult(status=CalendarStatus.SUCCEEDED, appointment=current)
