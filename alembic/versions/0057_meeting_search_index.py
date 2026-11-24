"""A full-text index for meeting search: `meeting_search_text()` and `ix_meetings_search_text`

Meeting search (Ask Beyric's `candidates`, the Meetings list's search box) matches words in a
meeting's notes plus its transcript. Without an index every search rebuilds the `tsvector` of
every meeting the caller may read (about 3 ms each: 13 s at 4,000 meetings, `docs/sop/
2026-11-24-meeting-search-index.md`). This adds:

* the SQL function `meeting_search_text(notes, speaker_transcript, transcript)`, IMMUTABLE (an
  index needs that; `concat_ws`, which the search used before, is not). It folds accents out of
  the stored text (Unicode NFKD, then the combining marks removed) the way `app.extraction.
  normalise` folds the words a person types, so "équipe" and "equipe" both find "équipe"; and
* a GIN index on a call to it, built CONCURRENTLY so inserts and updates to `meetings` (the worker
  writes to it all the time) are not blocked while it builds.

The function text below is a frozen copy of `app.models.MEETING_SEARCH_TEXT_SQL` (a migration must
not change with the app); a test keeps the two equal. `CREATE INDEX CONCURRENTLY` cannot run inside
a transaction, so it runs in Alembic's `autocommit_block`, after the function is committed. A
build that fails part way leaves an INVALID index of the same name, so the first thing it does is
drop any such leftover; running `alembic upgrade` again then starts clean.

Downgrade drops the index and the function; search goes back to rebuilding the vectors (slow, and
correct).

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-24
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION meeting_search_text(
    notes text, speaker_transcript text, transcript text
) RETURNS tsvector LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT to_tsvector(
        'simple',
        regexp_replace(
            normalize(
                coalesce(notes, '') || ' ' ||
                    coalesce(nullif(speaker_transcript, ''), transcript, ''),
                NFKD
            ),
            '[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]',
            '',
            'g'
        )
    )
$$
"""


def upgrade() -> None:
    op.execute(FUNCTION_SQL)
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_meetings_search_text")
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_meetings_search_text ON meetings "
            "USING gin (meeting_search_text(notes, speaker_transcript, transcript))"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_meetings_search_text")
    op.execute("DROP FUNCTION IF EXISTS meeting_search_text(text, text, text)")
