"""One-off, idempotent: split the series rows that mix scopes, so each holds only the meetings of
its own team (docs/superpowers/specs/2026-11-14-series-follow-meetings.md section 8).

Run by `scripts/backfill_series_follow.py` (a dry run first, then `--apply`). It calls the same
`series.place_meeting` the hooks use, for every meeting that sits in a row of another scope:

* a private meeting in a team's row goes to its recorder's PERSONAL row;
* a meeting shared to, or moved to, a team goes to that team's row (a personal row's, too);
* a MOVED meeting is never taken out of its owning team's row;
* empty pre-recording cards, agenda lines, project links and other organisations' rows are never
  touched; a new row is made only for a meeting that has to move and never carries a project link
  or agenda lines; a team that is full cuts the meeting loose from its series instead.

Safe with `SERIES_FOLLOW_MEETINGS` off (it does not read the flag) and safe to run twice: the
second run finds nothing to move. The dry run does the identical work inside the transaction and
rolls it back, so its counts are exactly what `--apply` will do. The report is counts only: no
title, name or address.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import series as series_rules
from app.models import Meeting, Series, Team
from app.visibility import series_scope_clause


@dataclass
class Report:
    applied: bool
    rows_scanned: int = 0
    rows_split: int = 0  # rows that held at least one meeting of another scope
    meetings_moved: int = 0  # includes the ones cut loose
    rows_made: int = 0
    meetings_cut_loose: int = 0  # taken out of their series: the team had no room for a row
    meetings_skipped: int = 0  # a private meeting whose recorder's account is gone

    def lines(self) -> list[str]:
        head = (
            "APPLIED: the database was changed."
            if self.applied
            else "DRY RUN: nothing was changed. Run again with --apply to do it."
        )
        return [
            head,
            f"Recurring-meeting rows looked at: {self.rows_scanned}",
            f"Rows that held a meeting of another team or of the personal space: {self.rows_split}",
            f"Meetings moved to the row of their own team or personal space: {self.meetings_moved}",
            f"New rows made for them: {self.rows_made}",
            f"Meetings taken out of their series (the team had no room): {self.meetings_cut_loose}",
            f"Meetings left alone (their recorder's account is gone): {self.meetings_skipped}",
        ]


def split_mixed_series(session: Session, *, apply: bool) -> Report:
    """Move every meeting that sits in a row of another scope to the row of its own. Commits with
    `apply`; with a dry run it rolls everything back. Returns the counts."""
    report = Report(applied=apply)
    for series_id in list(session.scalars(select(Series.id).order_by(Series.id))):
        row = session.get(Series, series_id)
        team = session.get(Team, row.team_id)
        report.rows_scanned += 1
        in_scope = select(Meeting.id).where(
            Meeting.series_id == series_id, series_scope_clause(team)
        )
        strays = list(
            session.scalars(
                select(Meeting)
                .where(Meeting.series_id == series_id, Meeting.id.not_in(in_scope))
                .order_by(Meeting.id)
            )
        )
        split = False
        for meeting in strays:
            placed = series_rules.place_meeting(session, meeting)
            report.meetings_skipped += placed.skipped
            if not placed.moved:
                continue
            split = True
            report.meetings_moved += 1
            report.rows_made += placed.made
            report.meetings_cut_loose += placed.cut_loose
        report.rows_split += split
    if apply:
        session.commit()
    else:
        session.rollback()
    return report
