import logging

from pipecat.observers.service_metrics_observer import ServiceMetricsObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver

logger = logging.getLogger(__name__)


def create_latency_observers():
    turn_observer = UserBotLatencyObserver()
    service_observer = ServiceMetricsObserver()

    @turn_observer.event_handler("on_latency_measured")
    async def on_turn_latency(observer, seconds):
        logger.info("Latency total_turn_to_first_audio=%.3fs", seconds)

    @turn_observer.event_handler("on_latency_breakdown")
    async def on_turn_breakdown(observer, breakdown):
        if breakdown.user_turn_secs is not None:
            logger.info("Latency transcription_and_turn_detection=%.3fs", breakdown.user_turn_secs)

    @service_observer.event_handler("on_service_latency")
    async def on_service_latency(observer, record):
        logger.info(
            "Latency service=%s processor=%s model=%s seconds=%.3f",
            record.kind.value,
            record.processor,
            record.model or "unspecified",
            record.seconds,
        )

    return [turn_observer, service_observer]
