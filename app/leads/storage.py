import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from app.leads.models import CallResult


class LeadRepository(Protocol):
    def save(self, result: CallResult, *, summary_status: str = "complete") -> Path: ...
    def get(self, call_id: str) -> CallResult | None: ...


class JsonLeadRepository:
    """Replaceable local JSON lead repository for the MVP."""

    def __init__(self, directory: Path):
        self.directory = directory

    def save(self, result: CallResult, *, summary_status: str = "complete") -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        call_id = result.call_id or uuid4().hex
        result.call_id = call_id
        target = self.directory / f"{call_id}.json"
        temporary = self.directory / f".{call_id}.tmp"
        payload = {
            "call_id": call_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "summary_status": summary_status,
            "result": result.model_dump(mode="json"),
        }
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def get(self, call_id: str) -> CallResult | None:
        target = self.directory / f"{call_id}.json"
        if not target.is_file():
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
        return CallResult.model_validate(payload["result"])


def save_result(result: CallResult, directory: Path, *, summary_status: str) -> Path:
    return JsonLeadRepository(directory).save(result, summary_status=summary_status)
