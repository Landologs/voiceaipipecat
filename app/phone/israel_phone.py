import re


def normalize_israeli_phone(raw: str) -> str:
    """Normalize supported IL mobile/landline shapes, or return empty when uncertain.

    Structural validation only; does not prove allocation, ownership or confirmation.
    Spoken number words deliberately require caller clarification.
    """
    if not raw or re.search(r"[^0-9+() .\-]", raw):
        return ""
    number = re.sub(r"[() .\-]", "", raw)
    if number.startswith("+972"):
        number = "0" + number[4:]
    if not re.fullmatch(r"(?:05[0-9]{8}|0[23489][0-9]{7}|07[0-9]{8})", number):
        return ""
    return "+972" + number[1:]
