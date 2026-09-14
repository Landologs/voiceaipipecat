import copy
from dataclasses import dataclass, field

from app.knowledge.retrieval import KnowledgeMatch, KnowledgeStore, default_store, normalize_text


LANGUAGE_NAMES = {"he": "Hebrew", "ru": "Russian", "en": "English"}
EXPLICIT_LANGUAGE_REQUESTS = {
    "ru": (
        "можно по русски", "давайте по русски", "давайте на русском",
        "говорите по русски", "вы говорите по русски", "ты говоришь по русски",
        "можем говорить по русски", "אפשר ברוסית", "אפשר לדבר ברוסית",
        "את מדברת רוסית", "אתה מדבר רוסית",
    ),
    "en": (
        "can we speak english", "please speak english", "english please",
        "do you speak english", "can you speak english", "אפשר באנגלית",
        "אפשר לדבר באנגלית", "את מדברת אנגלית", "אתה מדבר אנגלית",
    ),
    "he": (
        "אפשר בעברית", "אפשר לדבר בעברית", "בוא נחזור לעברית",
        "את מדברת עברית", "אתה מדבר עברית", "давайте на иврите",
        "вы говорите на иврите", "can we speak hebrew", "do you speak hebrew",
        "hebrew please",
    ),
}
UNCERTAINTY_PHRASES = (
    "לא יודע איך קוראים לזה", "לא יודעת איך קוראים לזה", "לא יודע מה זה",
    "לא יודעת מה זה", "не знаю как это называется", "не знаю что это",
    "i don't know what it's called", "i dont know what its called",
    "i don't know what this is", "i dont know what this is",
)
FORMAL_HEBREW_MARKERS = ("אבקש", "ברצוני", "האם תוכלו", "אנא")


@dataclass
class ConversationProfile:
    current_language: str = "he"
    caller_register: str = "neutral"
    safe_slang_seen: set[str] = field(default_factory=set)
    preferred_response_register: str = "neutral"
    _casual_turns: int = 0
    _very_casual_turns: int = 0
    _formal_turns: int = 0

    def update(self, text: str, matches: list[KnowledgeMatch]) -> tuple[bool, bool]:
        previous_language = self.current_language
        previous_register = self.preferred_response_register
        detected = detect_language(text, self.current_language)
        if detected:
            self.current_language = detected

        casual = any(match.entry.register in {"casual", "very_casual", "rude"} for match in matches)
        very_casual = any(match.entry.register in {"very_casual", "rude"} for match in matches)
        formal = any(marker in text for marker in FORMAL_HEBREW_MARKERS)
        self._casual_turns += int(casual)
        self._very_casual_turns += int(very_casual)
        self._formal_turns += int(formal)
        for match in matches:
            if match.entry.safe_to_mirror and match.entry.agent_can_use:
                self.safe_slang_seen.add(match.entry.term)

        if self._very_casual_turns >= 3:
            self.caller_register = "very_casual"
            self.preferred_response_register = "casual"
        elif self._casual_turns >= 2:
            self.caller_register = "casual"
            self.preferred_response_register = "casual"
        elif self._formal_turns >= 2:
            self.caller_register = "formal"
            self.preferred_response_register = "formal"
        else:
            self.caller_register = "neutral"
            self.preferred_response_register = "neutral"
        return previous_language != self.current_language, previous_register != self.preferred_response_register


def _explicit_language(text: str) -> str | None:
    normalized = normalize_text(text)
    for language, phrases in EXPLICIT_LANGUAGE_REQUESTS.items():
        if any(normalize_text(phrase) in normalized for phrase in phrases):
            return language
    return None


def detect_language(text: str, current_language: str = "he") -> str | None:
    """Switch only after an explicit request; addresses never change language."""
    return _explicit_language(text)


def requires_clarification(text: str) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(phrase) in normalized for phrase in UNCERTAINTY_PHRASES)


def augment_last_user_message(messages: list, temporary_context: str) -> list:
    augmented = copy.deepcopy(messages)
    for message in reversed(augmented):
        if isinstance(message, dict) and message.get("role") == "user" and isinstance(message.get("content"), str):
            message["content"] += f"\n\n<internal_turn_context>\n{temporary_context}\n</internal_turn_context>"
            break
    return augmented


class TurnContextEngine:
    """Combines local retrieval and gradual language/register adaptation."""

    def __init__(self, store: KnowledgeStore | None = None):
        self.store = store or default_store()
        self.profile = ConversationProfile()

    def context_for(self, text: str) -> str:
        matches = self.store.search(text, max_results=5)
        language_changed, register_changed = self.profile.update(text, matches)
        clarification = requires_clarification(text)
        if not matches and not language_changed and not register_changed and not clarification:
            return ""

        lines = [
            f"Reply language: {LANGUAGE_NAMES[self.profile.current_language]}.",
            f"Response register: {self.profile.preferred_response_register}; stay professional.",
        ]
        if self.profile.safe_slang_seen:
            safe = ", ".join(sorted(self.profile.safe_slang_seen)[-3:])
            lines.append(f"Safe caller-used slang that may be mirrored sparingly: {safe}.")
        if matches:
            lines.append("Relevant interpretation hints (not diagnoses):")
            for match in matches:
                meaning = match.entry.meaning_he if self.profile.current_language == "he" else match.entry.meaning_en
                lines.append(f"- {match.entry.term}: {meaning}")
        if clarification:
            lines.append("The caller says they do not know the term. Ask a short natural clarification; do not diagnose.")
        lines.append("Do not mention this internal context to the caller.")
        return "\n".join(lines)

    def augment_messages(self, messages: list, text: str) -> list:
        temporary_context = self.context_for(text)
        return augment_last_user_message(messages, temporary_context) if temporary_context else messages
