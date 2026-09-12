import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.utils.errors import ErrorCategory
from pipecat.workers.runner import WorkerRunner

from app.agent.prompt_loader import load_prompt
from app.actions.factory import create_business_actions
from app.actions.tools import build_pipecat_tools
from app.diagnostics.latency import create_latency_observers
from app.knowledge.processor import KnowledgeContextProcessor
from app.leads.models import CallResult
from app.leads.storage import save_result
from app.summaries.call_summary import summarize
from app.voice.live_display import LiveInputDisplay, LiveResponseDisplay
from app.voice.phone_speech import PhoneSpeechFormatter

logger = logging.getLogger(__name__)

FINAL_GOODBYES = (
    "תודה שפנית אלינו, להתראות",
    "Спасибо, что обратились к нам. До свидания",
    "Thank you for contacting us. Goodbye",
)
GOODBYE_SILENCE_SECONDS = 4.0
STT_CONTEXT_HINT = (
    "Calls may be in Hebrew, Russian, or English and may switch naturally. "
    "Expect Israeli names, addresses, business terms, quantities, and phone numbers spoken digit by digit. "
    "Hebrew number forms may include אחד, אחת, שני, שתי, שניים, שתיים, שלוש, שלושה and casual or imperfect grammar. "
    "Transcribe what was spoken; never invent a missing phone digit."
)


def is_final_goodbye(text: str | None) -> bool:
    """Recognize the exact spoken phrase reserved for a completed conversation."""
    if not text:
        return False
    normalized = re.sub(r"[.!?،,؛;]+", "", " ".join(text.split()))
    expected = (re.sub(r"[.!?،,؛;]+", "", phrase.casefold()) for phrase in FINAL_GOODBYES)
    return any(normalized.casefold().endswith(phrase) for phrase in expected)


class ConversationCloser:
    """Gracefully end after a final goodbye unless the caller speaks again."""

    def __init__(
        self,
        end_session: Callable[[], Awaitable[None]],
        delay: float = GOODBYE_SILENCE_SECONDS,
    ):
        self._end_session = end_session
        self._delay = delay
        self._task: asyncio.Task | None = None

    def schedule(self):
        self.cancel()
        self._task = asyncio.create_task(self._end_after_silence())

    def cancel(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _end_after_silence(self):
        try:
            await asyncio.sleep(self._delay)
            await self._end_session()
        except asyncio.CancelledError:
            pass


def safe_provider_error(frame, settings) -> str:
    message = str(getattr(frame, "error", "unspecified"))
    for secret in (settings.api_key, settings.gemini_api_key):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", message)[:1000]


def should_end_for_error(category: ErrorCategory) -> bool:
    return category.is_permanent


def create_llm(settings, system_instruction):
    if settings.llm_provider == "gemini":
        from pipecat.services.google.llm import GoogleLLMService

        return GoogleLLMService(
            api_key=settings.gemini_api_key,
            settings=GoogleLLMService.Settings(
                model=settings.llm_model, system_instruction=system_instruction
            ),
        )
    options = {
        "api_key": settings.llm_api_key,
        "settings": OpenAILLMService.Settings(
            model=settings.llm_model, system_instruction=system_instruction
        ),
    }
    if settings.llm_base_url:
        options["base_url"] = settings.llm_base_url
    return OpenAILLMService(**options)


def create_stt(settings, business=None):
    if settings.stt_provider == "gemini":
        from pipecat.services.google.gemini_live.stt import GeminiSTTService

        return GeminiSTTService(
            api_key=settings.gemini_api_key,
            settings=GeminiSTTService.Settings(model=settings.stt_model),
        )
    prompt = STT_CONTEXT_HINT
    if business:
        vocabulary = [business.company_name, business.agent_name, *business.services]
        approved_terms = "; ".join(term for term in vocabulary if term)[:1000]
        if approved_terms:
            prompt += " Known business and service vocabulary: " + approved_terms
    return OpenAIRealtimeSTTService(
        api_key=settings.api_key,
        turn_detection=False,
        settings=OpenAIRealtimeSTTService.Settings(
            model=settings.stt_model, language=None, prompt=prompt
        ),
    )


def create_tts(settings):
    if settings.tts_provider == "gemini":
        from pipecat.services.google.tts import GeminiTTSService

        service = GeminiTTSService(
            api_key=settings.gemini_api_key,
            use_genai=True,
            settings=GeminiTTSService.Settings(
                model=settings.tts_model,
                voice=settings.tts_voice,
                language=Language.HE,
            ),
        )
    else:
        service = OpenAITTSService(
            api_key=settings.api_key,
            settings=OpenAITTSService.Settings(
                model=settings.tts_model, voice=settings.tts_voice
            ),
        )
    service.add_text_transformer(PhoneSpeechFormatter())
    return service


def build_pipeline(settings, business, transport=None):
    """Construct services only; running the worker opens provider connections."""
    settings.validate_voice()
    started = datetime.now(timezone.utc)
    actions = create_business_actions(settings, business, started)
    if transport is None:
        transport = LocalAudioTransport(LocalAudioTransportParams(
            audio_in_enabled=True, audio_out_enabled=True,
            input_device_index=settings.input_device, output_device_index=settings.output_device))
    stt = create_stt(settings, business)
    llm = create_llm(settings, load_prompt(business, started, calendar_path=settings.calendar_path))
    tts = create_tts(settings)
    context = LLMContext(tools=build_pipecat_tools(actions))
    user, assistant = LLMContextAggregatorPair(context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))
    knowledge = KnowledgeContextProcessor()

    processors = [transport.input(), stt]
    if settings.show_live_transcripts:
        processors.append(LiveInputDisplay())
    processors.extend([user, knowledge, llm])
    if settings.show_live_transcripts:
        processors.append(LiveResponseDisplay())
    processors.extend([tts, transport.output(), assistant])
    pipeline = Pipeline(processors)
    worker = PipelineWorker(pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000,
                              enable_metrics=True),
        app_resources=actions,
        observers=create_latency_observers(),
        processor_unusable_policy=ProcessorUnusablePolicy.END)
    closer = ConversationCloser(worker.stop_when_done)

    @user.event_handler("on_user_turn_started")
    async def user_turn_started(aggregator, strategy):
        closer.cancel()

    @user.event_handler("on_user_turn_stopped")
    async def user_turn(aggregator, strategy, message):
        logger.info("Caller speech turn ended%s", ": " + message.content
                    if settings.log_transcripts and not settings.show_live_transcripts and message.content else "")

    @assistant.event_handler("on_assistant_turn_stopped")
    async def assistant_turn(aggregator, message):
        logger.info("Agent response completed%s", ": " + message.content
                    if settings.log_transcripts and not settings.show_live_transcripts and message.content else "")
        if not message.interrupted and is_final_goodbye(message.content):
            logger.info("Final goodbye completed; ending after %.0f seconds of silence",
                        GOODBYE_SILENCE_SECONDS)
            closer.schedule()

    @worker.event_handler("on_pipeline_started")
    async def on_started(worker, frame):
        logger.info("Voice pipeline started; Ctrl+C ends the session")

    @worker.event_handler("on_pipeline_error")
    async def on_error(worker, frame):
        # Provider error bodies can contain request content; keep console output minimal.
        logger.error("Pipeline error in %s (category=%s, exception=%s): %s",
                     type(frame.processor).__name__, frame.category,
                     type(frame.exception).__name__ if frame.exception else "unspecified",
                     safe_provider_error(frame, settings))
        if should_end_for_error(frame.category):
            logger.error("Provider configuration cannot recover; ending session")
            await worker.cancel()
        else:
            logger.warning("Transient provider error; keep the session open and retry after its delay")

    @worker.event_handler("on_pipeline_finished")
    async def on_finished(worker, frame):
        closer.cancel()

    return worker, context, started


