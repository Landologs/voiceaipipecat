"""Recover short OpenAI realtime STT turns that never receive a final frame."""

from __future__ import annotations

import asyncio
import logging

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.time import time_now_iso8601

logger = logging.getLogger(__name__)


class ShortUtteranceFinalizer(FrameProcessor):
    """Promote an STT interim to final when OpenAI omits the final short transcript.

    OpenAI realtime transcription can emit a useful delta for a one-word turn such
    as ``כן`` and then complete with an empty transcript. Pipecat intentionally does
    not emit a ``TranscriptionFrame`` for that empty completion, so the user-turn
    aggregator has nothing to send to the LLM. This processor waits briefly after
    local VAD ends and forwards the provider's own interim text as the final turn
    only when no real final arrives.
    """

    def __init__(self, fallback_delay: float = 1.35):
        super().__init__()
        self._fallback_delay = fallback_delay
        self._parts: list[str] = []
        self._latest: InterimTranscriptionFrame | None = None
        self._fallback_task: asyncio.Task | None = None
        self._fallback_text = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, VADUserStartedSpeakingFrame):
                await self._finish_previous_if_pending()
                self._reset_turn()
            elif isinstance(frame, InterimTranscriptionFrame) and frame.text:
                self._parts.append(frame.text)
                self._latest = frame
            elif isinstance(frame, TranscriptionFrame):
                await self._cancel_fallback()
                if self._fallback_text and self._same_text(frame.text, self._fallback_text):
                    self._fallback_text = ""
                    self._reset_turn()
                    return
                self._fallback_text = ""
                self._reset_turn()
            elif isinstance(frame, VADUserStoppedSpeakingFrame):
                await self._cancel_fallback()
                self._fallback_task = self.create_task(self._emit_after_delay())
            elif isinstance(frame, (EndFrame, CancelFrame)):
                await self._cancel_fallback()

        await self.push_frame(frame, direction)

    async def cleanup(self):
        await self._cancel_fallback()
        await super().cleanup()

    async def _emit_after_delay(self):
        try:
            await asyncio.sleep(self._fallback_delay)
            await self._emit_buffer()
        except asyncio.CancelledError:
            pass
        finally:
            self._fallback_task = None

    async def _finish_previous_if_pending(self):
        if self._fallback_task and not self._fallback_task.done():
            await self._cancel_fallback()
            await self._emit_buffer()

    async def _emit_buffer(self):
        if not self._latest:
            return
        text = "".join(self._parts).strip()
        if not text:
            return
        source = self._latest
        self._fallback_text = text
        self._reset_turn()
        logger.info("Recovered a short caller utterance from the STT interim result")
        await self.push_frame(
            TranscriptionFrame(
                text,
                source.user_id,
                source.timestamp or time_now_iso8601(),
                language=source.language,
                result={"source": "interim_fallback"},
                finalized=True,
            )
        )

    async def _cancel_fallback(self):
        if self._fallback_task and not self._fallback_task.done():
            task = self._fallback_task
            self._fallback_task = None
            await self.cancel_task(task)

    def _reset_turn(self):
        self._parts = []
        self._latest = None

    @staticmethod
    def _same_text(left: str, right: str) -> bool:
        return " ".join(left.casefold().split()) == " ".join(right.casefold().split())
