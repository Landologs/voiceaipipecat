import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.errors import ErrorCategory
from pipecat.workers.runner import WorkerRunner

from app.agent.prompt_loader import load_prompt
from app.actions.factory import create_business_actions
from app.actions.tools import build_pipecat_tools
from app.diagnostics.latency import create_latency_observers
from app.knowledge.processor import KnowledgeContextProcessor
from app.leads.models import CallResult
from app.leads.storage import conversation_transcript, save_result
from app.phone.israel_phone import normalize_israeli_phone
from app.summaries.call_summary import summarize
from app.voice.live_display import LiveInputDisplay, LiveResponseDisplay
from app.voice.phone_speech import PhoneSpeechFormatter
from app.voice.recording import CallRecordingWriter
from app.voice.reliable_tts import RetryingOpenAITTSService
from app.voice.short_utterance import ShortUtteranceFinalizer
from app.voice.time_speech import TimeSpeechFormatter

logger = logging.getLogger(__name__)

FINAL_GOODBYE = "תודה שהתקשרת אלינו, להתראות"
INITIAL_GREETING = "היי, זה מיזוג פלוס. מה נשמע?"
GOODBYE_SILENCE_SECONDS = 1.0
USER_IDLE_SECONDS = 6.0
# Give callers time to think after a question.  A new speech turn cancels this
# task, so the retry never talks over an interruption.
EMPTY_TURN_RETRY_SECONDS = 5.0
USER_SPEECH_TIMEOUT_SECONDS = 0.3
USER_TURN_STOP_TIMEOUT_SECONDS = 2.0
TELEPHONY_GREETING_DELAY_SECONDS = 0.5
TELEPHONY_VAD_PARAMS = VADParams(
    confidence=0.5,
    start_secs=0.03,
    stop_secs=0.18,
    min_volume=0.25,
)
STT_CONTEXT_HINT = (
    "Calls may be in Hebrew, Russian, or English and may switch naturally. "
    "Expect Israeli names, addresses, business terms, quantities, and phone numbers spoken in natural digit groups or digit by digit. "
    "Hebrew number forms may include אחד, אחת, שני, שתי, שניים, שתיים, שלוש, שלושה and casual or imperfect grammar. "
    "Transcribe what was spoken; never invent a missing phone digit."
)


def is_final_goodbye(text: str | None, goodbye: str = FINAL_GOODBYE) -> bool:
    """Recognize the exact spoken phrase reserved for a completed conversation."""
    if not text:
        return False
    normalized = re.sub(r"[.!?،,؛;]+", "", " ".join(text.split()))
    expected = re.sub(r"[.!?،,؛;]+", "", goodbye.casefold())
    return normalized.casefold().endswith(expected)


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
    for secret in (
        settings.api_key,
        settings.gemini_api_key,
        settings.elevenlabs_api_key,
    ):
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
    if settings.tts_provider == "elevenlabs":
        from pipecat.services.elevenlabs.dialogue.tts import ElevenLabsDialogueTTSService

        service = ElevenLabsDialogueTTSService(
            api_key=settings.elevenlabs_api_key,
            enable_logging=False,
            settings=ElevenLabsDialogueTTSService.Settings(
                model=settings.tts_model,
                voice=settings.elevenlabs_voice_id,
                language=Language.HE,
                stability=0.5,
            ),
        )
    elif settings.tts_provider == "gemini":
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
        service = RetryingOpenAITTSService(
            api_key=settings.api_key,
            settings=RetryingOpenAITTSService.Settings(
                model=settings.tts_model,
                voice=settings.tts_voice,
                speed=settings.tts_speed,
                instructions=(
                    "Speak naturally, warmly, and briskly. Avoid a robotic cadence. "
                    "Keep every phone-number digit distinct and easy to verify."
                ),
            ),
        )
    service.add_text_transformer(TimeSpeechFormatter())
    service.add_text_transformer(PhoneSpeechFormatter())
    return service


