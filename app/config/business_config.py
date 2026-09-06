import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator


class BusinessConfig(BaseModel):
    company_name: str
    agent_name: str
    language: str = "he"
    timezone: str = "Asia/Jerusalem"
    business_hours: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)
    services: list[str] = Field(default_factory=list)
    service_area: list[str] = Field(default_factory=list)
    pricing_rules: list[str] = Field(default_factory=list)
    faq: dict[str, str] = Field(default_factory=dict)
    callback_policy: str = ""
    escalation_policy: str = ""

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator("business_hours")
    @classmethod
    def valid_hours(cls, value):
        for day, intervals in value.items():
            if day not in {str(i) for i in range(7)}:
                raise ValueError("Hours use weekday keys 0=Monday through 6=Sunday")
            for start, end in intervals:
                for item in (start, end):
                    parsed = time.fromisoformat(item)
                    if parsed.tzinfo or len(item) != 5:
                        raise ValueError("Hours must use HH:MM local time")
                if start == end:
                    raise ValueError("Opening and closing times must differ")
        return value

    @classmethod
    def load(cls, path: Path):
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))

    def is_open(self, at: datetime) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Business-hours checks require an aware datetime")
        local = at.astimezone(ZoneInfo(self.timezone))
        for date in (local.date(), local.date() - timedelta(days=1)):
            for start, end in self.business_hours.get(str(date.weekday()), []):
                opening = datetime.combine(date, time.fromisoformat(start), ZoneInfo(self.timezone))
                closing_date = date + timedelta(days=1) if end < start else date
                closing = datetime.combine(closing_date, time.fromisoformat(end), ZoneInfo(self.timezone))
                if opening <= local < closing:
                    return True
        return False
