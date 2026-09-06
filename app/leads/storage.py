import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.leads.models import CallResult


def save_result(result: CallResult, directory: Path, *, summary_status: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    call_id = uuid4().hex
    target = directory / f"{call_id}.json"
    temporary = directory / f".{call_id}.tmp"
    payload = {"call_id": call_id, "saved_at": datetime.now(timezone.utc).isoformat(),
               "summary_status": summary_status, "result": result.model_dump()}
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
