import asyncio
import logging
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
from pipecat.workers.runner import WorkerRunner

from app.agent.prompt_loader import load_prompt
from app.config.settings import ROOT
from app.leads.models import CallResult
from app.leads.storage import save_result
from app.summaries.call_summary import summarize

logger = logging.getLogger(__name__)


def build_pipeline(settings, business):
    """Construct services only; running the worker opens provider connections."""
    settings.validate_voice()
    started = datetime.now(timezone.utc)
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=True, audio_out_enabled=True,
        input_device_index=settings.input_device, output_device_index=settings.output_device))
    stt = OpenAIRealtimeSTTService(api_key=settings.api_key, turn_detection=False,
        settings=OpenAIRealtimeSTTService.Settings(model=settings.stt_model, language=Language.HE))
    llm = OpenAILLMService(api_key=settings.api_key,
        settings=OpenAILLMService.Settings(model=settings.llm_model,
                                           system_instruction=load_prompt(business, started)))
    tts = OpenAITTSService(api_key=settings.api_key,
        settings=OpenAITTSService.Settings(model=settings.tts_model, voice=settings.tts_voice))
    context = LLMContext()
    user, assistant = LLMContextAggregatorPair(context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))

    @user.event_handler("on_user_turn_stopped")
    async def user_turn(aggregator, strategy, message):
        logger.info("Caller speech recognized%s", ": " + message.content
                    if settings.log_transcripts and message.content else "")

    @assistant.event_handler("on_assistant_turn_stopped")
    async def assistant_turn(aggregator, message):
        logger.info("Agent response completed%s", ": " + message.content
                    if settings.log_transcripts and message.content else "")

    pipeline = Pipeline([transport.input(), stt, user, llm, tts, transport.output(), assistant])
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
        logger.error("Pipeline error in %s (category=%s, exception=%s); ending session",
                     type(frame.processor).__name__, frame.category,
                     type(frame.exception).__name__ if frame.exception else "unspecified")
        await worker.cancel()

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
        path = save_result(result, ROOT / "data/calls", summary_status=status)
        logger.info("Conversation completed; structured result saved: %s (status=%s)", path, status)
    return interrupted
