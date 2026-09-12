from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel


class MessageStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"


class MessageResult(BaseModel):
    status: MessageStatus
    message_id: str = ""
    reason: str = ""


class WhatsAppBackend(Protocol):
    async def send_customer_confirmation(
        self, *, phone_normalized: str, appointment_id: str, text: str
    ) -> MessageResult: ...

    async def send_business_summary(self, *, text: str) -> MessageResult: ...


class MockWhatsApp:
    """Captures messages in memory and never contacts Meta."""

    def __init__(self):
        self.customer_messages: list[dict[str, str]] = []
        self.business_messages: list[dict[str, str]] = []
        self.fail_next_message = False

    def _result(self) -> MessageResult | None:
        if not self.fail_next_message:
            return None
        self.fail_next_message = False
        return MessageResult(status=MessageStatus.FAILED, reason="Mock message failed")

    async def send_customer_confirmation(
        self, *, phone_normalized: str, appointment_id: str, text: str
    ) -> MessageResult:
        if failure := self._result():
            return failure
        if not phone_normalized or not appointment_id or not text.strip():
            return MessageResult(
                status=MessageStatus.NEEDS_CLARIFICATION,
                reason="Phone, appointment ID, and message text are required",
            )
        message_id = "mock-wa-" + uuid4().hex
        self.customer_messages.append({
            "message_id": message_id,
            "phone_normalized": phone_normalized,
            "appointment_id": appointment_id,
            "text": text,
        })
        return MessageResult(status=MessageStatus.SUCCEEDED, message_id=message_id)

    async def send_business_summary(self, *, text: str) -> MessageResult:
        if failure := self._result():
            return failure
        if not text.strip():
            return MessageResult(
                status=MessageStatus.NEEDS_CLARIFICATION,
                reason="Summary text is required",
            )
        message_id = "mock-wa-" + uuid4().hex
        self.business_messages.append({"message_id": message_id, "text": text})
        return MessageResult(status=MessageStatus.SUCCEEDED, message_id=message_id)


class UnconfiguredWhatsApp:
    async def send_customer_confirmation(self, **kwargs) -> MessageResult:
        return MessageResult(status=MessageStatus.FAILED, reason="WhatsApp is not configured")

    async def send_business_summary(self, **kwargs) -> MessageResult:
        return MessageResult(status=MessageStatus.FAILED, reason="WhatsApp is not configured")
