"""The one-hour reminder's "· Sprint N opens" text and its grey summary box (`app/mail_summary.py`).

Each rule is checked in the cases where it shows and where it does not. The box's sentences are
asserted on the HTML the recipient gets and on the plain-text part. Visibility is checked with
two readers of the same series: the owner, who made the meeting, and Daniel, who sees only the
tasks assigned to him.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import mail_layout, series_mail
from app.models import (
    CalendarConnection,
    Milestone,
    Project,
    SeriesReminder,
    Sprint,
    Team,
    UserSettings,
)
from tests.test_series import (  # noqa: F401  (fixtures and helpers used by name)
    DANIEL_EMAIL,
    NOW,
    anon,
    anon_client,
    api,
    app,
    calendar_event,
    client,
    daniel,
    fake_openai,
    fake_vexa,
    fixed_clock,
    meeting_in,
    outsider,
    owner,
    ready,
    share,
    sign_in,
    task_in,
    template,
    weekly,
)
from tests.test_series_mail import (  # noqa: F401
    OWNER_EMAIL,
    SETTINGS,
    add_line,
    busy,
    due,
    queued,
)
from tests.test_series_mail import build as build_mail

TODAY = NOW.date()  # Wed 4 Nov 2026
BOX = "padding:12px 14px;background:#F6F7F6;border-radius:10px;font-size:13px;color:#4A524E"
JOIN = "The note taker will join this meeting automatically."


def make_sprint(session, team_id, number, start, end, *, closed_at=None, project_id=None):
    row = Sprint(
        team_id=team_id,
        project_id=project_id,
        number=number,
        start_date=start,
        end_date=end,
        closed_at=closed_at,
    )
    session.add(row)
    session.commit()
    return row


def put(session, task, sprint, *, status=None, carried=False):
    task.sprint_id = sprint.id
    task.carried = carried
    if status:
        task.status = status
    session.commit()
    return task


def preferences(session, user, **fields):
    row = session.get(UserSettings, user.id)
    if row is None:
        row = UserSettings(user_id=user.id)
        session.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    session.commit()
    return row


def mail_of(session, to, starts=None, *, minutes=55):
    """The one-hour mail for `to` about the occurrence at `starts` (default: the `due` event)."""
    starts = starts or NOW + timedelta(minutes=55)
    when = starts - timedelta(minutes=minutes)
    series_mail.queue_due_reminders(session, when)
    row = next(
        r
        for r in queued(session, "series_reminder")
        if r.to_email == to
        and session.get(SeriesReminder, r.payload["reminder_id"]).starts_at == starts
        and session.get(SeriesReminder, r.payload["reminder_id"]).kind == "1h"
    )
    return build_mail(session, row, when)


def box(message):
    """The text inside the grey box, or None when the mail has none."""
    marker = BOX + ';line-height:1.55">'
    if marker not in message.html:
        return None
    return message.html.split(marker)[1].split("</div>")[0]


# ---- the layout block ----


def test_the_summary_box_is_the_designers_grey_box_and_is_escaped():
    html = mail_layout.render_html("t", [mail_layout.SummaryBox("A <b>& B")], "f")
    assert f'<div style="{BOX};line-height:1.55">A &lt;b&gt;&amp; B</div>' in html, (
        "padding 12px 14px, #F6F7F6, radius 10px, 13px, #4A524E, line-height 1.55 (Emails.dc.html)"
    )
    assert "A <b>& B" in mail_layout.render_text([mail_layout.SummaryBox("A <b>& B")], "f")


# ---- rule 1: "· Sprint N opens" ----


@pytest.fixture
def sprints_on(session, weekly):
    """Sprint 12 ended Tue 3 Nov and closed; sprint 13 starts today, Wed 4 Nov."""
    team = weekly.team_id
    closed = make_sprint(
        session, team, 12, date(2026, 10, 21), date(2026, 11, 3), closed_at=NOW - timedelta(hours=9)
    )
    opening = make_sprint(session, team, 13, date(2026, 11, 4), date(2026, 11, 17))
    return closed, opening


def test_the_meta_line_names_the_sprint_that_opens_on_the_meeting_day(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    weekly.platform = "google_meet"
    session.commit()
    for to in (OWNER_EMAIL, DANIEL_EMAIL):
        message = mail_of(session, to)
        assert "UTC · Google Meet · Sprint 13 opens</div>" in message.html
        assert "UTC · Google Meet · Sprint 13 opens" in message.text
    weekly.platform = None  # no platform known: the sprint text follows the time
    session.commit()
    assert "11:55 UTC · Sprint 13 opens</div>" in mail_of(session, OWNER_EMAIL).html


def test_no_sprint_line_when_the_team_has_no_sprints(session, owner, daniel, weekly, busy, due):
    message = mail_of(session, OWNER_EMAIL)
    assert "opens" not in message.text and "Sprint" not in message.html


def test_no_sprint_line_when_no_sprint_starts_that_day(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    closed, opening = sprints_on
    opening.start_date, opening.end_date = date(2026, 11, 2), date(2026, 11, 15)
    closed.end_date = date(2026, 11, 1)
    session.commit()
    assert "opens" not in mail_of(session, OWNER_EMAIL).text


def test_a_sprint_that_starts_on_a_saturday_opens_on_the_first_working_day(
    session, owner, daniel, weekly, busy, due
):
    team = weekly.team_id
    make_sprint(session, team, 12, date(2026, 10, 24), date(2026, 11, 6), closed_at=NOW)
    make_sprint(session, team, 13, date(2026, 11, 7), date(2026, 11, 20))  # starts Saturday
    monday = NOW.replace(day=9, hour=9)
    calendar_event(session, owner, "rid-1", monday)
    assert "· Sprint 13 opens" in mail_of(session, OWNER_EMAIL, monday).text
    # not on the Saturday's own working neighbours: the Tuesday is a later day of the sprint
    tuesday = NOW.replace(day=10, hour=9)
    calendar_event(session, owner, "rid-1", tuesday)
    assert "opens" not in mail_of(session, OWNER_EMAIL, tuesday).text


def test_a_sprint_closed_early_opens_only_on_the_day_it_started(
    session, owner, daniel, weekly, busy, due
):
    team = weekly.team_id
    # closed early: the next sprint started on Mon 9 Nov, long before the old end date (Fri 13)
    make_sprint(session, team, 12, date(2026, 10, 26), date(2026, 11, 13), closed_at=NOW)
    make_sprint(session, team, 13, date(2026, 11, 9), date(2026, 11, 22))
    tuesday = NOW.replace(day=10, hour=9)
    calendar_event(session, owner, "rid-1", tuesday)
    assert "opens" not in mail_of(session, OWNER_EMAIL, tuesday).text
    monday = NOW.replace(day=9, hour=9)
    calendar_event(session, owner, "rid-1", monday)
    assert "· Sprint 13 opens" in mail_of(session, OWNER_EMAIL, monday).text


def test_no_sprint_line_when_an_earlier_meeting_already_opened_the_sprint(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    calendar_event(session, owner, "rid-1", NOW - timedelta(hours=4))  # 06:00 the same day
    assert "opens" not in mail_of(session, OWNER_EMAIL).text


def test_no_sprint_line_when_a_meeting_of_the_series_was_held_since_the_sprint_began(
    session, owner, daniel, weekly, busy, due, sprints_on, template
):
    meeting_in(session, template, weekly, when=NOW - timedelta(hours=5))  # the owner's own
    assert "opens" not in mail_of(session, OWNER_EMAIL).text


def test_a_projects_own_sprints_are_the_ones_named(session, owner, daniel, weekly, busy, due):
    team = weekly.team_id
    project = Project(team_id=team, name="Client Portal", color="#0E6B5E", sprint_length="week7")
    session.add(project)
    session.commit()
    weekly.project_id = project.id
    session.commit()
    make_sprint(session, team, 40, date(2026, 11, 4), date(2026, 11, 10))  # the team's, not ours
    assert "opens" not in mail_of(session, OWNER_EMAIL).text
    make_sprint(session, team, 3, date(2026, 11, 4), date(2026, 11, 10), project_id=project.id)
    assert "· Sprint 3 opens" in mail_of(session, OWNER_EMAIL).text


# ---- rule 2: "Sprint 12 closed with 7 of 11 tasks done." ----


def fill_sprint(session, weekly, busy, closed, opening, daniel, *, done=3, carried=1):
    """`done` done tasks that stayed in the closed sprint and `carried` moved on from it. The
    first two tasks are the `busy` fixture's: Daniel's "Finish checkout" and one nobody owns."""
    mine = [t for t in session.scalars(select(task_cls()).order_by(task_cls().id)) if t.meeting_id]
    extra = [
        task_in(session, weekly.team_id, busy, f"Extra {i}")
        for i in range(done + carried - len(mine))
    ]
    everything = mine + extra
    for task in everything[:done]:
        put(session, task, closed, status="done")
    for task in everything[done : done + carried]:
        put(session, task, opening, carried=True)
    return everything


def task_cls():
    from app.models import Task

    return Task


def test_the_box_says_how_the_last_closed_sprint_went(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    closed, opening = sprints_on
    fill_sprint(session, weekly, busy, closed, opening, daniel, done=3, carried=1)
    message = mail_of(session, OWNER_EMAIL)
    assert box(message) == f"Sprint 12 closed with 3 of 4 tasks done. {JOIN}"
    assert f"\nSprint 12 closed with 3 of 4 tasks done. {JOIN}\n" in message.text


@pytest.mark.parametrize(
    ("days", "shown"), [(13, True), (14, True), (15, False)], ids=["13 days", "14 days", "15 days"]
)
def test_a_sprint_is_mentioned_for_fourteen_days_after_it_closed(
    session, owner, daniel, weekly, busy, due, sprints_on, days, shown
):
    closed, opening = sprints_on
    closed.closed_at = NOW - timedelta(days=days)
    session.commit()
    fill_sprint(session, weekly, busy, closed, opening, daniel, done=2, carried=0)
    text = mail_of(session, OWNER_EMAIL).text
    assert ("Sprint 12 closed with 2 of 2 tasks done." in text) is shown


def test_the_most_recently_closed_sprint_is_the_one_mentioned(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    closed, opening = sprints_on
    older = make_sprint(
        session, weekly.team_id, 11, date(2026, 10, 7), date(2026, 10, 20),
        closed_at=NOW - timedelta(days=3),
    )  # fmt: skip
    fill_sprint(session, weekly, busy, closed, opening, daniel, done=2, carried=0)
    assert "Sprint 12 closed with 2 of 2" in mail_of(session, OWNER_EMAIL).text
    assert older.number == 11 and "Sprint 11" not in mail_of(session, OWNER_EMAIL).text


def test_a_closed_sprint_with_no_tasks_adds_nothing(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    message = mail_of(session, DANIEL_EMAIL)  # sprint 12 holds nothing at all
    assert box(message) is None and "closed with" not in message.text


def test_a_teammate_is_told_only_about_the_tasks_they_may_see(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    add_line(session, weekly, owner)  # something to say, so the mail is sent at all
    closed, opening = sprints_on
    # Four tasks done in the sprint: Daniel's own, and three in the owner's private meeting.
    fill_sprint(session, weekly, busy, closed, opening, daniel, done=4, carried=0)
    assert "Sprint 12 closed with 4 of 4 tasks done." in mail_of(session, OWNER_EMAIL).text
    theirs = mail_of(session, DANIEL_EMAIL)
    assert box(theirs) == "Sprint 12 closed with 1 of 1 task done."  # Finish checkout only
    for hidden in ("4 of 4", "Unassigned secret", "Extra"):
        assert hidden not in theirs.text and hidden not in theirs.html


def test_a_teammate_who_can_see_none_of_them_gets_no_sentence(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    add_line(session, weekly, owner)  # something to say, so the mail is sent at all
    closed, opening = sprints_on
    for task in fill_sprint(session, weekly, busy, closed, opening, daniel, done=2, carried=1):
        if task.assignee_user_id:
            task.assignee_user_id = None  # nothing of the sprint is Daniel's any more
    session.commit()
    message = mail_of(session, DANIEL_EMAIL)
    assert box(message) is None and "closed with" not in message.text


def test_unfinished_tasks_in_the_next_sprint_that_were_not_carried_do_not_count(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    closed, opening = sprints_on
    tasks = fill_sprint(session, weekly, busy, closed, opening, daniel, done=2, carried=1)
    planned = task_in(session, weekly.team_id, busy, "Planned for sprint 13")
    put(session, planned, opening)  # planned there, never carried: not part of sprint 12
    assert planned.id not in {t.id for t in tasks}
    assert "Sprint 12 closed with 2 of 3 tasks done." in mail_of(session, OWNER_EMAIL).text


# ---- rule 3: "{name} is in N days." ----


@pytest.fixture
def project(session, weekly):
    row = Project(team_id=weekly.team_id, name="Client Portal", color="#0E6B5E")
    session.add(row)
    session.commit()
    weekly.project_id = row.id
    session.commit()
    return row


def milestone(session, project, name, due_on, *, confirmed=True, **fields):
    row = Milestone(
        team_id=project.team_id,
        project_id=project.id,
        name=name,
        due_date=due_on,
        confirmed_at=NOW if confirmed else None,
        **fields,
    )
    session.add(row)
    session.commit()
    return row


def test_the_box_counts_the_days_to_the_next_confirmed_milestone(
    session, owner, daniel, weekly, busy, due, project
):
    milestone(session, project, "Client Portal launch", TODAY + timedelta(days=29))
    message = mail_of(session, DANIEL_EMAIL)
    assert box(message) == "Client Portal launch is in 29 days."
    assert "\nClient Portal launch is in 29 days.\n" in message.text


@pytest.mark.parametrize(
    ("offset", "sentence"),
    [(-1, None), (0, None), (1, "Beta is in 1 day.")],
    ids=["yesterday", "today", "tomorrow"],
)
def test_a_milestone_due_today_or_past_is_never_mentioned(
    session, owner, daniel, weekly, busy, due, project, offset, sentence
):
    milestone(session, project, "Beta", TODAY + timedelta(days=offset))
    assert box(mail_of(session, DANIEL_EMAIL)) == sentence


def test_an_unconfirmed_milestone_is_not_mentioned(
    session, owner, daniel, weekly, busy, due, project
):
    milestone(session, project, "Proposed one", TODAY + timedelta(days=5), confirmed=False)
    assert box(mail_of(session, DANIEL_EMAIL)) is None
    milestone(session, project, "Confirmed one", TODAY + timedelta(days=9))
    assert box(mail_of(session, DANIEL_EMAIL)) == "Confirmed one is in 9 days."


def test_the_nearest_future_milestone_wins_and_other_projects_are_ignored(
    session, owner, daniel, weekly, busy, due, project
):
    other = Project(team_id=weekly.team_id, name="Other", color="#123456")
    session.add(other)
    session.commit()
    milestone(session, other, "Other project's", TODAY + timedelta(days=2))
    milestone(session, project, "Far", TODAY + timedelta(days=40))
    milestone(session, project, "Near", TODAY + timedelta(days=12))
    milestone(session, project, "Gone", TODAY - timedelta(days=3))
    assert box(mail_of(session, DANIEL_EMAIL)) == "Near is in 12 days."


def test_a_series_with_no_project_has_no_milestone_sentence(
    session, owner, daniel, weekly, busy, due, project
):
    milestone(session, project, "Beta", TODAY + timedelta(days=5))
    weekly.project_id = None
    session.commit()
    assert box(mail_of(session, DANIEL_EMAIL)) is None


def test_the_days_are_counted_from_the_recipients_own_today(
    session, owner, daniel, weekly, busy, due, project
):
    milestone(session, project, "Beta", date(2026, 11, 5))  # tomorrow in UTC
    preferences(session, daniel, time_zone="Pacific/Kiritimati")  # UTC+14: already Thu 5 Nov
    assert box(mail_of(session, OWNER_EMAIL)) == f"Beta is in 1 day. {JOIN}"
    assert box(mail_of(session, DANIEL_EMAIL)) is None


def test_a_milestone_name_is_cleaned_and_escaped(
    session, owner, daniel, weekly, busy, due, project
):
    milestone(session, project, "<b>Launch</b> & Co", TODAY + timedelta(days=3))
    message = mail_of(session, DANIEL_EMAIL)
    assert "&lt;b&gt;Launch&lt;/b&gt; &amp; Co is in 3 days." in message.html
    assert "<b>Launch" not in message.html
    milestone(session, project, "See https://evil.test/x now", TODAY + timedelta(days=1))
    again = mail_of(session, DANIEL_EMAIL)
    assert "evil.test" not in again.html and "evil.test" not in again.text
    assert "[link removed]" in again.text


# ---- rule 4: "The note taker will join this meeting automatically." ----


def test_the_join_sentence_is_for_a_person_the_note_taker_will_join(
    session, owner, daniel, weekly, busy, due
):
    message = mail_of(session, OWNER_EMAIL)  # connected, rule "all", their own event
    assert box(message) == JOIN and f"\n{JOIN}\n" in message.text


def test_not_for_a_teammate_with_no_calendar_event_of_their_own(
    session, owner, daniel, weekly, busy, due
):
    assert box(mail_of(session, DANIEL_EMAIL)) is None  # the event is the owner's, not Daniel's


@pytest.mark.parametrize(
    ("setting", "fields", "event_fields"),
    [
        ("rule none", {"auto_join_rule": "none"}, {}),
        ("rule organizer, not the organiser", {"auto_join_rule": "organizer"}, {}),
        ("switched off for this meeting", {}, {"override": False}),
        ("declined", {}, {"self_response": "declined"}),
        ("no link", {}, {"platform": None}),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_not_when_their_own_rule_would_not_join(
    session, owner, daniel, weekly, busy, due, setting, fields, event_fields
):
    preferences(session, owner, **fields)
    for key, value in event_fields.items():
        setattr(due, key, value)
    session.commit()
    assert box(mail_of(session, OWNER_EMAIL)) is None, setting


def test_the_organiser_rule_and_an_override_that_switches_it_on(
    session, owner, daniel, weekly, busy, due
):
    preferences(session, owner, auto_join_rule="organizer")
    due.organizer_self = True
    session.commit()
    assert box(mail_of(session, OWNER_EMAIL)) == JOIN
    due.organizer_self, due.override = False, True  # not theirs, but they switched it on
    session.commit()
    assert box(mail_of(session, OWNER_EMAIL)) == JOIN


@pytest.mark.parametrize(
    ("status", "shown"),
    [
        ("connected", True),
        ("degraded", True),
        ("needs_reconnect", False),
        ("disconnected", False),
    ],
)
def test_only_while_their_calendar_is_connected(
    session, owner, daniel, weekly, busy, due, status, shown
):
    conn = session.scalars(
        select(CalendarConnection).where(CalendarConnection.user_id == owner.id)
    ).one()
    conn.status = status
    session.commit()
    assert (box(mail_of(session, OWNER_EMAIL)) == JOIN) is shown


def test_a_teammate_whose_bot_will_not_join_never_hears_it(
    session, owner, daniel, weekly, busy, due
):
    calendar_event(session, daniel, "rid-1", due.starts_at)  # Daniel has the event too
    assert box(mail_of(session, DANIEL_EMAIL)) == JOIN
    preferences(session, daniel, auto_join_rule="none")
    assert box(mail_of(session, DANIEL_EMAIL)) is None
    assert box(mail_of(session, OWNER_EMAIL)) == JOIN  # the owner's own rule is theirs alone


# ---- the box as a whole ----


def test_the_three_sentences_come_in_order_in_one_box(
    session, owner, daniel, weekly, busy, due, sprints_on, project
):
    closed, opening = sprints_on
    fill_sprint(session, weekly, busy, closed, opening, daniel, done=3, carried=1)
    milestone(session, project, "Client Portal launch", TODAY + timedelta(days=29))
    message = mail_of(session, OWNER_EMAIL)
    assert box(message) == (
        f"Sprint 12 closed with 3 of 4 tasks done. Client Portal launch is in 29 days. {JOIN}"
    )
    assert message.html.count(BOX) == 1
    assert "Sprint 13 opens" in message.html


def test_the_box_is_left_out_when_no_sentence_applies(session, owner, daniel, weekly, busy, due):
    message = mail_of(session, DANIEL_EMAIL)
    assert BOX not in message.html
    assert "closed with" not in message.text and " is in " not in message.text


def test_the_thirty_minute_mail_has_no_box_and_no_sprint_line(
    session, owner, daniel, weekly, busy, due, sprints_on, project
):
    add_line(session, weekly, owner)  # something to say, so the mail is sent at all
    closed, opening = sprints_on
    fill_sprint(session, weekly, busy, closed, opening, daniel, done=3, carried=1)
    milestone(session, project, "Client Portal launch", TODAY + timedelta(days=29))
    when = NOW + timedelta(minutes=26)
    series_mail.queue_due_reminders(session, when)
    rows = [
        r
        for r in queued(session, "series_reminder")
        if session.get(SeriesReminder, r.payload["reminder_id"]).kind == "30m"
    ]
    assert rows
    for row in rows:
        message = build_mail(session, row, when)
        assert BOX not in message.html
        for text in ("closed with", "is in 29 days", JOIN, "opens"):
            assert text not in message.text, text


def test_a_mail_without_sprints_or_milestones_is_unchanged(
    session, owner, daniel, weekly, busy, due
):
    team = session.get(Team, weekly.team_id)
    assert team is not None
    message = mail_of(session, DANIEL_EMAIL)
    assert "11:55 UTC</div>" in message.html and " · " not in message.text.split("UTC")[1][:4]
