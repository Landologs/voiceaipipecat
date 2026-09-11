import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def load_calendar_context(path: Path, at: datetime, timezone_name: str) -> str:
    """Render only the configured demo calendar as Hebrew prompt context."""
    calendar = json.loads(path.read_text(encoding="utf-8-sig"))
    local = at.astimezone(ZoneInfo(timezone_name))
    lines = []
    for day in calendar.get("days", []):
        date = local.date() + timedelta(days=int(day["offset"]))
        available = []
        for slot in day.get("slots", []):
            if slot.get("status") != "available":
                continue
            if day["offset"] == 0 and slot["end"] <= local.strftime("%H:%M"):
                continue
            available.append(f"{slot['start']}–{slot['end']}")
        label = "היום" if day["offset"] == 0 else "מחר" if day["offset"] == 1 else f"בעוד {day['offset']} ימים"
        slots = ", ".join(available) if available else "אין חלונות פנויים"
        lines.append(f"{label} ({date.strftime('%d.%m')}): {slots}")
    return "\n".join(lines)
