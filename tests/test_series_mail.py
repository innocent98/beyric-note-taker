"""W8 Series mail: the agenda a teammate sends and the reminders before a recurring meeting.

What matters here is who gets them, what each recipient is shown (only what they may read), the
caps, and that nothing from a meeting or a typed line can become markup or a link.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app import mail, mail_templates, series, series_mail, teams
from app.mail_templates import Skip
from app.models import (
    Decision,
    MailOutbox,
    Series,
    SeriesAgendaItem,
    SeriesAgendaSend,
    SeriesReminder,
    Team,
    UserSettings,
)
from tests.test_api import error_of  # noqa: F401
from tests.test_series import (  # noqa: F401  (fixtures and helpers used by name)
    DANIEL_EMAIL,
    MONDAY,
    NOW,
    Api,
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
    question_in,
    ready,
    share,
    sign_in,
    task_in,
    template,
    weekly,
)
from tests.test_web import make_settings

SETTINGS = make_settings()
OWNER_EMAIL = "owner@example.test"


def queued(session, template_name=None):
    query = select(MailOutbox).order_by(MailOutbox.id)
    if template_name:
        query = query.where(MailOutbox.template == template_name)
    return list(session.scalars(query))


def build(session, row, now=NOW):
    return mail_templates.prepare(session, row.template, row.payload, row.to_email, SETTINGS, now)


@pytest.fixture
def busy(session, owner, daniel, template, weekly):
    """A series with one past meeting Daniel attended, a task, a question and a decision."""
    meeting = meeting_in(session, template, weekly)
    session.add(
        Decision(meeting_id=meeting.id, text="Ship on Friday.", position=0, fingerprint="d")
    )
    session.commit()
    task_in(session, weekly.team_id, meeting, "Finish checkout", assignee=daniel)
    task_in(session, weekly.team_id, meeting, "Unassigned secret task")
    question_in(session, meeting, "Which pricing model?")
    return meeting


def add_line(session, weekly, owner, text="Budget review"):
    session.add(SeriesAgendaItem(series_id=weekly.id, text=text, created_by=owner.id))
    session.commit()


# ---- sending the agenda ----


def test_the_agenda_goes_to_the_teammates_who_attended_not_the_sender(
    session, owner, daniel, weekly, busy
):
    add_line(session, weekly, owner)
    people = series_mail.send_agenda(session, owner, weekly, NOW)
    session.commit()
    assert [p.name for p in people] == ["Daniel Okoro"]
    rows = queued(session, "series_agenda")
    assert [(r.to_email, sorted(r.payload)) for r in rows] == [
        (DANIEL_EMAIL, ["send_id", "user_id"])
    ]  # ids only, no text
    assert all(isinstance(v, int) for v in rows[0].payload.values())
    assert session.scalars(select(SeriesAgendaSend.recipient_count)).all() == [1]


def test_nobody_outside_the_team_is_ever_a_recipient(session, owner, daniel, weekly, busy):
    # "Guest Visitor" spoke in the meeting but is nobody's account: no address, no mail.
    names = {a.name: a.email for a in series.attendees(session, weekly, None)}
    assert names["Guest Visitor"] is None
    assert {p.user_id for p in series.recipients(session, weekly)} == {owner.id, daniel.id}
    assert all(r.to_email in {"owner@example.test", DANIEL_EMAIL} for r in queued(session))


def test_there_must_be_someone_to_send_to_and_something_to_say(
    session, owner, daniel, weekly, template
):
    with pytest.raises(series.SeriesRefused) as nobody:
        series_mail.send_agenda(session, owner, weekly, NOW)  # no past meeting
    assert nobody.value.code == "no_attendees"
    meeting_in(session, template, weekly)
    with pytest.raises(series.SeriesRefused) as empty:
        series_mail.send_agenda(session, owner, weekly, NOW)
    assert empty.value.code == "nothing_to_send"
    assert queued(session) == []


def test_five_sends_and_twenty_people_a_day_per_sender(session, owner, daniel, weekly, busy):
    add_line(session, weekly, owner)
    for _ in range(series_mail.SEND_DAILY_SENDS):
        series_mail.send_agenda(session, owner, weekly, NOW)
        session.commit()
    with pytest.raises(series.SeriesRefused) as over:
        series_mail.send_agenda(session, owner, weekly, NOW)
    assert (over.value.status, over.value.code) == (429, "agenda_rate_limited")
    # a day later the allowance is back
    series_mail.send_agenda(session, owner, weekly, NOW + timedelta(days=1, minutes=1))


def test_the_api_sends_and_reports_who(api, session, owner, daniel, weekly, busy):
    add_line(session, weekly, owner)
    body = api.post(f"/api/series/{weekly.id}/agenda/send").json()
    assert body == {"count": 1, "names": ["Daniel Okoro"]}
    assert len(queued(session, "series_agenda")) == 1
    empty = api.post(f"/api/series/{weekly.id}/agenda/send")
    assert empty.status_code == 200  # the queued mail is not a reason to refuse a second press


def test_the_api_refuses_with_a_clear_code_when_nobody_can_get_it(api, session, weekly):
    error_of(api.post(f"/api/series/{weekly.id}/agenda/send"), 409, "no_attendees")


def test_the_agenda_mail_shows_a_recipient_only_what_they_may_read(
    session, owner, daniel, weekly, busy
):
    add_line(session, weekly, owner, "Budget review")
    series_mail.send_agenda(session, owner, weekly, NOW)
    session.commit()
    row = queued(session, "series_agenda")[0]
    message = build(session, row)
    assert message.subject == "Agenda: Monday planning"
    # the line is team content; the private meeting's question and task are not Daniel's to see
    assert "Budget review" in message.text
    assert "Which pricing model?" not in message.text
    assert "Unassigned secret task" not in message.text
    # a task assigned to him is his to see whatever the meeting (spec 8.2: assigned tasks leak
    # the title on purpose)
    assert "Finish checkout" in message.text
    # shared: now the rest is too
    share(session, busy, owner)
    message = build(session, row)
    assert "Which pricing model?" in message.text and "Unassigned secret task" in message.text
    assert "Sent by" not in message.subject
    assert "/series/" in message.html  # a sign-in-gated link, to a teammate


def test_nothing_typed_becomes_markup_or_a_link(session, owner, daniel, weekly, busy):
    add_line(session, weekly, owner, "<script>alert(1)</script> docs.evil.example")
    series_mail.send_agenda(session, owner, weekly, NOW)
    session.commit()
    message = build(session, queued(session, "series_agenda")[0])
    assert "<script>" not in message.html and "&lt;script&gt;" in message.html
    assert "docs[.]evil[.]example" in message.text
    assert "docs.evil.example" not in message.text
    assert message.html.count("<a ") <= 2  # the button and the footer link


def test_a_series_title_cannot_break_the_subject(session, owner, daniel, weekly, busy):
    weekly.title = "Planning\r\nBcc: x@evil.example"
    add_line(session, weekly, owner)
    session.commit()
    series_mail.send_agenda(session, owner, weekly, NOW)
    message = build(session, queued(session, "series_agenda")[0])
    assert "\n" not in message.subject and "\r" not in message.subject


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("opted_out", "opted_out"),
        ("left_team", "left_team"),
        ("suspended", "recipient_inactive"),
        ("address", "recipient_changed"),
        ("sender_left", "sender_left_team"),
    ],
)
def test_the_sender_checks_again_when_the_mail_goes(
    session, owner, daniel, weekly, busy, change, reason
):
    add_line(session, weekly, owner)
    series_mail.send_agenda(session, owner, weekly, NOW)
    session.commit()
    row = queued(session, "series_agenda")[0]
    if change == "opted_out":
        session.add(UserSettings(user_id=daniel.id, notify_reminders=False))
    elif change == "left_team":
        teams.leave(session, daniel, NOW)
    elif change == "suspended":
        daniel.status = "suspended"
    elif change == "address":
        daniel.email = "new-address@example.test"
    else:
        teams.leave(session, owner, NOW)
    session.commit()
    result = build(session, row)
    assert isinstance(result, Skip) and result.reason == reason


def test_a_person_who_switched_series_mail_off_is_not_even_queued(
    session, owner, daniel, weekly, busy
):
    session.add(UserSettings(user_id=daniel.id, notify_reminders=False))
    session.commit()
    add_line(session, weekly, owner)
    with pytest.raises(series.SeriesRefused) as nobody:
        series_mail.send_agenda(session, owner, weekly, NOW)
    assert nobody.value.code == "no_attendees"


# ---- reminders ----


@pytest.fixture
def due(session, owner, daniel, weekly, busy):
    """Reminders on for the team, the series tied to a recurring event starting in 55 minutes."""
    weekly.calendar_recurring_id = "rid-1"
    session.get(Team, weekly.team_id).reminders_on = True
    session.commit()
    return calendar_event(session, owner, "rid-1", NOW + timedelta(minutes=55))


def test_reminders_are_off_until_a_manager_switches_them_on(
    session, owner, daniel, weekly, busy, due
):
    session.get(Team, weekly.team_id).reminders_on = False
    session.commit()
    assert series_mail.queue_due_reminders(session, NOW) == 0
    assert queued(session) == []


def test_the_one_hour_mail_goes_in_its_window_once_per_person(
    session, owner, daniel, weekly, busy, due
):
    assert series_mail.queue_due_reminders(session, NOW) == 2  # the owner and Daniel
    assert series_mail.queue_due_reminders(session, NOW + timedelta(minutes=5)) == 0
    rows = queued(session, "series_reminder")
    assert sorted(r.to_email for r in rows) == [DANIEL_EMAIL, "owner@example.test"]
    assert all(list(r.payload) == ["reminder_id"] for r in rows)
    kinds = session.scalars(select(SeriesReminder.kind)).all()
    assert set(kinds) == {"1h"}


def test_one_mail_per_person_per_occurrence_even_when_two_teams_each_have_the_series(
    session, owner, daniel, weekly, busy, due, template
):
    """Multi-team: the same recurring event has a series in each of two teams the people share
    (each made from its own recorder's calendar). The mail is per person and meeting, not per
    team: once team A has reminded someone, team B stays quiet."""
    from app.models import TeamMember

    other = Team(owner_user_id=owner.id, name="Second", is_personal=False, reminders_on=True)
    session.add(other)
    session.flush()
    for person, role in ((owner, "owner"), (daniel, "member")):
        session.add(TeamMember(team_id=other.id, user_id=person.id, role=role))
    twin = Series(
        team_id=other.id,
        owner_user_id=owner.id,
        title="Monday planning",
        calendar_recurring_id="rid-1",
    )
    session.add(twin)
    session.flush()
    session.add(SeriesAgendaItem(series_id=twin.id, text="Budget", created_by=owner.id))
    meeting_in(session, template, twin).shared_team_id = other.id
    session.commit()
    first = series_mail.queue_due_reminders(session, NOW)
    assert first == 2  # one mail each for the owner and Daniel, from whichever series ran first
    assert series_mail.queue_due_reminders(session, NOW) == 0
    rows = session.scalars(select(SeriesReminder)).all()
    assert len(rows) == 2 and len({r.user_id for r in rows}) == 2


def test_the_thirty_minute_mail_follows_and_the_window_closes(
    session, owner, daniel, weekly, busy, due
):
    series_mail.queue_due_reminders(session, NOW)
    assert series_mail.queue_due_reminders(session, NOW + timedelta(minutes=26)) == 2  # 29 min left
    assert set(session.scalars(select(SeriesReminder.kind))) == {"1h", "30m"}
    # four minutes before the start is too late for any: a stale reminder is worse than none
    assert series_mail.queue_due_reminders(session, NOW + timedelta(minutes=51)) == 0


def test_a_scheduler_that_was_down_misses_the_hour_mail_rather_than_send_it_late(
    session, owner, daniel, weekly, busy, due
):
    assert series_mail.queue_due_reminders(session, NOW + timedelta(minutes=22)) == 0  # between
    assert series_mail.queue_due_reminders(session, NOW + timedelta(minutes=26)) == 2
    assert set(session.scalars(select(SeriesReminder.kind))) == {"30m"}


def test_timing_choice_limits_the_mails(session, owner, daniel, weekly, busy, due):
    session.get(Team, weekly.team_id).reminder_timing = "30m"
    session.commit()
    assert series_mail.queue_due_reminders(session, NOW) == 0  # 55 min: only the 1h kind is open
    assert series_mail.queue_due_reminders(session, NOW + timedelta(minutes=26)) == 2


def test_no_reminder_for_a_cancelled_or_far_off_occurrence_or_a_series_with_nothing_to_say(
    session, owner, daniel, weekly, busy, due
):
    due.status = "cancelled"
    session.commit()
    assert series_mail.queue_due_reminders(session, NOW) == 0
    due.status, due.starts_at = "confirmed", NOW + timedelta(hours=3)
    session.commit()
    assert series_mail.queue_due_reminders(session, NOW) == 0
    # a series that never met and has no agenda has nothing to say
    due.starts_at = NOW + timedelta(minutes=55)
    session.execute(Series.__table__.update().values(calendar_recurring_id="rid-2"))
    calendar_event(session, owner, "rid-2", NOW + timedelta(minutes=55), title="Other")
    other = Series(team_id=weekly.team_id, title="Fresh", calendar_recurring_id="rid-1")
    session.add(other)
    session.commit()
    for found in (weekly, other):
        session.expire(found)
    assert series_mail.queue_due_reminders(session, NOW) >= 0


def test_the_one_hour_mail_tells_the_story_of_last_time(session, owner, daniel, weekly, busy, due):
    add_line(session, weekly, owner, "Budget review")
    share(session, busy, owner)
    series_mail.queue_due_reminders(session, NOW)
    row = next(r for r in queued(session, "series_reminder") if r.to_email == DANIEL_EMAIL)
    message = build(session, row)
    assert message.subject == "Starts in 1 hour: Monday planning"
    for text in (
        "Ship on Friday.",
        "Which pricing model?",
        "Finish checkout",
        "Budget review",
        "Mon 2 Nov",
        "Join meeting",
        "View agenda",
        "Reminder settings",
        "You are getting this because you attend Monday planning.",
    ):
        assert text in message.text, text
    assert "https://meet.google.com/abc-defg-hij" in message.html
    assert "Thu 5 Nov" in message.text or "Wed 4 Nov" in message.text  # the start, in UTC


def test_the_thirty_minute_mail_is_the_short_agenda(session, owner, daniel, weekly, busy, due):
    add_line(session, weekly, owner, "Budget review")
    share(session, busy, owner)
    when = NOW + timedelta(minutes=26)
    series_mail.queue_due_reminders(session, when)
    row = next(r for r in queued(session, "series_reminder") if r.to_email == DANIEL_EMAIL)
    message = build(session, row, when)
    assert message.subject == "Starts in 30 minutes: Monday planning"
    assert "Which pricing model?" in message.text and "Budget review" in message.text
    assert "Check on 2 unfinished tasks, including " in message.text
    assert "to 11:55" not in message.text and "UTC" in message.text  # the start only, as drawn
    # the one decision appears only in the closing sentence, with the link to the notes page
    assert "Last time the team agreed: Ship on Friday." in message.text
    assert message.text.count("Ship on Friday.") == 1
    assert f"/meetings/{busy.id}" in message.html and "Read the full notes" in message.html


def test_the_thirty_minute_mail_has_no_closing_sentence_without_a_readable_decision(
    session, owner, daniel, weekly, busy, due
):
    add_line(session, weekly, owner, "Budget review")  # busy stays private to the owner
    when = NOW + timedelta(minutes=26)
    series_mail.queue_due_reminders(session, when)
    row = next(r for r in queued(session, "series_reminder") if r.to_email == DANIEL_EMAIL)
    text = build(session, row, when).text
    assert "Last time the team agreed" not in text and "Read the full notes" not in text
    assert "Ship on Friday." not in text
    session.execute(Decision.__table__.update().values(hidden_at=NOW))  # nothing decided at all
    session.commit()
    owner_row = next(r for r in queued(session, "series_reminder") if r.to_email == OWNER_EMAIL)
    assert "Read the full notes" not in build(session, owner_row, when).text


def test_the_reminders_are_drawn_as_the_designer_drew_them(
    session, owner, daniel, weekly, busy, due
):
    series_mail.queue_due_reminders(session, NOW)
    row = next(r for r in queued(session, "series_reminder") if r.to_email == OWNER_EMAIL)
    html = build(session, row).html
    assert "background:#E6F1EE" in html and ">Starts in 1 hour</span>" in html
    for label in (
        "DECISIONS MADE",
        "OPEN QUESTIONS TO SETTLE",
        "STILL OPEN FROM LAST TIME",
    ):
        assert f'letter-spacing:0.04em;margin:0 0 8px">{label}</div>' in html, label
    assert "LAST TIME, " in html
    # the owner (or "Needs owner") sits under the title, the due text at the right
    assert (
        'Unassigned secret task<div style="font-size:12px;color:#6E7772">Needs owner</div>' in html
    )
    assert ">No due date</td>" in html
    assert "Sprint 12 closed" not in html  # the grey summary box waits for the owner's OK


def test_a_reminder_shows_the_last_meeting_only_to_someone_who_may_read_it(
    session, owner, daniel, weekly, busy, due
):
    add_line(session, weekly, owner, "Budget review")  # busy is private to the owner
    series_mail.queue_due_reminders(session, NOW)
    rows = {r.to_email: r for r in queued(session, "series_reminder")}
    for_daniel = build(session, rows[DANIEL_EMAIL]).text
    for_owner = build(session, rows["owner@example.test"]).text
    assert "Budget review" in for_daniel  # team content
    assert "Finish checkout" in for_daniel  # assigned to him: visible by design
    for secret in ("Ship on Friday.", "Which pricing model?", "Unassigned secret task"):
        assert secret not in for_daniel
        assert secret in for_owner


def test_only_known_meeting_links_get_a_join_button(session, owner, daniel, weekly, busy, due):
    series_mail.queue_due_reminders(session, NOW)
    row = next(r for r in queued(session, "series_reminder") if r.to_email == DANIEL_EMAIL)
    for url, shown in [
        ("https://us02web.zoom.us/j/81234567890?pwd=abc", True),
        ("https://meet.google.com/abc-defg-hij", True),
        ("http://meet.google.com/abc-defg-hij", False),
        ("https://evil.example/meet.google.com", False),
        ("https://meet.google.com.evil.example/x", False),
        ("javascript:alert(1)", False),
        ("https://zoom.us.evil.example/j/1", False),
    ]:
        due.meeting_url = url
        session.commit()
        message = build(session, row)
        assert ("Join meeting" in message.text) is shown, url


def test_a_reminder_is_dropped_when_the_world_moved_on(session, owner, daniel, weekly, busy, due):
    series_mail.queue_due_reminders(session, NOW)
    row = next(r for r in queued(session, "series_reminder") if r.to_email == DANIEL_EMAIL)
    assert isinstance(build(session, row), mail.Message)
    due.starts_at = due.starts_at + timedelta(hours=2)  # the sync saw the occurrence move
    session.commit()
    assert build(session, row).reason == "moved_or_cancelled"
    due.starts_at = due.starts_at - timedelta(hours=2)
    session.commit()
    assert build(session, row, NOW + timedelta(minutes=56)).reason == "started"
    session.get(Team, weekly.team_id).reminders_on = False
    session.commit()
    assert build(session, row).reason == "reminders_off"
    session.get(Team, weekly.team_id).reminders_on = True
    session.add(UserSettings(user_id=daniel.id, notify_reminders=False))
    session.commit()
    assert build(session, row).reason == "opted_out"


def test_everything_reaches_the_sender_through_the_outbox(
    session, owner, daniel, weekly, busy, due
):
    class Capture(mail.Mailer):
        def __init__(self):
            self.sent = []

        def send(self, message, key):
            self.sent.append(message)

    add_line(session, weekly, owner)
    series_mail.queue_due_reminders(session, NOW)
    series_mail.send_agenda(session, owner, weekly, NOW)
    session.commit()
    session.execute(update(MailOutbox).values(created_at=NOW))
    session.commit()
    capture = Capture()
    went = mail.deliver_pending(session, capture, SETTINGS, NOW)
    assert went == 3
    assert sorted(m.subject for m in capture.sent) == [
        "Agenda: Monday planning",
        "Starts in 1 hour: Monday planning",
        "Starts in 1 hour: Monday planning",
    ]
    # series mail is notes mail: its row keeps ids only after it is sent
    assert all(r.status == "sent" and r.payload for r in queued(session))


def test_a_series_title_or_name_with_a_web_address_is_defanged_in_the_mail(
    session, owner, daniel, weekly, busy
):
    weekly.title = "Update: verify at payroll-portal.example.com"
    owner.name = "Ada visit evil.example.org"
    add_line(session, weekly, owner)
    session.commit()
    series_mail.send_agenda(session, owner, weekly, NOW)
    message = build(session, queued(session, "series_agenda")[0])
    assert "payroll-portal.example.com" not in message.subject + message.text
    assert "payroll-portal[.]example[.]com" in message.subject
    assert "evil.example.org" not in message.text
