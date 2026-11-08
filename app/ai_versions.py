"""Prompt versions, in a module of their own so `app/notes.py` and `app/extraction.py` can name
theirs without importing the database layer. See `app/ai_runs.py`."""

LABEL_PROMPT_VERSION = "label-v1"
NOTES_PROMPT_VERSION = "notes-v1"
# The full extraction (review hints, action items, milestones): what the AI1 eval measures and
# what the Tasks phase will run. v2 also asks for a plain `reason` on a medium or low item
# (Suggested tasks). v3 also asks, for each decision bullet, who decided it, the words that show it,
# whether it was only proposed and which action items carry it out (the Decisions record).
EXTRACT_PROMPT_VERSION = "extract-v3"
# The call W1 makes: review hints only, a smaller prompt and schema.
EXTRACT_REVIEW_PROMPT_VERSION = "extract-review-v1"
# Translating notes and transcript lines into French or Spanish (app/translation.py).
TRANSLATE_PROMPT_VERSION = "translate-v1"
# Ask Beyric: answering a question from the passages of the asker's own meetings (app/ask.py).
ASK_PROMPT_VERSION = "ask-v1"
