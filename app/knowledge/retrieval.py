import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE_ROOT = ROOT / "knowledge"
DATA_FILES = (
    "hebrew/everyday_slang.jsonl",
    "hebrew/plumbing_terms.jsonl",
    "hebrew/plumbing_colloquial.jsonl",
    "hebrew/normalization.jsonl",
    "russian/plumbing_terms.jsonl",
    "english/plumbing_terms.jsonl",
)


def normalize_text(text: str) -> str:
    """Normalize scripts and punctuation without translating the utterance."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.translate(str.maketrans({"״": '"', "׳": "'", "־": " "}))
    return " ".join(re.sub(r"[^\w']+", " ", text, flags=re.UNICODE).split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text))


@dataclass(frozen=True)
class KnowledgeEntry:
    term: str
    variants: tuple[str, ...]
    meaning_he: str
    meaning_en: str
    category: str
    register: str
    agent_understand: bool
    agent_can_use: bool
    safe_to_mirror: bool
    notes: str
    language: str
    concept: str

    @classmethod
    def from_dict(cls, data: dict, language: str) -> "KnowledgeEntry":
        term = str(data["term"]).strip()
        return cls(
            term=term,
            variants=tuple(str(item).strip() for item in data.get("variants", []) if str(item).strip()),
            meaning_he=str(data.get("meaning_he", "")).strip(),
            meaning_en=str(data.get("meaning_en", "")).strip(),
            category=str(data.get("category", "plumbing")).strip(),
            register=str(data.get("register", "neutral")).strip(),
            agent_understand=bool(data.get("agent_understand", True)),
            agent_can_use=bool(data.get("agent_can_use", False)),
            safe_to_mirror=bool(data.get("safe_to_mirror", False)),
            notes=str(data.get("notes", "")).strip(),
            language=language,
            concept=str(data.get("concept", term)).strip(),
        )


@dataclass(frozen=True)
class KnowledgeMatch:
    entry: KnowledgeEntry
    matched_variant: str
    score: float


class KnowledgeStore:
    """Cached exact/alias lookup with conservative typo matching."""

    def __init__(self, root: Path = DEFAULT_KNOWLEDGE_ROOT):
        self.root = root
        self.entries: list[KnowledgeEntry] = []
        self._normalizations: list[tuple[str, str]] = []
        self._load()

    def _load(self):
        for relative in DATA_FILES:
            path = self.root / relative
            language = relative.split("/", 1)[0]
            with path.open(encoding="utf-8-sig") as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    try:
                        entry = KnowledgeEntry.from_dict(json.loads(line), language)
                    except (KeyError, TypeError, json.JSONDecodeError) as exc:
                        raise ValueError(f"Invalid knowledge entry: {path}:{line_number}") from exc
                    if entry.category == "normalization":
                        for variant in entry.variants:
                            self._normalizations.append((normalize_text(variant), normalize_text(entry.term)))
                    else:
                        self.entries.append(entry)
        self._normalizations.sort(key=lambda pair: len(pair[0]), reverse=True)

    def normalize(self, text: str) -> str:
        normalized = normalize_text(text)
        for variant, canonical in self._normalizations:
            normalized = re.sub(
                rf"(?<!\w){re.escape(variant)}(?!\w)", canonical, normalized
            )
        return " ".join(normalized.split())

    def search(self, text: str, max_results: int = 5) -> list[KnowledgeMatch]:
        normalized = self.normalize(text)
        if not normalized or max_results <= 0:
            return []
        tokens = normalized.split()
        matches: list[KnowledgeMatch] = []
        for entry in self.entries:
            if not entry.agent_understand:
                continue
            best: tuple[float, str] | None = None
            for alias in (entry.term, *entry.variants):
                candidate = self.normalize(alias)
                if not candidate:
                    continue
                if _contains_phrase(normalized, candidate):
                    score = 100.0 + min(len(candidate), 40) + (25.0 if normalized == candidate else 0.0)
                    if best is None or score > best[0]:
                        best = (score, alias)
                elif " " not in candidate and len(candidate) >= 5:
                    ratio = max((SequenceMatcher(None, candidate, token).ratio() for token in tokens), default=0)
                    if ratio >= 0.9:
                        score = 60.0 + ratio * 10
                        if best is None or score > best[0]:
                            best = (score, alias)
            if best:
                matches.append(KnowledgeMatch(entry=entry, matched_variant=best[1], score=best[0]))

        matches.sort(key=lambda match: (-match.score, match.entry.term))
        unique: list[KnowledgeMatch] = []
        seen_concepts: set[str] = set()
        for match in matches:
            if match.entry.concept in seen_concepts:
                continue
            seen_concepts.add(match.entry.concept)
            unique.append(match)
            if len(unique) == min(max_results, 5):
                break
        return unique

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for relative in DATA_FILES:
            path = self.root / relative
            counts[relative] = sum(1 for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())
        return counts


@lru_cache(maxsize=1)
def default_store() -> KnowledgeStore:
    return KnowledgeStore()
