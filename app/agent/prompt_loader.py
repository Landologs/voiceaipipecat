from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from app.config.business_config import BusinessConfig


def load_prompt(business: BusinessConfig, at: datetime) -> str:
    template = Path(__file__).with_name("receptionist_he.txt").read_text(encoding="utf-8")
    return (template + "\nהגדרות עסק מאושרות:\n" + business.model_dump_json(indent=2)
            + "\nזמן מקומי: " + at.astimezone(ZoneInfo(business.timezone)).isoformat()
            + "\nהעסק פתוח כעת: " + str(business.is_open(at)))
