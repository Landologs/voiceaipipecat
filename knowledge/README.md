# Spoken-language knowledge

This directory contains a deliberately small local vocabulary for receptionist calls. It is data, never an executable script. The application loads it once, normalizes the caller's completed transcript, and returns at most five relevant entries using exact phrase, alias, keyword, and conservative typo matching.

The complete dataset is never added to the system prompt. Matches are appended only to an ephemeral copy of the current user turn. The original conversation history and saved call result therefore contain the caller's words rather than retrieval metadata.

## JSONL schema

Each non-empty line is one JSON object:

```json
{
  "term": "",
  "variants": [],
  "meaning_he": "",
  "meaning_en": "",
  "category": "everyday|plumbing|emotion|urgency|normalization",
  "register": "neutral|casual|very_casual|rude",
  "agent_understand": true,
  "agent_can_use": false,
  "safe_to_mirror": false,
  "notes": "",
  "concept": "optional cross-language concept key"
}
```

`agent_can_use` and `safe_to_mirror` are intentionally conservative. Understanding an entry never grants the agent permission to repeat it. Profanity is marked as understood, disallowed for agent use, and unsafe to mirror.

## Editing rules

- Add terms that are plausible in a customer-service or plumbing call.
- Write short original definitions; do not copy dictionary prose.
- Put spelling and inflection aliases in `normalization.jsonl`.
- Keep ambiguous customer descriptions separate from professional terms.
- Never encode a customer description as a certain diagnosis.
- Keep each file valid JSONL and run the offline tests after changes.

Source provenance and licensing notes are recorded in `sources/SOURCES.md`.
