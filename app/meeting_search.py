"""Full-text matching of meetings, shared by Ask Beyric (`app/ask.py` `candidates`) and the keyword
box on the Meetings list (`app/services.py` `list_meetings`).

A meeting's searchable text is its notes plus its transcript (the speaker-labelled one when there
is one). Words match by prefix on their crude stem (`app/words.py`), so "pricing" finds "price" and
"decid" finds "decided". Nothing here calls a model.
"""

import re
import unicodedata

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import TSVECTOR

from app.models import SEARCH_FOLD_MARKS, Meeting
from app.words import stems

_MARKS = re.compile(SEARCH_FOLD_MARKS)


def document():
    """The text of a meeting as one `tsvector`, accents folded: a call to the one SQL function
    `meeting_search_text` (`app/models.py`), which the GIN index `ix_meetings_search_text` is
    built on. Every search calls it, never a copy of its body: that is what lets Postgres use the
    index."""
    return func.meeting_search_text(
        Meeting.notes, Meeting.speaker_transcript, Meeting.transcript, type_=TSVECTOR
    )


def prefix_query(words: list[str], joiner: str = " | "):
    """A `tsquery` of the stems as prefixes, joined by `joiner` (` | ` any of them, ` & ` all)."""
    return func.to_tsquery("simple", joiner.join(f"{word}:*" for word in words))


def _like_escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fold(text: str) -> str:
    """`text` with accents folded away, as the stored text is (`meeting_search_text`): NFKD, then
    the combining marks removed. Case and punctuation are left alone."""
    return _MARKS.sub("", unicodedata.normalize("NFKD", text))


def _folded(column):
    """A text column folded the same way, in SQL (for the title, which has no index)."""
    return func.regexp_replace(func.normalize(column, sa_text("NFKD")), SEARCH_FOLD_MARKS, "", "g")


def keyword_clause(text: str, readable: ColumnElement[bool]) -> ColumnElement[bool]:
    """The `where` condition for the Meetings list's search box: the title contains `text` (case
    and accents ignored, `%` and `_` taken literally), or, for a meeting whose notes are ready,
    every word of `text` is in its notes or transcript. A meeting that is not ready can match on
    its title only. `readable` is the caller's visibility condition for the list
    (`visibility.readable_in`): both subqueries look only at meetings it allows, so a search never
    reads another person's text.

    A meeting with no title is shown as "Google Meet abc-defg-hij" or "Zoom 8820...", so the code
    in that name is matched too. Words that never identify a topic ("meeting", "last", "the") are
    left out of the full-text part; a search made only of them matches titles only.

    The two kinds of match are two separate subqueries (`id IN (...) OR id IN (...)`), not one `OR`
    over the row: with the title test in the same `OR` Postgres cannot use the full-text index and
    reads every meeting's text (measured: 13 s at 4,000 meetings, 3 ms with the split)."""
    pattern = f"%{_like_escaped(fold(text))}%"
    titled = (
        select(Meeting.id)
        .where(
            readable,
            or_(
                _folded(Meeting.title).ilike(pattern, escape="\\"),
                and_(
                    func.coalesce(Meeting.title, "") == "",
                    Meeting.platform != "teams",  # a Teams meeting is shown with no code
                    Meeting.native_id.ilike(pattern, escape="\\"),
                ),
            ),
        )
        .correlate(None)  # its own `meetings`, not the outer query's
    )
    parts = [Meeting.id.in_(titled)]
    words = stems(text)
    if words:
        in_text = (
            select(Meeting.id)
            .where(
                readable,
                Meeting.status == "ready",
                document().op("@@")(prefix_query(words, " & ")),
            )
            .correlate(None)
        )
        parts.append(Meeting.id.in_(in_text))
    return or_(*parts)
