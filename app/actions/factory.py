import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.actions.service import BusinessActions
from app.calendar.google import GoogleCalendarAdapter
from app.calendar.mock import MockCalendar
from app.calendar.models import Appointment, TimeRange
from app.calendar.unavailable import UnconfiguredCalendar
from app.leads.storage import JsonLeadRepository
from app.messaging.whatsapp import MockWhatsApp, UnconfiguredWhatsApp


def _demo_calendar(settings, business, at: datetime) -> MockCalendar:
    payload = json.loads(settings.calendar_path.read_text(encoding="utf-8-sig"))
    local = at.astimezone(ZoneInfo(business.timezone))
    windows: list[TimeRange] = []
    appointments: list[Appointment] = []
    for day in payload.get("days", []):
        date = local.date() + timedelta(days=int(day["offset"]))
        for index, slot in enumerate(day.get("slots", [])):
            start = datetime.fromisoformat(f"{date.isoformat()}T{slot['start']}:00").replace(
                tzinfo=ZoneInfo(business.timezone)
            )
            end = datetime.fromisoformat(f"{date.isoformat()}T{slot['end']}:00").replace(
                tzinfo=ZoneInfo(business.timezone)
            )
            period = TimeRange(start=start, end=end)
            if slot.get("status") == "available":
                windows.append(period)
            else:
                appointments.append(Appointment(
                    appointment_id=f"demo-occupied-{day['offset']}-{index}",
                    customer_name="Demo occupied slot",
                    phone_normalized="+972500000000",
                    service="Occupied",
                    start=start,
                    end=end,
                ))
    return MockCalendar(
        business, appointments=appointments, availability_windows=windows
    )


def create_business_actions(
    settings, business, at: datetime, *, caller_phone: str = ""
) -> BusinessActions:
    provider = business.calendar.provider
    if provider == "mock" and settings.demo_mode and settings.calendar_path:
        calendar = _demo_calendar(settings, business, at)
    elif provider == "mock":
        calendar = MockCalendar(business)
    elif provider == "google":
        calendar = GoogleCalendarAdapter()
    else:
        calendar = UnconfiguredCalendar()
    whatsapp = MockWhatsApp() if settings.demo_mode else UnconfiguredWhatsApp()
    return BusinessActions(
        business,
        calendar,
        JsonLeadRepository(settings.results_path),
        whatsapp,
        caller_phone=caller_phone,
    )
