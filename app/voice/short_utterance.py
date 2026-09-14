"""Recover short OpenAI realtime STT turns that never receive a final frame."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import replace

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

_ACKNOWLEDGEMENT_WORDS = {
    "כן", "נכון", "לא", "אוקיי", "טוב", "הלו",
    "yes", "yeah", "yep", "no", "okay", "ok", "hello",
    "да", "ага", "нет", "ок", "алло",
}

# OpenAI's multilingual transcription occasionally writes a one-word Hebrew
# acknowledgement in Latin letters (for example, "Can" for "כן").  These are
# only normalized when the *entire* turn is a short acknowledgement.
_HEBREW_ACKNOWLEDGEMENT_ALIASES = {
    "can": "כן",
    "kan": "כן",
    "ken": "כן",
    "khen": "כן",
    "כןן": "כן",
    "lo": "לא",
    "low": "לא",
    "loe": "לא",
    "law": "לא",
    "love": "לא",
}


class ShortUtteranceFinalizer(FrameProcessor):
    """Promote an STT interim to final when OpenAI omits the final short transcript.

    OpenAI realtime transcription can emit a useful delta for a one-word turn such
    as ``כן`` and then complete with an empty transcript. Pipecat intentionally does
    not emit a ``TranscriptionFrame`` for that empty completion, so the user-turn
    aggregator has nothing to send to the LLM. This processor waits briefly after
    local VAD ends and forwards the provider's own interim text as the final turn
    only when no real final arrives.
    """

    def __init__(self, fallback_delay: float = 0.15):
        super().__init__()
        self._fallback_delay = fallback_delay
        self._parts: list[str] = []
        self._latest: InterimTranscriptionFrame | None = None
        self._fallback_task: asyncio.Task | None = None
        self._fallback_text = ""
        self._pending_stop: VADUserStoppedSpeakingFrame | None = None
        self._pending_stop_direction = FrameDirection.DOWNSTREAM

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # The universal user aggregator owns VAD and broadcasts its VAD frames
        # upstream.  The STT service needs the stop frame immediately so it can
        # commit its audio buffer.  Recover a buffered interim on the way back
        # before the aggregator's short speech timeout closes an empty turn.
        if direction == FrameDirection.UPSTREAM:
            if isinstance(frame, VADUserStartedSpeakingFrame):
                await self._finish_previous_if_pending()
                self._reset_turn()
            elif isinstance(frame, VADUserStoppedSpeakingFrame):
                if self._latest:
                    await self._cancel_fallback()
                    if self._is_acknowledgement(self._buffered_text()):
                        await self._emit_buffer()
                        await self.push_frame(frame, direction)
                        return
                await self.push_frame(frame, direction)
                if self._latest:
                    self._fallback_task = self.create_task(self._emit_after_delay())
                return
            elif isinstance(frame, (EndFrame, CancelFrame)):
                await self._finish_previous_if_pending()

        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, VADUserStartedSpeakingFrame):
                await self._finish_previous_if_pending()
                self._reset_turn()
            elif isinstance(frame, InterimTranscriptionFrame) and frame.text:
                self._parts.append(frame.text)
                self._latest = frame
            elif isinstance(frame, TranscriptionFrame):
                normalized_text = self._canonical_acknowledgement(frame.text)
                if normalized_text != frame.text:
                    frame = replace(frame, text=normalized_text)
                await self._cancel_fallback()
                if self._fallback_text and self._same_text(
                    self._canonical_acknowledgement(frame.text), self._fallback_text
                ):
                    self._fallback_text = ""
                    self._reset_turn()
                    return
                self._fallback_text = ""
                self._reset_turn()
                if self._pending_stop:
                    await self.push_frame(frame, direction)
                    await self._release_pending_stop()
                    return
            elif isinstance(frame, VADUserStoppedSpeakingFrame):
                if self._latest:
                    await self._cancel_fallback()
                    if self._is_acknowledgement(self._buffered_text()):
                        await self._emit_buffer()
                        await self.push_frame(frame, direction)
                        return
                    self._pending_stop = frame
                    self._pending_stop_direction = direction
                    self._fallback_task = self.create_task(self._emit_after_delay())
                    return
            elif isinstance(frame, (EndFrame, CancelFrame)):
                await self._finish_previous_if_pending()

        await self.push_frame(frame, direction)

    async def cleanup(self):
        await self._cancel_fallback()
        await super().cleanup()

    async def _emit_after_delay(self):
        try:
            await asyncio.sleep(self._fallback_delay)
            await self._emit_buffer()
            await self._release_pending_stop()
        except asyncio.CancelledError:
            pass
        finally:
            self._fallback_task = None

    async def _finish_previous_if_pending(self):
        if self._fallback_task and not self._fallback_task.done():
            await self._cancel_fallback()
            await self._emit_buffer()
        if self._pending_stop:
            await self._cancel_fallback()
            await self._emit_buffer()
            await self._release_pending_stop()

    async def _emit_buffer(self):
        if not self._latest:
            return
        text = self._canonical_acknowledgement(self._buffered_text())
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

    async def _release_pending_stop(self):
        if not self._pending_stop:
            return
        frame = self._pending_stop
        direction = self._pending_stop_direction
        self._pending_stop = None
        await self.push_frame(frame, direction)

    async def _cancel_fallback(self):
        if self._fallback_task and not self._fallback_task.done():
            task = self._fallback_task
            self._fallback_task = None
            await self.cancel_task(task)

    def _reset_turn(self):
        self._parts = []
        self._latest = None

    def _buffered_text(self) -> str:
        return "".join(self._parts).strip()

    @staticmethod
    def _is_acknowledgement(text: str) -> bool:
        words = ShortUtteranceFinalizer._acknowledgement_words(text)
        return bool(words) and len(words) <= 3 and all(
            word in _ACKNOWLEDGEMENT_WORDS for word in words
        )

    @staticmethod
    def _canonical_acknowledgement(text: str) -> str:
        """Return Hebrew canonical text for a one-word STT misrecognition."""
        words = ShortUtteranceFinalizer._acknowledgement_words(text)
        if not words or len(words) > 3:
            return text
        if not all(word in _ACKNOWLEDGEMENT_WORDS for word in words):
            return text
        return " ".join(words)

    @staticmethod
    def _acknowledgement_words(text: str) -> list[str]:
        raw_words = re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)
        words = []
        for word in raw_words:
            normalized = "".join(
                char for char in unicodedata.normalize("NFKD", word)
                if not unicodedata.combining(char)
            )
            words.append(_HEBREW_ACKNOWLEDGEMENT_ALIASES.get(normalized, normalized))
        return words

    @staticmethod
    def _same_text(left: str, right: str) -> bool:
        return " ".join(left.casefold().split()) == " ".join(right.casefold().split())
