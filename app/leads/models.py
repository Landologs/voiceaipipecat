from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator
from app.phone.israel_phone import normalize_israeli_phone


class CallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_name: str = ""
    phone_raw: str = ""
    phone_normalized: str = ""
    phone_confirmed: bool = False
    intent: str = ""
    request_summary: str = ""
    address: str = ""
    preferred_time: str = ""
    urgency: Literal["standard", "urgent", "emergency"] = "standard"
    appointment_status: Literal["not_requested", "pending"] = "not_requested"
    after_hours: bool = False
    notes: str = ""
    next_action: str = ""

    @model_validator(mode="after")
    def normalize_phone(self):
        self.phone_normalized = normalize_israeli_phone(self.phone_raw)
        if not self.phone_normalized:
            self.phone_confirmed = False
        return self
