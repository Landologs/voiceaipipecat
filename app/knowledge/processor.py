from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.knowledge.adaptation import TurnContextEngine, augment_last_user_message


class KnowledgeContextProcessor(FrameProcessor):
    """Add small, per-turn local hints to an ephemeral copy of LLM context."""

    def __init__(self, engine: TurnContextEngine | None = None):
        super().__init__()
        self.engine = engine or TurnContextEngine()
        self._last_message_count = -1
        self._last_temporary_context = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            messages = frame.context.get_messages()
            user_text = ""
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user" and isinstance(message.get("content"), str):
                    user_text = message["content"]
                    break
            if user_text:
                if len(messages) != self._last_message_count:
                    self._last_message_count = len(messages)
                    self._last_temporary_context = self.engine.context_for(user_text)
                if self._last_temporary_context:
                    augmented = augment_last_user_message(messages, self._last_temporary_context)
                    frame = LLMContextFrame(LLMContext(
                        augmented,
                        tools=frame.context.tools,
                        tool_choice=frame.context.tool_choice,
                    ))
        await self.push_frame(frame, direction)
