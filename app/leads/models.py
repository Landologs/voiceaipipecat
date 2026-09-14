from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.phone.israel_phone import normalize_israeli_phone


class CallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str = ""
    customer_name: str = ""
    phone_raw: str = ""
    phone_normalized: str = ""
    phone_confirmed: bool = False
    intent: str = ""
    language: str = Field(default="", pattern=r"^$|^[A-Za-z]{2,3}(?:-[A-Za-z]{2})?$")
    service_request: str = ""
    # Kept for backward compatibility with results written by the original MVP.
    plumbing_problem: str = ""
    request_summary: str = ""
    address: str = ""
    preferred_time: str = ""
    urgency: Literal["standard", "urgent", "emergency"] = "standard"
    appointment_status: Literal[
        "not_requested", "pending", "booked", "cancelled", "rescheduled", "failed"
    ] = "not_requested"
    appointment_id: str = ""
    after_hours: bool = False
    notes: str = ""
    next_action: str = ""
    call_started_at: datetime | None = None
    call_ended_at: datetime | None = None

    @field_validator("call_started_at", "call_ended_at")
    @classmethod
    def aware_datetimes(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Call timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def normalize_phone(self):
        if self.service_request and not self.plumbing_problem:
            self.plumbing_problem = self.service_request
        elif self.plumbing_problem and not self.service_request:
            self.service_request = self.plumbing_problem
        self.phone_normalized = normalize_israeli_phone(self.phone_raw)
        if not self.phone_normalized:
            self.phone_confirmed = False
        if self.appointment_status in {"booked", "cancelled", "rescheduled"} and not self.appointment_id:
            raise ValueError("Successful appointment status requires appointment_id")
        if self.call_started_at and self.call_ended_at and self.call_ended_at < self.call_started_at:
            raise ValueError("call_ended_at cannot be before call_started_at")
        return self
