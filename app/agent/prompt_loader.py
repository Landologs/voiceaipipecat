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
        from app.demo.calendar import load_calendar_context

        prompt += ("\n\nמצב הדגמה סגור: זהו עסק ולוח זמנים בדיוניים בלבד. "
                   "השתמשי אך ורק בהגדרות ובחלונות ההדגמה שמופיעים כאן. "
                   "אל תשתמשי בידע חיצוני ואל תצאי ממצב הדגמה. "
                   "אפשר להציג חלון פנוי או לתאר בחירה כ'שריון הדגמה', אבל לעולם לא "
                   "לטעון שנקבע תור אמיתי, נשלחה הודעה או נשמרה הזמנה אמיתית.\n"
                   "חלונות הדגמה פנויים בלבד:\n"
                   + load_calendar_context(calendar_path, at, business.timezone))
    return prompt
