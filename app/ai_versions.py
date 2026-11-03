"""Prompt versions, in a module of their own so `app/notes.py` and `app/extraction.py` can name
theirs without importing the database layer. See `app/ai_runs.py`."""

LABEL_PROMPT_VERSION = "label-v1"
NOTES_PROMPT_VERSION = "notes-v1"
EXTRACT_PROMPT_VERSION = "extract-v1"
