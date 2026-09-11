import asyncio
import logging
import re
from datetime import datetime, timezone

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
from app.leads.models import CallResult
from app.leads.storage import save_result
from app.summaries.call_summary import summarize
from app.voice.live_display import LiveInputDisplay, LiveResponseDisplay

logger = logging.getLogger(__name__)


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


def create_stt(settings):
    if settings.stt_provider == "gemini":
        from pipecat.services.google.gemini_live.stt import GeminiSTTService

        return GeminiSTTService(
            api_key=settings.gemini_api_key,
            settings=GeminiSTTService.Settings(model=settings.stt_model),
        )
    return OpenAIRealtimeSTTService(
        api_key=settings.api_key,
        turn_detection=False,
        settings=OpenAIRealtimeSTTService.Settings(
            model=settings.stt_model, language=Language.HE
        ),
    )


def create_tts(settings):
    if settings.tts_provider == "gemini":
        from pipecat.services.google.tts import GeminiTTSService

        return GeminiTTSService(
            api_key=settings.gemini_api_key,
            use_genai=True,
            settings=GeminiTTSService.Settings(
                model=settings.tts_model,
                voice=settings.tts_voice,
                language=Language.HE,
            ),
        )
    return OpenAITTSService(
        api_key=settings.api_key,
        settings=OpenAITTSService.Settings(
            model=settings.tts_model, voice=settings.tts_voice
        ),
    )


def build_pipeline(settings, business):
    """Construct services only; running the worker opens provider connections."""
    settings.validate_voice()
    started = datetime.now(timezone.utc)
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=True, audio_out_enabled=True,
        input_device_index=settings.input_device, output_device_index=settings.output_device))
    stt = create_stt(settings)
    llm = create_llm(settings, load_prompt(business, started, calendar_path=settings.calendar_path))
    tts = create_tts(settings)
    context = LLMContext()
    user, assistant = LLMContextAggregatorPair(context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))

    @user.event_handler("on_user_turn_stopped")
    async def user_turn(aggregator, strategy, message):
        logger.info("Caller speech turn ended%s", ": " + message.content
                    if settings.log_transcripts and not settings.show_live_transcripts and message.content else "")

    @assistant.event_handler("on_assistant_turn_stopped")
    async def assistant_turn(aggregator, message):
        logger.info("Agent response completed%s", ": " + message.content
                    if settings.log_transcripts and not settings.show_live_transcripts and message.content else "")

    processors = [transport.input(), stt]
    if settings.show_live_transcripts:
        processors.append(LiveInputDisplay())
    processors.extend([user, llm])
    if settings.show_live_transcripts:
        processors.append(LiveResponseDisplay())
    processors.extend([tts, transport.output(), assistant])
    pipeline = Pipeline(processors)
    worker = PipelineWorker(pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000,
                              enable_metrics=True),
        processor_unusable_policy=ProcessorUnusablePolicy.END)

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

    return worker, context, started


async def run_voice(settings, business):
    logger.info("Providers: STT=%s LLM=%s TTS=%s", settings.stt_provider,
                settings.llm_provider, settings.tts_provider)
    worker, context, started = build_pipeline(settings, business)
    runner = WorkerRunner(handle_sigint=False)
    interrupted = False
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
        path = save_result(result, settings.results_path, summary_status=status)
        logger.info("Conversation completed; structured result saved: %s (status=%s)", path, status)
    return interrupted
