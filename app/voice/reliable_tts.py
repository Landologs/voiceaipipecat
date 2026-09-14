"""Small reliability wrapper for OpenAI's streaming TTS service."""

from __future__ import annotations

import asyncio
import logging

from pipecat.services.openai.tts import OpenAITTSService

logger = logging.getLogger(__name__)


class RetryingOpenAITTSService(OpenAITTSService):
    """Retry once when OpenAI accepts a TTS request but streams no frames."""

    async def run_tts(self, text: str, context_id: str):
        produced_frame = False
        async for frame in super().run_tts(text, context_id):
            produced_frame = True
            yield frame

        if produced_frame:
            return

        logger.warning(
            "OpenAI TTS returned no audio frames; retrying once (context=%s)",
            context_id,
        )
        await asyncio.sleep(0.15)
        async for frame in super().run_tts(text, context_id):
            yield frame
