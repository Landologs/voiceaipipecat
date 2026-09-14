import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from app.leads.models import CallResult


def render_text_report(
    result: CallResult,
    *,
    saved_at: datetime,
    summary_status: str,
    transcript: list[dict[str, str]],
    recording_file: str = "",
) -> str:
    """Create a human-readable local companion to the machine-readable JSON result."""
    fields = (
        ("Статус обработки", summary_status),
        ("Имя клиента", result.customer_name),
        ("Телефон", result.phone_normalized or result.phone_raw),
        ("Телефон подтверждён", "да" if result.phone_confirmed else "нет"),
        ("Язык", result.language),
        ("Запрос", result.request_summary or result.intent),
        ("Проблема", result.plumbing_problem),
        ("Адрес", result.address),
        ("Предпочтительное время", result.preferred_time),
        ("Срочность", result.urgency),
        ("Статус записи", result.appointment_status),
        ("ID записи", result.appointment_id),
        ("После рабочих часов", "да" if result.after_hours else "нет"),
        ("Следующее действие", result.next_action),
        ("Заметки", result.notes),
    )
    lines = [
        "Результат звонка",
        f"ID звонка: {result.call_id}",
        f"Сохранено: {saved_at.isoformat()}",
    ]
    if recording_file:
        lines.append(f"Аудиозапись: {recording_file}")
    lines.extend(f"{label}: {value}" for label, value in fields if value)
    if transcript:
        lines.extend(("", "Полный текст разговора"))
        for message in transcript:
            speaker = "Клиент" if message["role"] == "user" else "Агент"
            lines.append(f"{speaker}: {message['content']}")
    return "\n".join(lines) + "\n"


class LeadRepository(Protocol):
    def save(
        self,
        result: CallResult,
        *,
        summary_status: str = "complete",
        transcript: list[dict[str, str]] | None = None,
        recording_path: Path | None = None,
    ) -> Path: ...
    def get(self, call_id: str) -> CallResult | None: ...


class JsonLeadRepository:
    """Replaceable local JSON lead repository for the MVP."""

    def __init__(self, directory: Path):
        self.directory = directory

    def save(
        self,
        result: CallResult,
        *,
        summary_status: str = "complete",
        transcript: list[dict[str, str]] | None = None,
        recording_path: Path | None = None,
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        transcript = transcript or []
        call_id = result.call_id or uuid4().hex
        result.call_id = call_id
        target = self.directory / f"{call_id}.json"
        temporary = self.directory / f".{call_id}.tmp"
        text_target = self.directory / f"{call_id}.txt"
        text_temporary = self.directory / f".{call_id}.txt.tmp"
        saved_at = datetime.now(timezone.utc)
        recording_file = recording_path.name if recording_path and recording_path.is_file() else ""
        payload = {
            "call_id": call_id,
            "saved_at": saved_at.isoformat(),
            "summary_status": summary_status,
            "result": result.model_dump(mode="json"),
            "transcript": transcript,
            "recording_file": recording_file,
        }
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
            with text_temporary.open("x", encoding="utf-8") as stream:
                stream.write(render_text_report(
                    result,
                    saved_at=saved_at,
                    summary_status=summary_status,
                    transcript=transcript,
                    recording_file=recording_file,
                ))
                stream.flush()
                os.fsync(stream.fileno())
            text_temporary.replace(text_target)
        finally:
            temporary.unlink(missing_ok=True)
            text_temporary.unlink(missing_ok=True)
        return target

    def get(self, call_id: str) -> CallResult | None:
        target = self.directory / f"{call_id}.json"
        if not target.is_file():
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
        return CallResult.model_validate(payload["result"])


def conversation_transcript(messages: list) -> list[dict[str, str]]:
    """Keep the completed caller and agent turns, excluding prompts and tool metadata."""
    transcript = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            transcript.append({"role": message["role"], "content": content.strip()})
    return transcript


def save_result(
    result: CallResult,
    directory: Path,
    *,
    summary_status: str,
    transcript: list[dict[str, str]] | None = None,
    recording_path: Path | None = None,
) -> Path:
    return JsonLeadRepository(directory).save(
        result,
        summary_status=summary_status,
        transcript=transcript,
        recording_path=recording_path,
    )
