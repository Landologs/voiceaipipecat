"""Lightweight local spoken-language knowledge and adaptation."""

from app.knowledge.adaptation import ConversationProfile, TurnContextEngine
from app.knowledge.retrieval import KnowledgeEntry, KnowledgeMatch, KnowledgeStore

__all__ = [
    "ConversationProfile",
    "KnowledgeEntry",
    "KnowledgeMatch",
    "KnowledgeStore",
    "TurnContextEngine",
]
