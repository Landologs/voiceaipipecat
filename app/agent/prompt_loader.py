from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from app.config.business_config import BusinessConfig


def load_prompt(business: BusinessConfig, at: datetime, *, calendar_path: Path | None = None) -> str:
    template = Path(__file__).with_name("receptionist_he.txt").read_text(encoding="utf-8")
    prompt = (template + "\nהגדרות עסק מאושרות:\n" + business.model_dump_json(indent=2)
            + "\nזמן מקומי: " + at.astimezone(ZoneInfo(business.timezone)).isoformat()
            + "\nהעסק פתוח כעת: " + str(business.is_open(at)))
    if calendar_path:
        prompt += ("\n\nגבולות מידע פנימיים: השתמשי אך ורק בהגדרות העסק ובתוצאות "
                   "הכלים. אל תזכירי לפונה בדיקה, הדגמה או סביבת פיתוח. "
                   "אל תציגי חלון פנוי בלי תוצאה עדכנית של check_availability ואל "
                   "תטעני שפעולה הצליחה בלי תוצאת succeeded מתאימה.")
    return prompt
