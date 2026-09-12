import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


class CalendarConfig(BaseModel):
    provider: str = "none"
    calendar_id: str = ""

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value):
        if value not in {"none", "mock", "google"}:
            raise ValueError("Calendar provider must be none, mock, or google")
        return value


class BusinessHoursStatus(BaseModel):
    is_open_now: bool
    after_hours: bool
    next_open_time: datetime | None = None


class BusinessConfig(BaseModel):
    company_name: str
    agent_name: str
    primary_language: str = Field(
        default="he", validation_alias=AliasChoices("primary_language", "language")
    )
    supported_languages: list[str] = Field(default_factory=lambda: ["he", "ru", "en"])
    timezone: str = "Asia/Jerusalem"
    business_hours: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)
    services: list[str] = Field(default_factory=list)
    service_descriptions: dict[str, str] = Field(default_factory=dict)
    service_area: list[str] = Field(default_factory=list)
    known_prices: dict[str, str] = Field(default_factory=dict)
    pricing_rules: list[str] = Field(default_factory=list)
    appointment_duration_minutes: int = Field(default=60, ge=5, le=1440)
    faq: dict[str, str] = Field(default_factory=dict)
    after_hours_policy: str = ""
    urgent_call_policy: str = ""
    callback_policy: str = ""
    escalation_policy: str = ""
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)

    @model_validator(mode="after")
    def valid_languages_and_services(self):
        allowed = {"he", "ru", "en"}
        if self.primary_language not in allowed:
            raise ValueError("Primary language must be he, ru, or en")
        if not self.supported_languages or any(item not in allowed for item in self.supported_languages):
            raise ValueError("Supported languages may contain only he, ru, and en")
        if self.primary_language not in self.supported_languages:
            raise ValueError("Primary language must be included in supported languages")
        unknown = (set(self.service_descriptions) | set(self.known_prices)) - set(self.services)
        if unknown:
            raise ValueError("Service details reference an unknown service")
        return self

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

    def next_open_time(self, at: datetime) -> datetime | None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Business-hours checks require an aware datetime")
        local = at.astimezone(ZoneInfo(self.timezone))
        zone = ZoneInfo(self.timezone)
        for offset in range(8):
            date = local.date() + timedelta(days=offset)
            for start, _ in sorted(self.business_hours.get(str(date.weekday()), [])):
                opening = datetime.combine(date, time.fromisoformat(start), zone)
                if opening > local:
                    return opening
        return None

    def hours_status(self, at: datetime) -> BusinessHoursStatus:
        is_open = self.is_open(at)
        return BusinessHoursStatus(
            is_open_now=is_open,
            after_hours=not is_open,
            next_open_time=None if is_open else self.next_open_time(at),
        )
