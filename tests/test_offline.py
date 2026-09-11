import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from app.config.business_config import BusinessConfig
from app.config.settings import Settings
from app.leads.models import CallResult
from app.leads.storage import save_result
from app.phone.israel_phone import normalize_israeli_phone


class PhoneTests(unittest.TestCase):
    def test_supported_formats(self):
        for value in ("050-1234567", "+972501234567", "(050) 123 4567"):
            self.assertEqual(normalize_israeli_phone(value), "+972501234567")
        self.assertEqual(normalize_israeli_phone("02-1234567"), "+97221234567")

    def test_uncertain_inputs(self):
        for value in ("050123", "+9720501234567", "05012345678", "אפס חמש", "0501234567?", "++972501234567"):
            self.assertEqual(normalize_israeli_phone(value), "")


class HoursTests(unittest.TestCase):
    def test_boundaries_and_saturday(self):
        business = BusinessConfig(company_name="Test", agent_name="Test",
                                  business_hours={"5": [("09:00", "17:00")]})
        zone = ZoneInfo("Asia/Jerusalem")
        self.assertTrue(business.is_open(datetime(2026, 9, 5, 9, tzinfo=zone)))
        self.assertFalse(business.is_open(datetime(2026, 9, 5, 17, tzinfo=zone)))
        with self.assertRaises(ValueError):
            business.is_open(datetime(2026, 9, 5, 12))

    def test_overnight(self):
        business = BusinessConfig(company_name="Test", agent_name="Test",
                                  business_hours={"5": [("22:00", "02:00")]})
        self.assertTrue(business.is_open(datetime(2026, 9, 6, 1, tzinfo=ZoneInfo("Asia/Jerusalem"))))

    def test_dst_conversion(self):
        business = BusinessConfig(company_name="Test", agent_name="Test",
                                  business_hours={"0": [("09:00", "10:00")]})
        self.assertTrue(business.is_open(datetime(2026, 1, 5, 7, tzinfo=ZoneInfo("UTC"))))
        self.assertTrue(business.is_open(datetime(2026, 7, 6, 6, tzinfo=ZoneInfo("UTC"))))
        self.assertFalse(business.is_open(datetime(2026, 7, 6, 7, tzinfo=ZoneInfo("UTC"))))


