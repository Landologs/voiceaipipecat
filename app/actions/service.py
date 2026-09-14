from datetime import datetime
from enum import StrEnum
import logging
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from app.calendar.interface import CalendarBackend
from app.calendar.models import AppointmentRequest, AvailabilityQuery, CalendarStatus
from app.config.business_config import BusinessConfig
from app.leads.models import CallResult
from app.leads.storage import LeadRepository
from app.messaging.whatsapp import WhatsAppBackend
from app.phone.israel_phone import normalize_israeli_phone
from app.addressing.israel import assess_service_area

logger = logging.getLogger(__name__)


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_AVAILABILITY = "no_availability"


class ActionResult(BaseModel):
    status: ActionStatus
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class BusinessActions:
    """The only layer allowed to change calendars, leads, or messaging state."""

    def __init__(
        self,
        business: BusinessConfig,
        calendar: CalendarBackend,
        leads: LeadRepository,
        whatsapp: WhatsAppBackend,
        caller_phone: str = "",
    ):
        self.business = business
        self.calendar = calendar
        self.leads = leads
        self.whatsapp = whatsapp
        self.caller_phone = normalize_israeli_phone(caller_phone)
        self.call_id = uuid4().hex
        self.verified_appointment_id = ""
        self.verified_appointment_status = "not_requested"

    @staticmethod
    def _from_calendar(result) -> ActionResult:
        mapping = {
            CalendarStatus.SUCCEEDED: ActionStatus.SUCCEEDED,
            CalendarStatus.FAILED: ActionStatus.FAILED,
            CalendarStatus.NEEDS_CLARIFICATION: ActionStatus.NEEDS_CLARIFICATION,
            CalendarStatus.NO_AVAILABILITY: ActionStatus.NO_AVAILABILITY,
        }
        if result.status == CalendarStatus.SUCCEEDED and not result.appointment:
            return ActionResult(
                status=ActionStatus.FAILED,
                message="Calendar returned success without an appointment record",
            )
        data = {}
        if result.appointment:
            data["appointment"] = result.appointment.model_dump(mode="json")
        return ActionResult(
            status=mapping[result.status],
            message=result.reason or result.status.value,
            data=data,
        )

    @staticmethod
    def _backend_failed(operation: str, exc: Exception) -> ActionResult:
        logger.error("Business action %s failed (%s)", operation, type(exc).__name__)
        return ActionResult(
            status=ActionStatus.FAILED,
            message="The calendar operation could not be completed",
        )

    async def check_availability(
        self, *, start: datetime, end: datetime, duration_minutes: int | None = None
    ) -> ActionResult:
        try:
            query = AvailabilityQuery(
                start=start,
                end=end,
                duration_minutes=duration_minutes or self.business.appointment_duration_minutes,
            )
        except ValidationError:
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Provide a valid timezone-aware start, end, and duration",
            )
        try:
            result = await self.calendar.check_availability(query)
        except Exception as exc:
            return self._backend_failed("check_availability", exc)
        if result.status == CalendarStatus.SUCCEEDED and not result.slots:
            return ActionResult(
                status=ActionStatus.NO_AVAILABILITY,
                message="Calendar returned no available slots",
            )
        status = {
            CalendarStatus.SUCCEEDED: ActionStatus.SUCCEEDED,
            CalendarStatus.FAILED: ActionStatus.FAILED,
            CalendarStatus.NEEDS_CLARIFICATION: ActionStatus.NEEDS_CLARIFICATION,
            CalendarStatus.NO_AVAILABILITY: ActionStatus.NO_AVAILABILITY,
        }[result.status]
        return ActionResult(
            status=status,
            message=result.reason or result.status.value,
            data={"slots": [slot.model_dump(mode="json") for slot in result.slots]},
        )

    async def create_appointment(self, **values) -> ActionResult:
        caller_confirmed = values.pop("caller_confirmed", False)
        if caller_confirmed is not True:
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Explicit caller confirmation is required before booking",
            )
        if self.caller_phone:
            values["phone_raw"] = self.caller_phone
        values.setdefault("duration_minutes", self.business.appointment_duration_minutes)
        try:
            request = AppointmentRequest.model_validate(values)
        except ValidationError:
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Name, valid phone, service, timezone-aware start, and duration are required",
            )
        if request.service not in self.business.services:
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Service is not in the approved business configuration",
            )
        if self.business.service_area:
            address = assess_service_area(request.address, self.business)
            if not address.is_in_service_area:
                return ActionResult(
                    status=ActionStatus.NEEDS_CLARIFICATION,
                    message=(
                        "The address is outside the configured service area or its city "
                        "has not been confirmed"
                    ),
                    data={"default_country": address.country_code},
                )
        try:
            result = await self.calendar.create_appointment(request)
        except Exception as exc:
            return self._backend_failed("create_appointment", exc)
        action = self._from_calendar(result)
        if result.status == CalendarStatus.SUCCEEDED and result.appointment:
            self.verified_appointment_id = result.appointment.appointment_id
            self.verified_appointment_status = "booked"
        return action

    async def cancel_appointment(self, *, appointment_id: str, phone_raw: str) -> ActionResult:
        phone = normalize_israeli_phone(phone_raw)
        if not appointment_id.strip() or not phone:
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Appointment ID and a valid Israeli phone number are required",
            )
        try:
            result = await self.calendar.cancel_appointment(
                appointment_id.strip(), phone_normalized=phone
            )
        except Exception as exc:
            return self._backend_failed("cancel_appointment", exc)
        action = self._from_calendar(result)
        if result.status == CalendarStatus.SUCCEEDED and result.appointment:
            self.verified_appointment_id = result.appointment.appointment_id
            self.verified_appointment_status = "cancelled"
        return action

    async def get_appointment(self, *, appointment_id: str, phone_raw: str) -> ActionResult:
        phone = normalize_israeli_phone(phone_raw)
        if not appointment_id.strip() or not phone:
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Appointment ID and a valid Israeli phone number are required",
            )
        try:
            result = await self.calendar.get_appointment(appointment_id.strip())
        except Exception as exc:
            return self._backend_failed("get_appointment", exc)
        if (
            result.status == CalendarStatus.SUCCEEDED
            and result.appointment
            and result.appointment.phone_normalized != phone
        ):
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Phone number does not match the appointment",
            )
        return self._from_calendar(result)

    async def reschedule_appointment(
        self, *, appointment_id: str, phone_raw: str, new_start: datetime
    ) -> ActionResult:
        phone = normalize_israeli_phone(phone_raw)
        if (
            not appointment_id.strip()
            or not phone
            or new_start.tzinfo is None
            or new_start.utcoffset() is None
        ):
            return ActionResult(
                status=ActionStatus.NEEDS_CLARIFICATION,
                message="Appointment ID, valid phone, and timezone-aware new start are required",
            )
        try:
            result = await self.calendar.reschedule_appointment(
                appointment_id.strip(), new_start, phone_normalized=phone
            )
        except Exception as exc:
            return self._backend_failed("reschedule_appointment", exc)
        action = self._from_calendar(result)
        if result.status == CalendarStatus.SUCCEEDED and result.appointment:
            self.verified_appointment_id = result.appointment.appointment_id
            self.verified_appointment_status = "rescheduled"
        return action

    def save_lead(self, result: CallResult) -> ActionResult:
        result.call_id = result.call_id or self.call_id
        if self.caller_phone:
            result.phone_raw = self.caller_phone
            result.phone_normalized = self.caller_phone
        try:
            self.leads.save(result, summary_status="captured")
        except (OSError, ValidationError):
            return ActionResult(status=ActionStatus.FAILED, message="Lead could not be saved")
        return ActionResult(
            status=ActionStatus.SUCCEEDED,
            message="Lead saved",
            data={"call_id": result.call_id},
        )

    def get_business_info(self, *, topic: str = "") -> ActionResult:
        status = self.business.hours_status(datetime.now(ZoneInfo(self.business.timezone)))
        data = self.business.model_dump(mode="json")
        data["hours_status"] = status.model_dump(mode="json")
        if topic:
            key = topic.strip().lower()
            aliases = {
                "hours": "business_hours", "services": "services", "prices": "known_prices",
                "faq": "faq", "area": "service_area", "policies": "callback_policy",
            }
            selected = aliases.get(key)
            if selected:
                data = {selected: data[selected], "hours_status": data["hours_status"]}
        return ActionResult(
            status=ActionStatus.SUCCEEDED, message="Approved business information", data=data
        )

    def apply_verified_calendar_state(self, result: CallResult) -> CallResult:
        result.call_id = result.call_id or self.call_id
        if self.caller_phone:
            result.phone_raw = self.caller_phone
            result.phone_normalized = self.caller_phone
        if self.verified_appointment_id:
            result.appointment_id = self.verified_appointment_id
            result.appointment_status = self.verified_appointment_status
        elif result.appointment_status in {"booked", "cancelled", "rescheduled"}:
            result.appointment_id = ""
            result.appointment_status = "pending"
        return result
