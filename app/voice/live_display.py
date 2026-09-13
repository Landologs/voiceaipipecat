"""Terminal-only live transcript display. It never persists audio or text."""
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class LiveInputDisplay(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._interim = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._interim = ""
        elif isinstance(frame, InterimTranscriptionFrame) and frame.text:
            self._interim += frame.text
            print(f"\rВы (распознаётся): {self._interim}", end="", flush=True)
        elif isinstance(frame, TranscriptionFrame) and frame.text:
            print(f"\rВы: {frame.text}")
            self._interim = ""
        await self.push_frame(frame, direction)


class LiveResponseDisplay(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._started = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._started = True
            print("Агент: ", end="", flush=True)
        elif isinstance(frame, LLMTextFrame) and frame.text:
            print(frame.text, end="", flush=True)
        elif isinstance(frame, LLMFullResponseEndFrame) and self._started:
            self._started = False
            print()
        await self.push_frame(frame, direction)
