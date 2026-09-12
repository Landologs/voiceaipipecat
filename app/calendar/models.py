from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must include a timezone")
    return value


class CalendarStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_AVAILABILITY = "no_availability"


class TimeRange(BaseModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value):
        return _require_aware(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("End must be after start")
        return self

    def overlaps(self, other: "TimeRange") -> bool:
        return self.start < other.end and other.start < self.end


class AvailabilityQuery(TimeRange):
    duration_minutes: int = Field(ge=5, le=1440)


class AppointmentRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)
    phone_raw: str = Field(min_length=1, max_length=100)
    service: str = Field(min_length=1, max_length=300)
    start: datetime
    duration_minutes: int = Field(ge=5, le=1440)
    address: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)
    language: str = Field(default="he", pattern="^(he|ru|en)$")

    @field_validator("start")
    @classmethod
    def aware_start(cls, value):
        return _require_aware(value)

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_minutes)

    def provider_record(self, timezone_name: str) -> dict:
        """Build the caller details a provider gateway should place on its event."""
        from app.phone.israel_phone import normalize_israeli_phone

        phone = normalize_israeli_phone(self.phone_raw)
        description = [
            f"Customer: {self.customer_name}",
            f"Phone: {phone or self.phone_raw}",
            f"Service: {self.service}",
        ]
        if self.address:
            description.append(f"Address: {self.address}")
        if self.notes:
            description.append(f"Notes: {self.notes}")
        return {
            "summary": f"{self.service} — {self.customer_name}",
            "description": "\n".join(description),
            "start": {"dateTime": self.start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": self.end.isoformat(), "timeZone": timezone_name},
            "metadata": {
                "phone_normalized": phone,
                "language": self.language,
                "service": self.service,
            },
        }


class Appointment(BaseModel):
    appointment_id: str
    customer_name: str
    phone_normalized: str
    service: str
    start: datetime
    end: datetime
    address: str = ""
    notes: str = ""
    language: str = "he"
    status: Literal["booked", "cancelled"] = "booked"

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value):
        return _require_aware(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Appointment end must be after start")
        return self


class AvailabilityResult(BaseModel):
    status: CalendarStatus
    slots: list[TimeRange] = Field(default_factory=list)
    reason: str = ""


class CalendarOperationResult(BaseModel):
    status: CalendarStatus
    appointment: Appointment | None = None
    reason: str = ""
