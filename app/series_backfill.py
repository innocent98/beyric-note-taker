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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import series as series_rules
from app.models import Meeting, Series, SeriesAgendaItem, Team
from app.visibility import series_scope_clause


@dataclass
class Report:
    applied: bool
    rows_scanned: int = 0
    rows_split: int = 0  # rows that held at least one meeting of another scope
    meetings_moved: int = 0  # includes the ones cut loose
    rows_made: int = 0
    meetings_cut_loose: int = 0  # taken out of their series: the team had no room for a row
    meetings_no_card: int = 0  # left ungrouped: the team has no card and the system makes none
    meetings_skipped: int = 0  # a private meeting whose recorder's account is gone (every run)
    personal_teams_made: int = 0  # a person who had none (they are made when first needed)
    rows_left_empty: int = 0  # rows the split left with no meeting at all
    rows_left_empty_with_lines: int = 0  # ...of which still hold waiting agenda lines

    def lines(self) -> list[str]:
        head = (
            "APPLIED: the database was changed."
            if self.applied
            else "DRY RUN: nothing was changed. Run again with --apply to do it."
        )
        lines = [
            head,
            f"Recurring-meeting rows looked at: {self.rows_scanned}",
            f"Rows that held a meeting of another team or of the personal space: {self.rows_split}",
            f"Meetings moved to the row of their own team or personal space: {self.meetings_moved}",
            f"New rows made for them: {self.rows_made}",
            f"Meetings taken out of their series (the team had no room): {self.meetings_cut_loose}",
            f"Meetings left alone (their recorder's account is gone): {self.meetings_skipped}",
            f"Personal spaces made for people who had none: {self.personal_teams_made}",
            f"Rows left with no meeting after the split: {self.rows_left_empty}",
            f"...of which still hold waiting agenda lines: {self.rows_left_empty_with_lines}",
        ]
        if self.meetings_no_card:  # only when there is one: the usual report keeps its lines
            lines.append(
                "Meetings left ungrouped (their team has no card for them): "
                f"{self.meetings_no_card}"
            )
        return lines


def split_mixed_series(session: Session, *, apply: bool) -> Report:
    """Move every meeting that sits in a row of another scope to the row of its own. Commits with
    `apply`; with a dry run it rolls everything back. Returns the counts."""
    report = Report(applied=apply)
    personal_before = session.scalar(
        select(func.count()).select_from(Team).where(Team.is_personal.is_(True))
    )
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
                .with_for_update(of=Meeting)  # a share committing now is waited for, then re-read
                .execution_options(populate_existing=True)
            )
        )
        split = False
        for meeting in strays:
            placed = series_rules.place_meeting(session, meeting, actor=series_rules.SYSTEM)
            report.meetings_skipped += placed.skipped
            if not placed.moved:
                continue
            split = True
            report.meetings_moved += 1
            report.rows_made += placed.made
            report.meetings_cut_loose += placed.cut_loose
            report.meetings_no_card += placed.no_card
        report.rows_split += split
        if split and not session.scalar(
            select(Meeting.id).where(Meeting.series_id == series_id).limit(1)
        ):
            report.rows_left_empty += 1
            report.rows_left_empty_with_lines += bool(
                session.scalar(
                    select(SeriesAgendaItem.id)
                    .where(
                        SeriesAgendaItem.series_id == series_id,
                        SeriesAgendaItem.consumed_at.is_(None),
                    )
                    .limit(1)
                )
            )
    session.flush()
    report.personal_teams_made = (
        session.scalar(select(func.count()).select_from(Team).where(Team.is_personal.is_(True)))
        - personal_before
    )
    if apply:
        session.commit()
    else:
        session.rollback()
    return report


@dataclass
class EmptyCardsReport:
    """What `remove_empty_team_cards` found or did. Counts only."""

    applied: bool
    cards_looked_at: int = 0  # calendar-made cards of ordinary teams
    cards_removed: int = 0
    cards_kept: int = 0
    kept_with_meeting: int = 0
    kept_with_lines: int = 0
    kept_with_project: int = 0
    kept_with_mail: int = 0

    def lines(self) -> list[str]:
        head = (
            "APPLIED: the database was changed."
            if self.applied
            else "DRY RUN: nothing was changed. Run again with --apply to do it."
        )
        return [
            head,
            f"Calendar-made recurring cards of teams looked at: {self.cards_looked_at}",
            f"Empty ones removed: {self.cards_removed}",
            f"Kept, because something is in them: {self.cards_kept}",
            f"...a meeting is grouped under it: {self.kept_with_meeting}",
            f"...an agenda line is waiting: {self.kept_with_lines}",
            f"...a project is linked: {self.kept_with_project}",
            f"...a mail about it is still to be sent: {self.kept_with_mail}",
        ]


def remove_empty_team_cards(session: Session, *, apply: bool) -> EmptyCardsReport:
    """One-off, idempotent: remove the EMPTY recurring cards that the calendar sync made in an
    ordinary team before a person's calendar cards moved to their personal space (the simpler
    model). A card is empty when `series.non_empty_reason` finds nothing: no meeting grouped under
    it, no waiting agenda line, no linked project, no mail still to be sent. Only cards made from a
    calendar event (a hand-made empty card is somebody's own work and stays) and only in ordinary
    teams (a personal card is the person's own). **No "ignored" mark is written**, so the next sync
    makes the person's PERSONAL card for the event. The dry run does the identical work inside the
    transaction and rolls it back. Counts only: no title, name or id."""
    report = EmptyCardsReport(applied=apply)
    ids = list(
        session.scalars(
            select(Series.id)
            .join(Team, Team.id == Series.team_id)
            .where(Team.is_personal.is_(False), Series.calendar_recurring_id.is_not(None))
            .order_by(Series.id)
        )
    )
    for series_id in ids:
        row = session.scalars(
            select(Series).where(Series.id == series_id).with_for_update()
        ).first()
        if row is None:
            continue
        report.cards_looked_at += 1
        reason = series_rules.non_empty_reason(session, row)
        if reason is None:
            session.delete(row)
            session.flush()
            report.cards_removed += 1
            continue
        report.cards_kept += 1
        if reason == "meeting":
            report.kept_with_meeting += 1
        elif reason == "lines":
            report.kept_with_lines += 1
        elif reason == "project":
            report.kept_with_project += 1
        else:
            report.kept_with_mail += 1
    if apply:
        session.commit()
    else:
        session.rollback()
    return report
