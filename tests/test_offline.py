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

    def test_phone_tts_representations_keep_storage_and_speech_separate(self):
        from app.voice.phone_number_tts_test import (
            group_israeli_mobile_number,
            representations,
            spoken_hebrew_phone_number,
        )

        canonical = "0588080808"
        self.assertEqual(group_israeli_mobile_number(canonical), "058-808-0808")
        self.assertEqual(
            spoken_hebrew_phone_number(canonical),
            "אפס חמש שמונה, שמונה אפס שמונה, אפס שמונה אפס שמונה",
        )
        numeric, spoken = representations(canonical)
        self.assertEqual(numeric.tts_input, "058-808-0808")
        self.assertNotIn("058", spoken.tts_input)

    def test_phone_tts_plan_has_sixty_requests(self):
        from app.voice.phone_number_tts_test import estimate_plan

        plan = estimate_plan(Settings(tts_provider="openai", tts_model="gpt-4o-mini-tts"))
        self.assertEqual(plan["requests"], 60)
        self.assertGreater(plan["estimated_audio_seconds"], 0)
        self.assertIsNotNone(plan["estimated_cost_usd"])

    def test_production_phone_speech_is_language_specific(self):
        from app.voice.phone_speech import spoken_phone_number

        self.assertEqual(
            spoken_phone_number("+972586006600", "he"),
            "אפס חמש שמונה, שש אפס אפס, שש שש אפס אפס",
        )
        english = spoken_phone_number("058-600-6600", "en")
        self.assertEqual(english, "zero five eight, six zero zero, six six zero zero")
        self.assertNotIn("hundred", english)
        self.assertNotIn(" oh ", f" {english} ")
        self.assertEqual(
            spoken_phone_number("0586006600", "ru"),
            "ноль пять восемь, шесть ноль ноль, шесть шесть ноль ноль",
        )

    def test_only_valid_israeli_phone_numbers_are_changed_for_tts(self):
        from app.voice.phone_speech import format_phone_numbers_for_speech

        text = "המספר הוא 058-808-0808 והמחיר הוא 1200"
        spoken = format_phone_numbers_for_speech(text)
        self.assertIn("אפס חמש שמונה, שמונה אפס שמונה, אפס שמונה אפס שמונה", spoken)
        self.assertIn("1200", spoken)
        self.assertEqual(format_phone_numbers_for_speech("השעה 10:30"), "השעה 10:30")

    def test_phone_speech_does_not_mutate_canonical_storage(self):
        from app.voice.phone_speech import format_phone_numbers_for_speech

        canonical = "0521234567"
        format_phone_numbers_for_speech(f"Phone: {canonical}", language="en")
        self.assertEqual(canonical, "0521234567")

    def test_landline_phone_uses_israeli_grouping(self):
        from app.voice.phone_speech import group_israeli_phone_number, spoken_phone_number

        self.assertEqual(group_israeli_phone_number("02-1234567"), "02-123-4567")
        self.assertEqual(
            spoken_phone_number("+97221234567", "en"),
            "zero two, one two three, four five six seven",
        )


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


class KnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.knowledge.retrieval import KnowledgeStore

        cls.store = KnowledgeStore()

    def test_dataset_sizes_are_deliberately_bounded(self):
        counts = self.store.counts()
        self.assertGreaterEqual(counts["hebrew/everyday_slang.jsonl"], 50)
        self.assertLessEqual(counts["hebrew/everyday_slang.jsonl"], 100)
        hebrew_plumbing = (
            counts["hebrew/plumbing_terms.jsonl"]
            + counts["hebrew/plumbing_colloquial.jsonl"]
        )
        self.assertGreaterEqual(hebrew_plumbing, 75)
        self.assertLessEqual(hebrew_plumbing, 150)
        self.assertGreaterEqual(counts["hebrew/normalization.jsonl"], 25)

    def test_hebrew_slang_lookup(self):
        matches = self.store.search("סבבה, תודה רבה")
        self.assertEqual(matches[0].entry.term, "סבבה")
        self.assertEqual(matches[0].entry.category, "everyday")

    def test_hebrew_plumbing_lookup_is_limited(self):
        matches = self.store.search("יש לי סתימה מטורפת בכיור והמים לא יורדים")
        concepts = {match.entry.concept for match in matches}
        self.assertIn("blocked_drain", concepts)
        self.assertTrue(any(match.entry.term == "כיור" for match in matches))
        self.assertLessEqual(len(matches), 5)

    def test_normalization_and_alias_lookup(self):
        matches = self.store.search("הניגרה לא מפסיקה למלא מים")
        self.assertTrue(any(match.entry.concept == "running_toilet" for match in matches))

    def test_only_explicitly_safe_slang_can_be_mirrored(self):
        from app.knowledge.adaptation import ConversationProfile

        profile = ConversationProfile()
        for _ in range(2):
            profile.update("סבבה, זה מתאים לי", self.store.search("סבבה, זה מתאים לי"))
        self.assertEqual(profile.preferred_response_register, "casual")
        self.assertIn("סבבה", profile.safe_slang_seen)

    def test_profanity_is_understood_but_never_mirrored(self):
        from app.knowledge.adaptation import ConversationProfile

        matches = self.store.search("כוסעמק, הכול מוצף")
        profanity = next(match.entry for match in matches if match.entry.term == "כוס אמק")
        self.assertFalse(profanity.agent_can_use)
        self.assertFalse(profanity.safe_to_mirror)
        profile = ConversationProfile()
        profile.update("כוסעמק, הכול מוצף", matches)
        self.assertNotIn("כוס אמק", profile.safe_slang_seen)

    def test_explicit_hebrew_to_russian_switch(self):
        from app.knowledge.adaptation import ConversationProfile

        profile = ConversationProfile()
        profile.update("שלום, יש לי בעיה בכיור", self.store.search("שלום, יש לי בעיה בכיור"))
        profile.update("Можно по-русски?", [])
        self.assertEqual(profile.current_language, "ru")

    def test_substantive_russian_switch_and_clear_hebrew_return(self):
        from app.knowledge.adaptation import ConversationProfile

        profile = ConversationProfile()
        profile.update("Да", [])
        self.assertEqual(profile.current_language, "he")
        profile.update("У меня полностью забилась раковина", self.store.search(
            "У меня полностью забилась раковина"
        ))
        self.assertEqual(profile.current_language, "ru")
        profile.update("עכשיו אני רוצה להמשיך בעברית", [])
        self.assertEqual(profile.current_language, "he")

    def test_substantive_hebrew_to_english_switch(self):
        from app.knowledge.adaptation import ConversationProfile

        profile = ConversationProfile()
        profile.update("The kitchen sink is completely blocked", self.store.search(
            "The kitchen sink is completely blocked"
        ))
        self.assertEqual(profile.current_language, "en")

    def test_mixed_hebrew_product_words_do_not_switch_language(self):
        from app.knowledge.adaptation import ConversationProfile

        profile = ConversationProfile()
        profile.update("יש בעיה עם ה-Wi-Fi וה-WhatsApp", [])
        self.assertEqual(profile.current_language, "he")

    def test_history_is_preserved_when_temporary_language_context_is_added(self):
        from app.knowledge.adaptation import TurnContextEngine

        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "קוראים לי דני והכתובת היא הרצל 10"},
            {"role": "assistant", "content": "תודה דני"},
            {"role": "user", "content": "Можно по-русски?"},
        ]
        augmented = TurnContextEngine(self.store).augment_messages(messages, messages[-1]["content"])
        self.assertEqual(messages[-1]["content"], "Можно по-русски?")
        self.assertIn("הרצל 10", augmented[1]["content"])
        self.assertIn("Reply language: Russian", augmented[-1]["content"])

    def test_unknown_part_request_adds_clarification_guidance(self):
        from app.knowledge.adaptation import TurnContextEngine

        context = TurnContextEngine(self.store).context_for(
            "אני לא יודע איך קוראים לזה, משהו נשבר מתחת לכיור"
        )
        self.assertIn("Ask a short natural clarification", context)
        self.assertIn("not diagnoses", context)

    def test_irrelevant_turn_injects_nothing(self):
        from app.knowledge.adaptation import TurnContextEngine

        context = TurnContextEngine(self.store).context_for("שלום, רציתי לשאול שאלה כללית")
        self.assertEqual(context, "")


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
        self.assertIn("גבולות מידע פנימיים", prompt)
        self.assertIn("מיזוג פלוס", prompt)
        self.assertIn("check_availability", prompt)
        self.assertNotIn("מיזוג פלוס — הדגמה", prompt)

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
            report = path.with_suffix(".txt").read_text(encoding="utf-8")
            self.assertIn("Результат звонка", report)
            self.assertIn("Имя клиента: נועה", report)
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_no_model_defaults_or_key_in_repr(self):
        settings = Settings(
            api_key="private-test-value",
            gemini_api_key="gemini-private-value",
            twilio_auth_token="twilio-private-value",
            google_calendar_credentials_file="google-private-value",
            whatsapp_access_token="whatsapp-private-value",
        )
        self.assertNotIn("private-test-value", repr(settings))
        self.assertNotIn("gemini-private-value", repr(settings))
        self.assertNotIn("twilio-private-value", repr(settings))
        self.assertNotIn("google-private-value", repr(settings))
        self.assertNotIn("whatsapp-private-value", repr(settings))
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
    async def test_final_goodbye_detection(self):
        from app.voice.pipeline import is_final_goodbye

        self.assertTrue(is_final_goodbye("תודה שפנית אלינו, להתראות."))
        self.assertTrue(is_final_goodbye("Спасибо, что обратились к нам. До свидания."))
        self.assertTrue(is_final_goodbye("Thank you for contacting us. Goodbye."))
        self.assertFalse(is_final_goodbye("תודה, האם דרוש עוד משהו?"))

    async def test_openai_realtime_stt_is_not_pinned_to_hebrew(self):
        from app.voice.pipeline import STT_CONTEXT_HINT, create_stt

        settings = Settings(api_key="offline-placeholder", stt_model="gpt-live-transcribe")
        stt = create_stt(settings)
        self.assertIsNone(stt._settings.language)
        self.assertEqual(stt._settings.prompt, STT_CONTEXT_HINT)

    async def test_stt_hint_uses_configured_business_vocabulary(self):
        from app.voice.pipeline import create_stt

        business = BusinessConfig(
            company_name="מיזוג פלוס",
            agent_name="נועה",
            services=["ניקוי מזגן עילי"],
        )
        stt = create_stt(
            Settings(api_key="offline-placeholder", stt_model="gpt-live-transcribe"),
            business,
        )
        self.assertIn("מיזוג פלוס", stt._settings.prompt)
        self.assertIn("ניקוי מזגן עילי", stt._settings.prompt)

    async def test_phone_tts_formatter_remembers_language_for_number_only_chunk(self):
        from app.voice.phone_speech import PhoneSpeechFormatter

        formatter = PhoneSpeechFormatter()
        await formatter("Let me confirm your phone number.", "sentence")
        spoken = await formatter("058-600-6600", "sentence")
        self.assertEqual(spoken, "zero five eight, six zero zero, six six zero zero")

    async def test_standalone_tts_can_set_its_output_rate_without_a_pipeline(self):
        from app.voice.phone_number_tts_test import create_standalone_tts

        settings = Settings(
            api_key="offline-placeholder",
            tts_model="gpt-4o-mini-tts",
            tts_voice="alloy",
        )
        tts = create_standalone_tts(settings)
        self.assertEqual(tts.sample_rate, 24000)

    async def test_conversation_close_can_be_cancelled_during_grace_period(self):
        from app.voice.pipeline import ConversationCloser

        end_session = AsyncMock()
        closer = ConversationCloser(end_session, delay=0.01)
        closer.schedule()
        closer.cancel()
        await asyncio.sleep(0.02)
        end_session.assert_not_awaited()

    async def test_conversation_closes_after_grace_period(self):
        from app.voice.pipeline import ConversationCloser

        end_session = AsyncMock()
        closer = ConversationCloser(end_session, delay=0)
        closer.schedule()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        end_session.assert_awaited_once()

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
        self.assertEqual(
            {tool.name for tool in context.tools.standard_tools},
            {
                "check_availability", "create_appointment", "get_appointment",
                "cancel_appointment", "reschedule_appointment", "save_lead",
                "get_business_info",
            },
        )
        observer_types = {type(item).__name__ for item in worker._observer._observers}
        self.assertIn("UserBotLatencyObserver", observer_types)
        self.assertIn("ServiceMetricsObserver", observer_types)
        create_schema = next(
            tool for tool in context.tools.standard_tools if tool.name == "create_appointment"
        )
        self.assertIn("caller_confirmed", create_schema.required)

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


class TelephonyTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            api_key="offline-placeholder",
            stt_model="offline-stt",
            llm_model="offline-llm",
            tts_model="offline-tts",
            tts_voice="offline-voice",
            twilio_account_sid="AC00000000000000000000000000000000",
            twilio_auth_token="offline-twilio-token",
            twilio_public_url="https://example.ngrok.app",
        )
        self.business = BusinessConfig(company_name="Test", agent_name="Test")

    def test_telephony_settings_require_twilio_credentials(self):
        with self.assertRaisesRegex(ValueError, "TWILIO_ACCOUNT_SID"):
            Settings(
                api_key="key", stt_model="stt", llm_model="llm",
                tts_model="tts", tts_voice="voice",
            ).validate_telephony()
        self.settings.validate_telephony()
        with self.assertRaisesRegex(ValueError, "TWILIO_PUBLIC_URL"):
            Settings(
                api_key="key", stt_model="stt", llm_model="llm",
                tts_model="tts", tts_voice="voice",
                twilio_account_sid="AC123", twilio_auth_token="token",
                twilio_public_url="https://user:password@example.com/path?unsafe=yes",
            ).validate_telephony()

    def test_twiml_uses_wss_and_custom_parameters(self):
        from app.telephony.twilio_server import _valid_signature, build_twiml, websocket_url
        from twilio.request_validator import RequestValidator

        stream_url = websocket_url("https://example.ngrok.app")
        xml = build_twiml(stream_url, "+972501234567", "+972599999999")
        self.assertIn('url="wss://example.ngrok.app/twilio/media"', xml)
        self.assertIn('name="from_number" value="+972501234567"', xml)
        self.assertNotIn("?", stream_url)
        signature = RequestValidator(self.settings.twilio_auth_token).compute_signature(
            stream_url, {}
        )
        self.assertTrue(_valid_signature(
            self.settings.twilio_auth_token, stream_url, {}, signature
        ))

    def test_signed_voice_webhook_returns_twiml(self):
        from fastapi.testclient import TestClient
        from twilio.request_validator import RequestValidator
        from app.telephony.twilio_server import create_app

        url = "https://example.ngrok.app/twilio/voice"
        form = {"CallSid": "CA123", "From": "+972501234567", "To": "+972599999999"}
        signature = RequestValidator(self.settings.twilio_auth_token).compute_signature(url, form)
        client = TestClient(create_app(self.settings, self.business))
        with self.assertLogs("app.telephony.twilio_server", level="INFO") as logs:
            response = client.post(
                "/twilio/voice", data=form, headers={"X-Twilio-Signature": signature}
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("wss://example.ngrok.app/twilio/media", response.text)
        self.assertTrue(any("Incoming call received: CallSid=CA123" in line for line in logs.output))

    def test_unsigned_voice_webhook_is_rejected(self):
        from fastapi.testclient import TestClient
        from app.telephony.twilio_server import create_app

        client = TestClient(create_app(self.settings, self.business))
        response = client.post("/twilio/voice", data={"CallSid": "CA123"})
        self.assertEqual(response.status_code, 403)

    def test_signed_media_websocket_starts_twilio_transport(self):
        from fastapi.testclient import TestClient
        from twilio.request_validator import RequestValidator
        from app.telephony.twilio_server import create_app

        url = "wss://example.ngrok.app/twilio/media"
        signature = RequestValidator(self.settings.twilio_auth_token).compute_signature(url, {})
        call_data = SimpleNamespace(stream_id="MZ123", call_id="CA123")
        run_session = AsyncMock(return_value=False)
        with (
            patch(
                "app.telephony.twilio_server.parse_telephony_websocket",
                new=AsyncMock(return_value=("twilio", call_data)),
            ),
            patch("app.telephony.twilio_server.run_voice_session", new=run_session),
            self.assertLogs("app.telephony.twilio_server", level="INFO") as logs,
        ):
            client = TestClient(create_app(self.settings, self.business))
            with client.websocket_connect(
                "/twilio/media", headers={"X-Twilio-Signature": signature}
            ):
                pass
        run_session.assert_awaited_once()
        self.assertTrue(any("media stream connected: CallSid=CA123" in line for line in logs.output))
        self.assertTrue(any("media stream disconnected: CallSid=CA123" in line for line in logs.output))

    def test_unsigned_media_websocket_is_rejected(self):
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        from app.telephony.twilio_server import create_app

        client = TestClient(create_app(self.settings, self.business))
        with self.assertRaises(WebSocketDisconnect) as error:
            with client.websocket_connect("/twilio/media"):
                pass
        self.assertEqual(error.exception.code, 1008)

    def test_caller_disconnect_cancels_worker(self):
        from app.voice.pipeline import _register_twilio_disconnect_handler

        handlers = {}

        class FakeTransport:
            def event_handler(self, name):
                def register(callback):
                    handlers[name] = callback
                    return callback
                return register

        runner = SimpleNamespace(cancel=AsyncMock())
        _register_twilio_disconnect_handler(FakeTransport(), runner, "CA123")
        with self.assertLogs("app.voice.pipeline", level="INFO") as logs:
            asyncio.run(handlers["on_client_disconnected"](None, None))
        runner.cancel.assert_awaited_once()
        self.assertTrue(any("Caller hangup: CallSid=CA123" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
