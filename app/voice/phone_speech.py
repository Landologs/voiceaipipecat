"""Language-aware phone-number text normalization for speech synthesis."""

from __future__ import annotations

import re

from app.phone.israel_phone import normalize_israeli_phone

PHONE_CANDIDATE_RE = re.compile(
    r"(?<!\d)(?:\+972|0)(?:[\s().-]*\d){8,9}(?!\d)"
)

DIGIT_WORDS = {
    "he": {
        "0": "אפס", "1": "אחת", "2": "שתיים", "3": "שלוש", "4": "ארבע",
        "5": "חמש", "6": "שש", "7": "שבע", "8": "שמונה", "9": "תשע",
    },
    "ru": {
        "0": "ноль", "1": "один", "2": "два", "3": "три", "4": "четыре",
        "5": "пять", "6": "шесть", "7": "семь", "8": "восемь", "9": "девять",
    },
    "en": {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    },
}


def detect_speech_language(text: str) -> str:
    """Infer the output language from a complete assistant sentence; Hebrew wins ties."""
    counts = {
        "he": len(re.findall(r"[א-ת]", text)),
        "ru": len(re.findall(r"[А-Яа-яЁё]", text)),
        "en": len(re.findall(r"[A-Za-z]", text)),
    }
    return max(("he", "ru", "en"), key=lambda language: counts[language])


def _detect_present_language(text: str) -> str | None:
    counts = {
        "he": len(re.findall(r"[א-ת]", text)),
        "ru": len(re.findall(r"[А-Яа-яЁё]", text)),
        "en": len(re.findall(r"[A-Za-z]", text)),
    }
    language = max(("he", "ru", "en"), key=lambda item: counts[item])
    return language if counts[language] else None


def domestic_phone(raw: str) -> str:
    """Validate an Israeli number and return its canonical local digit form."""
    normalized = normalize_israeli_phone(raw)
    if not normalized:
        return ""
    return "0" + normalized[4:]


def group_phone_digits(digits: str) -> tuple[str, str, str]:
    if len(digits) == 10 and digits.startswith(("05", "07")):
        return digits[:3], digits[3:6], digits[6:]
    if len(digits) == 9 and digits.startswith(("02", "03", "04", "08", "09")):
        return digits[:2], digits[2:5], digits[5:]
    raise ValueError("Expected a canonical Israeli mobile or landline number")


def group_israeli_phone_number(raw: str) -> str:
    """Return a validated Israeli number in familiar local display groups."""
    digits = domestic_phone(raw)
    if not digits:
        raise ValueError("Expected a valid Israeli phone number")
    return "-".join(group_phone_digits(digits))


def spoken_phone_number(raw: str, language: str) -> str:
    """Render a validated number digit by digit in language-specific words."""
    if language not in DIGIT_WORDS:
        raise ValueError("Phone speech language must be he, ru or en")
    digits = domestic_phone(raw)
    if not digits:
        raise ValueError("Expected a valid Israeli phone number")
    words = DIGIT_WORDS[language]
    return ", ".join(" ".join(words[digit] for digit in group)
                     for group in group_phone_digits(digits))


def format_phone_numbers_for_speech(text: str, language: str | None = None) -> str:
    """Replace only structurally valid Israeli phone numbers in spoken output text."""
    speech_language = language or detect_speech_language(text)

    def replace(match: re.Match) -> str:
        candidate = match.group(0)
        try:
            return spoken_phone_number(candidate, speech_language)
        except ValueError:
            return candidate

    return PHONE_CANDIDATE_RE.sub(replace, text)


async def normalize_phone_numbers_for_tts(text: str, aggregation_type) -> str:
    """Pipecat TTS transformer; aggregation_type is accepted by its public API."""
    return format_phone_numbers_for_speech(text)


class PhoneSpeechFormatter:
    """Remember the assistant's spoken language across sentence-sized TTS chunks."""

    def __init__(self, default_language: str = "he"):
        if default_language not in DIGIT_WORDS:
            raise ValueError("Default phone speech language must be he, ru or en")
        self.current_language = default_language

    async def __call__(self, text: str, aggregation_type) -> str:
        detected = _detect_present_language(text)
        if detected:
            self.current_language = detected
        return format_phone_numbers_for_speech(text, self.current_language)