def build_pipeline(
    settings,
    business,
    transport=None,
    *,
    caller_phone: str = "",
    source: str = "local",
):
    """Construct services only; running the worker opens provider connections."""
    settings.validate_voice()
    started = datetime.now(timezone.utc)
    caller_phone = normalize_israeli_phone(caller_phone)
    actions = create_business_actions(
        settings, business, started, caller_phone=caller_phone
    )
    if transport is None:
        transport = LocalAudioTransport(LocalAudioTransportParams(
            audio_in_enabled=True, audio_out_enabled=True,
            input_device_index=settings.input_device, output_device_index=settings.output_device))
    stt = create_stt(settings, business)
    llm = create_llm(settings, load_prompt(
        business,
        started,
        calendar_path=settings.calendar_path,
        caller_phone=caller_phone,
    ))
    tts = create_tts(settings)
    context = LLMContext(tools=build_pipecat_tools(actions))
    vad_params = TELEPHONY_VAD_PARAMS if source == "twilio" else VADParams(stop_secs=0.2)
    user, assistant = LLMContextAggregatorPair(context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=vad_params),
            user_turn_strategies=UserTurnStrategies(stop=[
                SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=USER_SPEECH_TIMEOUT_SECONDS
                )
            ]),
            user_turn_stop_timeout=USER_TURN_STOP_TIMEOUT_SECONDS,
            user_idle_timeout=USER_IDLE_SECONDS,
        ))
    knowledge = KnowledgeContextProcessor()
    audio_buffer = None
    recording_writer = None
    if settings.record_call_audio:
        audio_buffer = AudioBufferProcessor(
            sample_rate=16000,
            num_channels=2,
            auto_start_recording=True,
        )
        recording_writer = CallRecordingWriter(
            settings.results_path / f"{actions.call_id}.wav"
        )

        @audio_buffer.event_handler("on_audio_data")
        async def on_audio_data(buffer, audio, sample_rate, num_channels):
            await recording_writer.save(audio, sample_rate, num_channels)

    processors = [transport.input(), stt]
    if settings.stt_provider == "openai":
        processors.append(ShortUtteranceFinalizer())
    if settings.show_live_transcripts:
        processors.append(LiveInputDisplay())
    processors.extend([user, knowledge, llm])
    if settings.show_live_transcripts:
        processors.append(LiveResponseDisplay())
    processors.extend([tts, transport.output()])
    if audio_buffer:
        processors.append(audio_buffer)
    processors.append(assistant)
    pipeline = Pipeline(processors)
    worker = PipelineWorker(pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000,
                              enable_metrics=True),
        app_resources=actions,
        observers=create_latency_observers(),
        processor_unusable_policy=ProcessorUnusablePolicy.END)
    closer = ConversationCloser(worker.stop_when_done)
    retry_task: asyncio.Task | None = None
    retry_sent = False
    empty_assistant_retry_sent = False
    worker.call_audio_buffer = audio_buffer
    worker.call_recording_writer = recording_writer

    async def repeat_last_question_after(delay: float):
        nonlocal retry_task, retry_sent
        try:
            await asyncio.sleep(delay)
            retry_task = None
            if retry_sent:
                return
            retry_sent = True
            logger.info("No usable caller response; repeating the last question")
            await user.push_frame(LLMMessagesAppendFrame([{
                "role": "developer",
                "content": (
                    "The caller gave no usable response. Briefly repeat your last unanswered "
                    "question in the conversation language; prefer Hebrew if the language is ambiguous. "
                    "Keep every detail already collected."
                ),
            }], run_llm=True))
        except asyncio.CancelledError:
            pass

    def cancel_retry(*, reset: bool = False):
        nonlocal retry_task, retry_sent
        if retry_task and not retry_task.done():
            retry_task.cancel()
        retry_task = None
        if reset:
            retry_sent = False

    def schedule_retry(delay: float):
        nonlocal retry_task
        if retry_sent or (retry_task and not retry_task.done()):
            return
        retry_task = asyncio.create_task(repeat_last_question_after(delay))

    @user.event_handler("on_user_turn_started")
    async def user_turn_started(aggregator, strategy):
        nonlocal empty_assistant_retry_sent
        logger.info("Caller speech detected")
        closer.cancel()
        cancel_retry(reset=True)
        empty_assistant_retry_sent = False

    @user.event_handler("on_user_turn_stopped")
    async def user_turn(aggregator, strategy, message):
        if not settings.show_live_transcripts:
            logger.info("Caller speech turn ended%s", ": " + message.content
                        if settings.log_transcripts and message.content else "")
        if not (message.content or "").strip():
            schedule_retry(EMPTY_TURN_RETRY_SECONDS)

    @user.event_handler("on_user_turn_idle")
    async def user_idle(aggregator):
        # Do not repeat immediately when VAD reports an idle/empty turn.  The
        # caller may simply be thinking or preparing a short answer.
        schedule_retry(EMPTY_TURN_RETRY_SECONDS)

    @assistant.event_handler("on_assistant_turn_started")
    async def assistant_turn_started(aggregator):
        cancel_retry()

    @assistant.event_handler("on_assistant_turn_stopped")
    async def assistant_turn(aggregator, message):
        nonlocal empty_assistant_retry_sent
        logger.info("Agent response completed%s", ": " + message.content
                    if settings.log_transcripts and not settings.show_live_transcripts and message.content else "")
        if not message.interrupted and not (message.content or "").strip():
            if not empty_assistant_retry_sent:
                empty_assistant_retry_sent = True
                logger.warning("LLM completed an empty answer; retrying the latest caller turn once")
                await user.push_frame(LLMMessagesAppendFrame([{
                    "role": "developer",
                    "content": (
                        "Your previous answer contained no speakable text. Answer the caller's "
                        "latest finalized turn now in one short spoken sentence. Keep the current "
                        "conversation state and use Hebrew unless the caller explicitly requested "
                        "Russian or English."
                    ),
                }], run_llm=True))
            else:
                logger.error("LLM returned a second empty answer; waiting for the caller")
            return
        empty_assistant_retry_sent = False
        if not message.interrupted and is_final_goodbye(message.content, business.goodbye("he")):
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
        cancel_retry()

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
    caller_phone: str = "",
):
    logger.info("Providers: STT=%s LLM=%s TTS=%s", settings.stt_provider,
                settings.llm_provider, settings.tts_provider)
    caller_phone = normalize_israeli_phone(caller_phone)
    worker, context, started = build_pipeline(
        settings,
        business,
        transport=transport,
        caller_phone=caller_phone,
        source=source,
    )
    runner = WorkerRunner(handle_sigint=False)
    interrupted = False

    if source == "twilio" and transport is not None:
        _register_twilio_disconnect_handler(transport, runner, session_id)

    try:
        await runner.add_workers(worker)
        if source == "twilio":
            # The Media Stream is connected at this point. A short pause makes
            # the pickup feel natural without adding a noticeable wait.
            await asyncio.sleep(TELEPHONY_GREETING_DELAY_SECONDS)
        # This fixed opening does not need an LLM round trip. TTSSpeakFrame also
        # appends it to conversation context for the following user turn.
        await worker.queue_frames([
            TTSSpeakFrame(business.greeting(), append_to_context=True)
        ])
        await runner.run()
    except asyncio.CancelledError:
        interrupted = True
        logger.info("Conversation cancelled; cleaning up")
    finally:
        await runner.cancel()
        audio_buffer = getattr(worker, "call_audio_buffer", None)
        if audio_buffer:
            await audio_buffer.stop_recording()
        recording_writer = getattr(worker, "call_recording_writer", None)
        recording_path = recording_writer.saved_path if recording_writer else None
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
        if caller_phone:
            result.phone_raw = caller_phone
            result.phone_normalized = caller_phone
        result = worker.app_resources.apply_verified_calendar_state(result)
        path = save_result(
            result,
            settings.results_path,
            summary_status=status,
            transcript=conversation_transcript(context.get_messages()),
            recording_path=recording_path,
        )
        logger.info("%s conversation completed; structured result saved: %s (status=%s)",
                    source.capitalize(), path, status)
    return interrupted


async def run_voice(settings, business):
    return await run_voice_session(settings, business)
