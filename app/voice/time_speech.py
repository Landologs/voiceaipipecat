"""Turn 24-hour clock strings into natural spoken time for TTS."""

from __future__ import annotations

import re

TIME_RE = re.compile(r"(?<!\d)(ב-)?([01]?\d|2[0-3]):([0-5]\d)(?!\d)")

HE_HOURS = (
    "שתים עשרה", "אחת", "שתיים", "שלוש", "ארבע", "חמש", "שש",
    "שבע", "שמונה", "תשע", "עשר", "אחת עשרה",
)
RU_HOURS = (
    "двенадцать", "один", "два", "три", "четыре", "пять", "шесть",
    "семь", "восемь", "девять", "десять", "одиннадцать",
)
EN_HOURS = (
    "twelve", "one", "two", "three", "four", "five", "six",
    "seven", "eight", "nine", "ten", "eleven",
)


def _detect_language(text: str) -> str | None:
    counts = {
        "he": len(re.findall(r"[א-ת]", text)),
        "ru": len(re.findall(r"[А-Яа-яЁё]", text)),
        "en": len(re.findall(r"[A-Za-z]", text)),
    }
    language = max(("he", "ru", "en"), key=lambda item: counts[item])
    return language if counts[language] else None


def _he_period(hour: int) -> str:
    if hour == 0:
        return "בלילה"
    if 1 <= hour <= 4 or hour >= 22:
        return "בלילה"
    if 5 <= hour <= 11:
        return "בבוקר"
    if 12 <= hour <= 14:
        return "בצהריים"
    if 14 <= hour <= 17:
        return "אחר הצהריים"
    return "בערב"


def _format_he(hour: int, minute: int) -> str:
    if hour == 0 and minute == 0:
        return "חצות"
    if hour == 12 and minute == 0:
        return "שתים עשרה בצהריים"
    spoken_hour = HE_HOURS[hour % 12]
    minute_text = {
        0: "", 15: " ורבע", 30: " וחצי", 45: " וארבעים וחמש",
    }.get(minute, f" ו-{minute:02d}")
    return f"{spoken_hour}{minute_text} {_he_period(hour)}"


def _format_ru(hour: int, minute: int) -> str:
    spoken_hour = RU_HOURS[hour % 12]
    minute_text = {0: "", 15: " пятнадцать", 30: " тридцать", 45: " сорок пять"}.get(
        minute, f" {minute:02d}"
    )
    period = "ночи" if hour < 5 or hour >= 22 else "утра" if hour < 12 else "дня" if hour < 18 else "вечера"
    return f"{spoken_hour}{minute_text} {period}"


def _format_en(hour: int, minute: int) -> str:
    spoken_hour = EN_HOURS[hour % 12]
    minute_text = {0: "", 15: " fifteen", 30: " thirty", 45: " forty-five"}.get(
        minute, f" {minute:02d}"
    )
    return f"{spoken_hour}{minute_text} {'AM' if hour < 12 else 'PM'}"


def format_times_for_speech(text: str, language: str = "he") -> str:
    """Replace standalone 24-hour clock values without changing stored datetimes."""
    formatters = {"he": _format_he, "ru": _format_ru, "en": _format_en}
    formatter = formatters[language]
    def replace(match: re.Match) -> str:
        spoken = formatter(int(match.group(2)), int(match.group(3)))
        return "ב" + spoken if match.group(1) and language == "he" else spoken

    return TIME_RE.sub(replace, text)


class TimeSpeechFormatter:
    """Keep the current spoken language across sentence-sized TTS chunks."""

    def __init__(self, default_language: str = "he"):
        if default_language not in {"he", "ru", "en"}:
            raise ValueError("Default time speech language must be he, ru or en")
        self.current_language = default_language

    async def __call__(self, text: str, aggregation_type) -> str:
        detected = _detect_language(text)
        if detected:
            self.current_language = detected
        return format_times_for_speech(text, self.current_language)
