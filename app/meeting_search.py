"""Full-text matching of meetings, shared by Ask Beyric (`app/ask.py` `candidates`) and the keyword
box on the Meetings list (`app/services.py` `list_meetings`).

A meeting's searchable text is its notes plus its transcript (the speaker-labelled one when there
is one). Words match by prefix on their crude stem (`app/words.py`), so "pricing" finds "price" and
"decid" finds "decided". Nothing here calls a model.
"""

from sqlalchemy import ColumnElement, and_, func, or_

from app.models import Meeting
from app.words import stems


def document():
    """The text of a meeting as one `tsvector` (the `simple` configuration: no language rules, so a
    French or Yoruba name matches as typed)."""
    return func.to_tsvector(
        "simple",
        func.concat_ws(
            " ",
            Meeting.notes,
            func.coalesce(func.nullif(Meeting.speaker_transcript, ""), Meeting.transcript),
        ),
    )


def prefix_query(words: list[str], joiner: str = " | "):
    """A `tsquery` of the stems as prefixes, joined by `joiner` (` | ` any of them, ` & ` all)."""
    return func.to_tsquery("simple", joiner.join(f"{word}:*" for word in words))


def _like_escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def keyword_clause(text: str) -> ColumnElement[bool]:
    """The `where` condition for the Meetings list's search box: the title contains `text` (case
    ignored, `%` and `_` taken literally), or, for a meeting whose notes are ready, every word of
    `text` is in its notes or transcript. A meeting that is not ready can match on its title only.

    A meeting with no title is shown as "Google Meet abc-defg-hij" or "Zoom 8820...", so the code
    in that name is matched too. Words that never identify a topic ("meeting", "last", "the") are
    left out of the full-text part; a search made only of them matches titles only."""
    pattern = f"%{_like_escaped(text)}%"
    titled = Meeting.title.ilike(pattern, escape="\\")
    untitled = and_(
        func.coalesce(Meeting.title, "") == "",
        Meeting.platform != "teams",  # a Teams meeting is shown with no code
        Meeting.native_id.ilike(pattern, escape="\\"),
    )
    parts = [titled, untitled]
    words = stems(text)
    if words:
        parts.append(
            and_(Meeting.status == "ready", document().op("@@")(prefix_query(words, " & ")))
        )
    return or_(*parts)