class ResultTests(unittest.TestCase):
    def test_demo_mode_isolated_to_demo_directory(self):
        from app.agent.prompt_loader import load_prompt
        from app.config.settings import DEMO_ROOT

        settings = Settings(business_path=Path("outside-demo.json")).for_demo()
        self.assertTrue(settings.demo_mode)
        self.assertEqual(settings.business_path, DEMO_ROOT / "business.json")
        self.assertEqual(settings.calendar_path, DEMO_ROOT / "calendar.json")
        self.assertEqual(settings.results_path, DEMO_ROOT / "results")

        business = BusinessConfig.load(settings.business_path)
        prompt = load_prompt(
            business,
            datetime(2026, 9, 10, 9, tzinfo=ZoneInfo("Asia/Jerusalem")),
            calendar_path=settings.calendar_path,
        )
        self.assertIn("מצב הדגמה סגור", prompt)
        self.assertIn("מיזוג פלוס", prompt)
        self.assertIn("חלונות הדגמה פנויים בלבד", prompt)

    def test_booking_and_phone_guardrails(self):
        with self.assertRaises(ValidationError):
            CallResult(appointment_status="booked")
        result = CallResult(phone_raw="050123", phone_normalized="invented", phone_confirmed=True)
        self.assertEqual(result.phone_normalized, "")
        self.assertFalse(result.phone_confirmed)

    def test_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            result = CallResult(customer_name="נועה")
            path = save_result(result, Path(directory), summary_status="complete")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["result"]["customer_name"], "נועה")
            self.assertIsNotNone(datetime.fromisoformat(saved["saved_at"]).tzinfo)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_no_model_defaults_or_key_in_repr(self):
        settings = Settings(api_key="private-test-value", gemini_api_key="gemini-private-value")
        self.assertNotIn("private-test-value", repr(settings))
        self.assertNotIn("gemini-private-value", repr(settings))
        with self.assertRaisesRegex(ValueError, "STT_MODEL"):
            settings.validate_voice()

    def test_gemini_key_is_required_only_for_gemini_llm(self):
        common = dict(api_key="openai-placeholder", stt_model="stt", llm_model="llm",
                      tts_model="tts", tts_voice="voice")
        Settings(**common).validate_voice()
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            Settings(**common, llm_provider="gemini").validate_voice()
        Settings(**common, llm_provider="gemini",
                 gemini_api_key="gemini-placeholder").validate_voice()

    def test_gemini_only_env_selects_full_gemini_test_stack(self):
        from app.config.settings import (
            GEMINI_TEST_LLM_MODEL,
            GEMINI_TEST_STT_MODEL,
            GEMINI_TEST_TTS_MODEL,
            GEMINI_TEST_TTS_VOICE,
        )
        environment = {
            "OPENAI_API_KEY": "",
            "GEMINI_API_KEY": "gemini-placeholder",
            "STT_PROVIDER": "",
            "LLM_PROVIDER": "",
            "TTS_PROVIDER": "",
            "STT_MODEL": "",
            "LLM_MODEL": "",
            "TTS_MODEL": "",
            "TTS_VOICE": "",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "app.config.settings.load_dotenv"
        ):
            settings = Settings.load()
        settings.validate_voice()
        self.assertEqual(
            (
                settings.stt_provider,
                settings.llm_provider,
                settings.tts_provider,
                settings.stt_model,
                settings.llm_model,
                settings.tts_model,
                settings.tts_voice,
            ),
            (
                "gemini",
                "gemini",
                "gemini",
                GEMINI_TEST_STT_MODEL,
                GEMINI_TEST_LLM_MODEL,
                GEMINI_TEST_TTS_MODEL,
                GEMINI_TEST_TTS_VOICE,
            ),
        )


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_chat_uses_prompt_and_configured_model(self):
        from app.chat.console import run_text
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="שלום"))]))
        settings = Settings(api_key="offline-placeholder", llm_model="offline-llm")
        business = BusinessConfig(company_name="Test", agent_name="Test")
        with patch("app.chat.console.AsyncOpenAI", return_value=client):
            status = await run_text(settings, business, "היי")
        self.assertEqual(status, 0)
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "offline-llm")
        self.assertIn("Test", request["messages"][0]["content"])
        self.assertEqual(request["messages"][1], {"role": "user", "content": "היי"})

    async def test_text_validation_needs_only_llm_settings(self):
        settings = Settings(api_key="offline-placeholder", llm_model="offline-llm")
        settings.validate_text()
        with self.assertRaisesRegex(ValueError, "LLM_MODEL"):
            Settings(api_key="offline-placeholder").validate_text()

    async def test_rate_limit_does_not_end_session(self):
        from pipecat.utils.errors import ErrorCategory
        from app.voice.pipeline import should_end_for_error
        self.assertFalse(should_end_for_error(ErrorCategory.RATE_LIMIT))
        self.assertFalse(should_end_for_error(ErrorCategory.SERVER))
        self.assertTrue(should_end_for_error(ErrorCategory.AUTHENTICATION))
        self.assertTrue(should_end_for_error(ErrorCategory.INVALID_REQUEST))

    async def test_summary_validation_without_api(self):
        from app.summaries.call_summary import summarize
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "phone_raw": "0521234567", "appointment_status": "pending"})))]))
        transcript = [{"role": "user", "content": "0501234567"},
                      {"role": "user", "content": "Correction: 0521234567"}]
        with patch("app.summaries.call_summary.AsyncOpenAI", return_value=client):
            result = await summarize(transcript, Settings(llm_model="offline"), after_hours=True)
        self.assertEqual(result.phone_normalized, "+972521234567")
        self.assertTrue(result.after_hours)
        sent = client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
        self.assertIn("Correction: 0521234567", sent)

    async def test_gemini_summary_uses_compatibility_endpoint(self):
        from app.config.settings import GEMINI_OPENAI_BASE_URL
        from app.summaries.call_summary import summarize
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]))
        settings = Settings(llm_provider="gemini", gemini_api_key="offline-placeholder",
                            llm_model="offline-gemini")
        with patch("app.summaries.call_summary.AsyncOpenAI", return_value=client) as constructor:
            await summarize([{"role": "user", "content": "שלום"}], settings,
                            after_hours=False)
        self.assertEqual(constructor.call_args.kwargs["base_url"], GEMINI_OPENAI_BASE_URL)
        self.assertEqual(constructor.call_args.kwargs["api_key"], "offline-placeholder")

    async def test_gemini_pipeline_llm_uses_native_pipecat_service(self):
        from app.voice.pipeline import create_llm
        settings = Settings(llm_provider="gemini", gemini_api_key="offline-placeholder",
                            llm_model="offline-gemini")
        with patch("pipecat.services.google.llm.GoogleLLMService") as service:
            service.Settings.return_value = "settings"
            create_llm(settings, "prompt")
        self.assertEqual(service.call_args.kwargs["api_key"], "offline-placeholder")

    async def test_full_gemini_pipeline_construction_without_network(self):
        from loguru import logger
        logger.remove()
        from app.voice.pipeline import build_pipeline
        settings = Settings(
            gemini_api_key="offline-placeholder",
            stt_provider="gemini",
            llm_provider="gemini",
            tts_provider="gemini",
            stt_model="gemini-3.5-transcribe-live",
            llm_model="gemini-3.6-flash",
            tts_model="gemini-3.1-flash-tts-preview",
            tts_voice="Kore",
        )
        business = BusinessConfig(company_name="Test", agent_name="Test")
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            worker, context, started = build_pipeline(settings, business)
        self.assertIsNotNone(worker)
        self.assertEqual(context.get_messages(), [])
        self.assertIsNotNone(started.tzinfo)

    async def test_demo_voice_pipeline_construction_without_network(self):
        from app.voice.pipeline import build_pipeline

        settings = Settings(
            api_key="offline-placeholder",
            stt_model="gpt-live-transcribe",
            llm_model="gpt-5-nano",
            tts_model="gpt-4o-mini-tts",
            tts_voice="alloy",
        ).for_demo()
        business = BusinessConfig.load(settings.business_path)
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            worker, context, started = build_pipeline(settings, business)
        self.assertIsNotNone(worker)
        self.assertEqual(context.get_messages(), [])
        self.assertIsNotNone(started.tzinfo)

    async def test_empty_summary_does_not_create_client(self):
        from app.summaries.call_summary import summarize
        with patch("app.summaries.call_summary.AsyncOpenAI") as client:
            result = await summarize([], Settings(), after_hours=False)
        client.assert_not_called()
        self.assertEqual(result, CallResult())

    async def test_worker_lifecycle_without_providers(self):
        from pipecat.frames.frames import EndFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineWorker
        from pipecat.processors.frame_processor import FrameProcessor
        from pipecat.workers.runner import WorkerRunner
        class PassThrough(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                await self.push_frame(frame, direction)
        worker = PipelineWorker(Pipeline([PassThrough()]))
        completed = asyncio.Event()
        @worker.event_handler("on_pipeline_finished")
        async def finished(worker, frame):
            completed.set()
        runner = WorkerRunner(handle_sigint=False)
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            await runner.add_workers(worker)
            await worker.queue_frames([EndFrame()])
            await asyncio.wait_for(runner.run(), timeout=10)
        self.assertTrue(completed.is_set())

    async def test_construction_without_network(self):
        from loguru import logger
        logger.remove()
        # Sentinel names are test data, not selected models. Never start this worker.
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            from app.voice.pipeline import build_pipeline
            settings = Settings(api_key="offline-placeholder", stt_model="offline-stt",
                                llm_model="offline-llm", tts_model="offline-tts", tts_voice="offline-voice")
            business = BusinessConfig(company_name="Test", agent_name="Test")
            worker, context, started = build_pipeline(settings, business)
            self.assertIsNotNone(worker)
            self.assertEqual(context.get_messages(), [])
            self.assertIsNotNone(started.tzinfo)

    async def test_vad_barge_in_trigger(self):
        from pipecat.frames.frames import VADUserStartedSpeakingFrame
        from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
        strategy = VADUserTurnStartStrategy()
        seen = []
        @strategy.event_handler("on_user_turn_started")
        async def started(sender, params):
            seen.append(params.enable_interruptions)
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
