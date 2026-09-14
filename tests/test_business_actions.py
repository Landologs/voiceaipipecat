import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.actions.service import ActionStatus, BusinessActions
from app.calendar.google import GoogleCalendarAdapter
from app.calendar.mock import MockCalendar
from app.calendar.models import (
    AppointmentRequest,
    AvailabilityQuery,
    CalendarStatus,
)
from app.config.business_config import BusinessConfig
from app.config.settings import Settings
from app.leads.models import CallResult
from app.leads.storage import JsonLeadRepository
from app.messaging.whatsapp import MessageStatus, MockWhatsApp


ZONE = ZoneInfo("Asia/Jerusalem")


def business(**overrides):
    values = {
        "company_name": "Test",
        "agent_name": "Noa",
        "business_hours": {"0": [("09:00", "17:00")]},
        "services": ["inspection"],
        "service_descriptions": {"inspection": "Basic inspection"},
        "known_prices": {"inspection": "250 ILS"},
        "appointment_duration_minutes": 60,
    }
    values.update(overrides)
    return BusinessConfig(**values)


class BusinessConfigurationTests(unittest.TestCase):
    def test_expanded_configuration_and_legacy_language_alias(self):
        configured = business(language="ru", supported_languages=["ru", "he"])
        self.assertEqual(configured.primary_language, "ru")
        self.assertEqual(configured.service_descriptions["inspection"], "Basic inspection")
        self.assertEqual(configured.appointment_duration_minutes, 60)

    def test_primary_language_must_be_supported(self):
        with self.assertRaises(ValidationError):
            business(primary_language="en", supported_languages=["he", "ru"])

    def test_unknown_service_details_are_rejected(self):
        with self.assertRaises(ValidationError):
            business(known_prices={"unknown": "100 ILS"})

    def test_hours_status_exposes_next_open_time(self):
        configured = business()
        sunday = datetime(2026, 9, 13, 12, tzinfo=ZONE)
        status = configured.hours_status(sunday)
        self.assertFalse(status.is_open_now)
        self.assertTrue(status.after_hours)
        self.assertEqual(status.next_open_time, datetime(2026, 9, 14, 9, tzinfo=ZONE))

    def test_profile_controls_greeting_and_supports_other_languages(self):
        configured = business(
            primary_language="fr",
            supported_languages=["fr", "en"],
            greetings={"fr": "Bonjour"},
            goodbyes={"fr": "Au revoir"},
        )
        self.assertEqual(configured.greeting(), "Bonjour")
        self.assertEqual(configured.goodbye(), "Au revoir")


class MockCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.business = business()
        self.calendar = MockCalendar(self.business)
        self.start = datetime(2026, 9, 14, 10, tzinfo=ZONE)

    def request(self, start=None):
        return AppointmentRequest(
            customer_name="Dana",
            phone_raw="050-1234567",
            service="inspection",
            start=start or self.start,
            duration_minutes=60,
            address="Herzl 10",
        )

    async def test_availability_and_successful_booking(self):
        availability = await self.calendar.check_availability(AvailabilityQuery(
            start=datetime(2026, 9, 14, 9, tzinfo=ZONE),
            end=datetime(2026, 9, 14, 12, tzinfo=ZONE),
            duration_minutes=60,
        ))
        self.assertEqual(availability.status, CalendarStatus.SUCCEEDED)
        self.assertEqual(len(availability.slots), 3)
        created = await self.calendar.create_appointment(self.request())
        self.assertEqual(created.status, CalendarStatus.SUCCEEDED)
        self.assertTrue(created.appointment.appointment_id.startswith("mock-"))
        self.assertEqual(created.appointment.customer_name, "Dana")
        self.assertEqual(created.appointment.phone_normalized, "+972501234567")
        self.assertEqual(created.appointment.address, "Herzl 10")

    async def test_conflict_prevents_second_booking(self):
        first = await self.calendar.create_appointment(self.request())
        second = await self.calendar.create_appointment(self.request())
        self.assertEqual(first.status, CalendarStatus.SUCCEEDED)
        self.assertEqual(second.status, CalendarStatus.NO_AVAILABILITY)

    async def test_failed_booking_does_not_return_appointment(self):
        self.calendar.fail_next_operation = True
        result = await self.calendar.create_appointment(self.request())
        self.assertEqual(result.status, CalendarStatus.FAILED)
        self.assertIsNone(result.appointment)

    async def test_provider_record_preserves_caller_details(self):
        record = self.request().provider_record("Asia/Jerusalem")
        self.assertIn("Dana", record["description"])
        self.assertIn("+972501234567", record["description"])
        self.assertIn("Herzl 10", record["description"])
        self.assertEqual(record["metadata"]["language"], "he")

    async def test_cancel_and_reschedule(self):
        created = await self.calendar.create_appointment(self.request())
        appointment_id = created.appointment.appointment_id
        moved = await self.calendar.reschedule_appointment(
            appointment_id,
            self.start + timedelta(hours=2),
            phone_normalized="+972501234567",
        )
        self.assertEqual(moved.status, CalendarStatus.SUCCEEDED)
        self.assertEqual(moved.appointment.start, self.start + timedelta(hours=2))
        cancelled = await self.calendar.cancel_appointment(
            appointment_id, phone_normalized="+972501234567"
        )
        self.assertEqual(cancelled.status, CalendarStatus.SUCCEEDED)
        self.assertEqual(cancelled.appointment.status, "cancelled")
        repeated = await self.calendar.cancel_appointment(
            appointment_id, phone_normalized="+972501234567"
        )
        self.assertEqual(repeated.status, CalendarStatus.FAILED)

    async def test_google_adapter_is_safe_when_unconfigured(self):
        adapter = GoogleCalendarAdapter()
        result = await adapter.create_appointment(self.request())
        self.assertEqual(result.status, CalendarStatus.FAILED)
        self.assertIn("not configured", result.reason)


class ActionLayerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.business = business()
        self.calendar = MockCalendar(self.business)
        self.actions = BusinessActions(
            self.business,
            self.calendar,
            JsonLeadRepository(Path(self.directory.name)),
            MockWhatsApp(),
        )
        self.start = datetime(2026, 9, 14, 10, tzinfo=ZONE)

    async def test_action_validation_needs_clarification(self):
        result = await self.actions.create_appointment(
            customer_name="Dana",
            phone_raw="short",
            service="inspection",
            start=self.start,
            caller_confirmed=True,
        )
        self.assertEqual(result.status, ActionStatus.NEEDS_CLARIFICATION)
        self.assertEqual(self.actions.verified_appointment_id, "")

    async def test_action_success_is_applied_as_verified_state(self):
        action = await self.actions.create_appointment(
            customer_name="Dana",
            phone_raw="0501234567",
            service="inspection",
            start=self.start,
            caller_confirmed=True,
        )
        self.assertEqual(action.status, ActionStatus.SUCCEEDED)
        result = self.actions.apply_verified_calendar_state(CallResult())
        self.assertEqual(result.appointment_status, "booked")
        self.assertEqual(result.appointment_id, self.actions.verified_appointment_id)

    async def test_failed_action_never_sets_booked_state(self):
        self.calendar.fail_next_operation = True
        action = await self.actions.create_appointment(
            customer_name="Dana",
            phone_raw="0501234567",
            service="inspection",
            start=self.start,
            caller_confirmed=True,
        )
        self.assertEqual(action.status, ActionStatus.FAILED)
        self.assertEqual(self.actions.verified_appointment_id, "")
        self.assertEqual(
            self.actions.apply_verified_calendar_state(CallResult()).appointment_status,
            "not_requested",
        )

    async def test_unverified_booked_summary_is_downgraded(self):
        result = CallResult(appointment_status="booked", appointment_id="invented")
        result = self.actions.apply_verified_calendar_state(result)
        self.assertEqual(result.appointment_status, "pending")
        self.assertEqual(result.appointment_id, "")

    async def test_action_uses_configured_duration(self):
        result = await self.actions.create_appointment(
            customer_name="Dana",
            phone_raw="0501234567",
            service="inspection",
            start=self.start,
            caller_confirmed=True,
        )
        appointment = result.data["appointment"]
        self.assertEqual(
            datetime.fromisoformat(appointment["end"]) - datetime.fromisoformat(appointment["start"]),
            timedelta(minutes=60),
        )

    async def test_inbound_caller_phone_is_used_for_booking(self):
        actions = BusinessActions(
            self.business,
            MockCalendar(self.business),
            self.actions.leads,
            self.actions.whatsapp,
            caller_phone="+972501234567",
        )
        result = await actions.create_appointment(
            customer_name="Caller",
            phone_raw="",
            service="inspection",
            start=self.start,
            caller_confirmed=True,
        )
        self.assertEqual(result.status, ActionStatus.SUCCEEDED)
        self.assertEqual(
            result.data["appointment"]["phone_normalized"], "+972501234567"
        )

    async def test_booking_enforces_configured_service_area(self):
        configured = business(
            service_area=["תל אביב-יפו"],
            service_area_aliases={"תל אביב-יפו": ["תל אביב", "יפו"]},
        )
        actions = BusinessActions(
            configured,
            MockCalendar(configured),
            self.actions.leads,
            self.actions.whatsapp,
        )
        outside = await actions.create_appointment(
            customer_name="Dana",
            phone_raw="0501234567",
            service="inspection",
            start=self.start,
            address="רבקה גובר 11, כפר סבא",
            caller_confirmed=True,
        )
        self.assertEqual(outside.status, ActionStatus.NEEDS_CLARIFICATION)
        inside = await actions.create_appointment(
            customer_name="Dana",
            phone_raw="0501234567",
            service="inspection",
            start=self.start,
            address="הרצל 10, תל אביב",
            caller_confirmed=True,
        )
        self.assertEqual(inside.status, ActionStatus.SUCCEEDED)

    async def test_lead_persistence_is_replaceable_repository(self):
        lead = CallResult(
            customer_name="Dana",
            call_started_at=self.start,
            call_ended_at=self.start + timedelta(minutes=4),
        )
        saved = self.actions.save_lead(lead)
        self.assertEqual(saved.status, ActionStatus.SUCCEEDED)
        loaded = self.actions.leads.get(lead.call_id)
        self.assertEqual(loaded.customer_name, "Dana")
        self.assertEqual(loaded.call_started_at, self.start)

    async def test_booking_requires_explicit_confirmation(self):
        result = await self.actions.create_appointment(
            customer_name="Dana",
            phone_raw="0501234567",
            service="inspection",
            start=self.start,
        )
        self.assertEqual(result.status, ActionStatus.NEEDS_CLARIFICATION)
        self.assertEqual(len(self.calendar.appointments), 0)

    async def test_whatsapp_mock_records_no_external_call(self):
        backend = MockWhatsApp()
        customer = await backend.send_customer_confirmation(
            phone_normalized="+972501234567", appointment_id="apt-1", text="Confirmed"
        )
        business_result = await backend.send_business_summary(text="New lead")
        self.assertEqual(customer.status, MessageStatus.SUCCEEDED)
        self.assertEqual(business_result.status, MessageStatus.SUCCEEDED)
        self.assertEqual(len(backend.customer_messages), 1)
        self.assertEqual(len(backend.business_messages), 1)

    async def test_pipecat_tool_set_contains_controlled_actions(self):
        from app.actions.tools import build_pipecat_tools

        names = {tool.__name__ for tool in build_pipecat_tools(self.actions)}
        self.assertEqual(names, {
            "check_availability", "create_appointment", "cancel_appointment",
            "get_appointment", "reschedule_appointment", "save_lead", "get_business_info",
        })

    async def test_tool_result_always_requests_spoken_llm_follow_up(self):
        from app.actions.tools import _return_tool_result

        callback = AsyncMock()
        params = SimpleNamespace(result_callback=callback)
        await _return_tool_result(
            params, "check_availability", {"status": "succeeded", "data": {}}
        )
        callback.assert_awaited_once()
        self.assertTrue(callback.call_args.kwargs["properties"].run_llm)

    async def test_demo_factory_loads_only_configured_calendar_windows(self):
        from app.actions.factory import create_business_actions

        settings = Settings().for_demo()
        demo_business = BusinessConfig.load(settings.business_path)
        actions = create_business_actions(
            settings, demo_business, datetime(2026, 9, 14, 8, tzinfo=ZONE)
        )
        self.assertIsInstance(actions.calendar, MockCalendar)
        self.assertEqual(len(actions.calendar.availability_windows), 9)
        self.assertEqual(len(actions.calendar.appointments), 5)


if __name__ == "__main__":
    unittest.main()