def _register_twilio_disconnect_handler(transport, runner, session_id: str):
    """Stop the call worker when Twilio closes the caller-side WebSocket."""

    @transport.event_handler("on_client_disconnected")
    async def on_caller_disconnected(transport, client):
        logger.info("Caller hangup: CallSid=%s", session_id or "unknown")
        await runner.cancel()


async def run_voice_session(
    settings,
    business,
    transport=None,
    *,
    source="local",
    session_id: str = "",
):
    logger.info("Providers: STT=%s LLM=%s TTS=%s", settings.stt_provider,
                settings.llm_provider, settings.tts_provider)
    worker, context, started = build_pipeline(settings, business, transport=transport)
    runner = WorkerRunner(handle_sigint=False)
    interrupted = False

    if source == "twilio" and transport is not None:
        _register_twilio_disconnect_handler(transport, runner, session_id)

    try:
        await runner.add_workers(worker)
        context.add_message({"role": "developer", "content": "הציגי את עצמך ושאלי איך אפשר לעזור."})
        await worker.queue_frames([LLMRunFrame()])
        await runner.run()
    except asyncio.CancelledError:
        interrupted = True
        logger.info("Conversation cancelled; cleaning up")
    finally:
        await runner.cancel()
        result = CallResult(after_hours=not business.is_open(started))
        status = "complete"
        try:
            result = await summarize(context.get_messages(), settings, after_hours=result.after_hours)
        except Exception as exc:
            status = "failed"
            logger.error("Summary failed (%s); saving an incomplete result", type(exc).__name__)
            result.notes = "Summary unavailable; caller details were not extracted."
        result.call_started_at = started
        result.call_ended_at = datetime.now(timezone.utc)
        result = worker.app_resources.apply_verified_calendar_state(result)
        path = save_result(result, settings.results_path, summary_status=status)
        logger.info("%s conversation completed; structured result saved: %s (status=%s)",
                    source.capitalize(), path, status)
    return interrupted


async def run_voice(settings, business):
    return await run_voice_session(settings, business)
