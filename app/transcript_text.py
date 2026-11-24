"""The one identity of a transcript text: `version_of`.

A page sends it back with an edit (`transcript_version`), and the notes remember the version of the
text they were written from (`Meeting.notes_source_version`). Kept here, apart from
`app/transcript_edit.py`, because `app/services.py` (the notes rewrite) needs it and the editor
imports `app/services.py`.
"""

import hashlib


def version_of(text: str) -> str:
    """A hash of the text, 16 hex characters: it changes with every edit and needs no column."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
