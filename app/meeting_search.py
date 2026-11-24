"""Full-text matching of meetings, shared by Ask Beyric (`app/ask.py` `candidates`) and the keyword
box on the Meetings list (`app/services.py` `list_meetings`).

A meeting's searchable text is its notes plus its transcript (the speaker-labelled one when there
is one). Words match by prefix on their crude stem (`app/words.py`), so "pricing" finds "price" and
"decid" finds "decided". Nothing here calls a model.
"""

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import aliased

from app.models import Meeting
from app.words import stems


def document(meeting=Meeting):
    """The text of a meeting as one `tsvector`: a call to the one SQL function
    `meeting_search_text` (`app/models.py`), which the GIN index `ix_meetings_search_text` is built
    on. Every search calls it, never a copy of its body: that is what lets Postgres use the index.
    `meeting` is `Meeting` or an alias of it."""
    return func.meeting_search_text(
        meeting.notes, meeting.speaker_transcript, meeting.transcript, type_=TSVECTOR
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
    left out of the full-text part; a search made only of them matches titles only.

    The two kinds of match are two separate subqueries (`id IN (...) OR id IN (...)`), not one `OR`
    over the row: with the title test in the same `OR` Postgres cannot use the full-text index and
    reads every meeting's text (measured: 13 s at 4,000 meetings, 3 ms with the split)."""
    pattern = f"%{_like_escaped(text)}%"
    by_title = aliased(Meeting)
    titled = select(by_title.id).where(
        or_(
            by_title.title.ilike(pattern, escape="\\"),
            and_(
                func.coalesce(by_title.title, "") == "",
                by_title.platform != "teams",  # a Teams meeting is shown with no code
                by_title.native_id.ilike(pattern, escape="\\"),
            ),
        )
    )
    parts = [Meeting.id.in_(titled)]
    words = stems(text)
    if words:
        by_text = aliased(Meeting)
        in_text = select(by_text.id).where(
            by_text.status == "ready",
            document(by_text).op("@@")(prefix_query(words, " & ")),
        )
        parts.append(Meeting.id.in_(in_text))
    return or_(*parts)
