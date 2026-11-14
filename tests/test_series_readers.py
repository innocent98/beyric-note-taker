"""Recurring meetings follow the meetings, PR 2 (docs/superpowers/specs/2026-11-14-series-follow-
meetings.md, sections 4 and 9): the READERS and the mail use the scope rule. Written before the
code. Leak cases of section 9 that this PR touches: 2 to 9, 10 to 12 (the readers' half), 16, and
Adebayo's answers (a one-team person's private meeting is on the personal page only; no "Keeps
coming up" on the personal page; "next meeting" hidden on personal rows; one reminder mail, ordinary
team before personal, then the lower team id).

The cast is `tests/test_multi_team_leaks.py`'s `world`: Ada records and owns team X; Pat is in X and
Y; Quinn owns Y and is only in Y; Zed owns Z. Pat's Standup (one recurring event, `rid-1`) has three
rows, one per scope, each holding exactly the meetings of its scope: X's holds Monday (shared to X),
Y's Wednesday (shared to Y), Pat's personal row Tuesday (private). "Mixed" rows (a legacy row that
still points at a meeting of another scope: before the backfill, or a straggler) are built by hand;
the readers must not depend on the writers keeping rows pure. The flag `SERIES_FOLLOW_MEETINGS` is
ON in every test; with it off the old suites (`tests/test_series*.py`, `tests/test_topics.py`,
`tests/test_calendar_recurring.py`) are the proof that nothing changed.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import deletion, series_mail, teams, topics, visibility
from app import series as series_rules
from app.models import (
    MailOutbox,
    SeriesAgendaItem,
    SeriesReminder,
    SeriesTopic,
    Task,
    Team,
    User,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    OWNER_EMAIL,
    Api,
    anon,
    anon_client,
    api,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    template,
)
from tests.test_ask import model, post  # noqa: F401  (model: a fixture used by name)
from tests.test_multi_team_leaks import (  # noqa: F401  (fixtures used by name)
    PAT,
    QUINN,
    multi_team_on,
    world,
)
from tests.test_multi_team_moved import NoVexa
from tests.test_series import (  # noqa: F401
    MONDAY,
    NOW,
    calendar_event,
    fixed_clock,
    question_in,
    task_in,
)
from tests.test_series_follow import RID, personal_of, put, standup
from tests.test_series_mail import build, queued
from tests.test_team_visibility import sign_in
from tests.test_topics import FakeTopics, message_of, run, stored, week

TUESDAY, WEDNESDAY = MONDAY + timedelta(days=1), MONDAY + timedelta(days=2)


@pytest.fixture(autouse=True)
def follow_on(monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: True)


def speak(session, meeting, name):
    meeting.speaker_transcript = f"[{name}]: Hello about {name}."
    session.commit()


def add_task(session, team_id, meeting, title, *, due=None, suggested=False, **fields) -> Task:
    task = task_in(session, team_id, meeting, title, **fields)
    task.due_date, task.suggested = due, suggested
    session.commit()
    return task


def get(who, path, team=None):
    headers = {"X-Team-Id": str(team)} if team else {}
    return who.client.get(path, headers=headers)


def page_of(who, row):
    response = who.get(f"/api/series/{row.id}")
    assert response.status_code == 200, response.text
    return response.json()


def cards_of(who, team):
    response = get(who, "/api/series", team)
    assert response.status_code == 200, response.text
    return response.json()["series"]


def past_ids(page) -> list[int]:
    return [m["id"] for m in page["past_meetings"]]


@pytest.fixture
def pats(session, template, world):
    """Pat's three Standups, one meeting each, a task and a carried question on each."""
    pat = world.pat
    w = SimpleNamespace(me=personal_of(session, pat))
    w.x_row = standup(session, world.x.id, owner_id=pat.id)
    w.y_row = standup(session, world.y.id, owner_id=pat.id)
    w.me_row = standup(session, w.me, owner_id=pat.id)
    w.monday = put(session, template, w.x_row, who=pat, team=world.x.id, when=MONDAY)
    w.tuesday = put(session, template, w.me_row, who=pat, when=TUESDAY)
    w.wednesday = put(session, template, w.y_row, who=pat, team=world.y.id, when=WEDNESDAY)
    for meeting, name in ((w.monday, "Mona Monday"), (w.tuesday, "Tessa Tuesday")):
        speak(session, meeting, name)
    speak(session, w.wednesday, "Wanda Wednesday")
    for meeting, team, label in (
        (w.monday, world.x.id, "Monday"),
        (w.tuesday, w.me, "Tuesday"),
        (w.wednesday, world.y.id, "Wednesday"),
    ):
        add_task(session, team, meeting, f"{label} task")
        question_in(session, meeting, f"{label} question", carry=True)
    return w


@pytest.fixture
def mixed(session, template, world, pats):
    """A legacy X row that still points at Monday (X), Tuesday (private, Pat's) and Wednesday
    (shared to Y): the three scopes in one row. Pat reads all three."""
    row = standup(session, world.x.id, rid="rid-mixed", owner_id=world.pat.id)
    for meeting in (pats.monday, pats.tuesday, pats.wednesday):
        meeting.series_id = row.id
    session.commit()
    return row


# ---- case 1 and the personal page: each page lists its scope's meetings and nothing else ----


def test_each_pages_row_lists_its_own_meeting_only(app, session, world, pats):
    pat = sign_in(app, PAT)
    rows = {world.x.id: pats.x_row, world.y.id: pats.y_row, pats.me: pats.me_row}
    expected = {world.x.id: pats.monday, world.y.id: pats.wednesday, pats.me: pats.tuesday}
    for team_id, row in rows.items():
        cards = cards_of(pat, team_id)
        assert [c["id"] for c in cards] == [row.id]  # one Standup per page
        assert past_ids(page_of(pat, row)) == [expected[team_id].id]
    ada = sign_in(app, OWNER_EMAIL)
    assert [c["id"] for c in cards_of(ada, world.x.id)] == [pats.x_row.id]
    assert past_ids(page_of(ada, pats.x_row)) == [pats.monday.id]
    for other in (pats.y_row, pats.me_row):
        assert ada.get(f"/api/series/{other.id}").status_code == 404


def test_after_the_occurrence_is_unshared_the_team_no_longer_lists_the_row(
    app, session, world, pats
):
    ada = sign_in(app, OWNER_EMAIL)
    assert [c["id"] for c in cards_of(ada, world.x.id)] == [pats.x_row.id]
    pats.monday.shared_team_id = None  # unshared (the row would be emptied by the hook)
    pats.monday.series_id = pats.me_row.id
    session.commit()
    assert cards_of(ada, world.x.id) == []
    assert ada.get(f"/api/series/{pats.x_row.id}").status_code == 404


def test_a_one_team_persons_private_meeting_is_on_the_personal_page_only(
    app, session, world, template
):
    """Ada has one ordinary team, X: her default page. Her private Standup is on her personal page
    and on no team page, even in a legacy row that still holds it."""
    ada_personal = personal_of(session, world.ada)
    legacy = standup(session, world.x.id, owner_id=world.pat.id)  # not Ada's own empty card
    private = put(session, template, legacy, who=world.ada, when=TUESDAY)
    ada = sign_in(app, OWNER_EMAIL)
    assert cards_of(ada, None) == []  # her default team page: nothing of hers private
    assert cards_of(ada, world.x.id) == []
    assert ada.get(f"/api/series/{legacy.id}").status_code == 404  # X's row, nothing in scope
    mine = standup(session, ada_personal, owner_id=world.ada.id)
    private.series_id = mine.id
    session.commit()
    [card] = cards_of(ada, ada_personal)
    assert card["id"] == mine.id
    assert past_ids(page_of(ada, mine)) == [private.id]
    assert cards_of(ada, None) == []  # still not on the default (X) page


# ---- cases 2 and 16: a mixed row shows only its scope's meetings ----


def test_a_person_in_x_and_y_sees_on_xs_page_only_xs_meeting_even_in_a_mixed_row(
    app, session, world, pats, mixed
):
    add_task(session, world.y.id, pats.wednesday, "Late Y task", due=date(2026, 10, 1))
    done = add_task(session, world.y.id, pats.wednesday, "Done Y task", status="done")
    done.done_at = WEDNESDAY
    add_task(session, world.y.id, pats.wednesday, "Suggested Y task", suggested=True)
    session.commit()
    pat = sign_in(app, PAT)
    [card] = [c for c in cards_of(pat, world.x.id) if c["id"] == mixed.id]
    assert (card["unfinished_tasks"], card["open_questions"]) == (1, 1)  # Monday's, not Y's or his
    page = page_of(pat, mixed)
    assert past_ids(page) == [pats.monday.id]
    assert [t["title"] for t in page["unfinished"]] == ["Monday task"]
    assert page["finished"] == []
    assert [q["text"] for q in page["questions"]] == ["Monday question"]
    names = {a["name"] for a in page["attendees"]}
    assert "Mona Monday" in names
    assert not names & {"Tessa Tuesday", "Wanda Wednesday"}
    kinds = [line["kind"] for line in page["suggested_agenda"]]
    assert "overdue" not in kinds and "suggested" not in kinds  # Y's late and suggested tasks
    assert [m.id for m in series_rules.meetings_of(session, mixed, None)] == [pats.monday.id]
    # the same row, the other scopes' readers: Pat's personal page does not list the X row at all
    assert [c["id"] for c in cards_of(pat, pats.me)] == [pats.me_row.id]


def test_the_row_readers_filter_by_scope_directly(session, world, pats, mixed):
    pat = world.pat
    pairs = series_rules.carried_questions(session, pat, mixed)
    assert [m.id for _q, m in pairs] == [pats.monday.id]
    assert series_rules.counts_for(session, pat, [mixed.id]) == {mixed.id: (1, 1)}
    assert [t.title for t in series_rules.unfinished_tasks(session, pat, mixed)] == ["Monday task"]
    assert series_rules.last_meeting_day(session, pat, mixed, None) == MONDAY.date()
    assert [a.name for a in series_rules.attendees(session, mixed, pat) if a.user_id is None] == [
        "Mona Monday"
    ]


def test_resolving_a_question_never_closes_the_same_words_in_another_scope(
    session, world, pats, mixed
):
    """Case 5 and the note's section 6: resolving applies to the same words in the same row and
    scope only."""
    for meeting, text in ((pats.monday, "Same words"), (pats.wednesday, "Same words")):
        question_in(session, meeting, text, position=5, carry=True)
    mine = series_rules.carried_questions(session, world.pat, mixed)
    question = next(q for q, _m in mine if q.text == "Same words")
    series_rules._resolve(session, world.pat, question, pats.monday, NOW)
    session.commit()
    other = session.scalars(
        select(type(question)).where(
            type(question).meeting_id == pats.wednesday.id, type(question).text == "Same words"
        )
    ).one()
    assert other.resolved_at is None


def test_the_meeting_page_shows_the_series_row_only_for_a_meeting_in_its_scope(
    app, session, world, pats, mixed
):
    """Case 16 on the meeting page: a legacy X row still points at Wednesday (shared to Y), and Pat
    reads both: Monday shows X's Standup, Wednesday shows no series at all."""
    pat = sign_in(app, PAT)
    monday = pat.get(f"/api/meetings/{pats.monday.id}").json()
    wednesday = pat.get(f"/api/meetings/{pats.wednesday.id}").json()
    assert monday["series"] == {"id": mixed.id, "title": "Standup"}
    assert wednesday["series"] is None


# ---- case 3: a person only in Y ----


def test_a_person_only_in_y_gets_404_for_xs_row_everywhere_and_never_lists_it(
    app, session, world, pats
):
    quinn = sign_in(app, QUINN)
    base = f"/api/series/{pats.x_row.id}"
    for method, path, body in (
        ("GET", base, None),
        ("GET", f"{base}/agenda", None),
        ("POST", f"{base}/agenda", {"text": "sneaky line"}),
        ("POST", f"{base}/agenda/send", None),
        ("PATCH", base, {"title": "Hijack"}),
        ("PUT", f"{base}/project", {"project_id": None}),
    ):
        assert quinn.call(method, path, body).status_code == 404, (method, path)
    assert get(quinn, "/api/series", world.x.id).status_code == 404  # not his team
    assert [c["id"] for c in cards_of(quinn, world.y.id)] == [pats.y_row.id]
    assert past_ids(page_of(quinn, pats.y_row)) == [pats.wednesday.id]  # Wednesday only
    for hidden in (pats.x_row, pats.me_row):
        assert quinn.get(f"/api/series/{hidden.id}").status_code == 404
    assert session.scalars(select(SeriesAgendaItem)).all() == []


# ---- case 4: "Keeps coming up" ----


def test_a_topic_citing_a_meeting_of_another_scope_is_hidden_even_for_one_who_reads_both(
    app, session, world, pats, mixed
):
    topics_row = SeriesTopic(
        series_id=mixed.id,
        position=0,
        label="Across scopes",
        agenda="Decide across scopes",
        reason="no_decision",
        meeting_ids=[pats.monday.id, pats.wednesday.id],
        source_meeting_ids=[pats.monday.id, pats.wednesday.id],
    )
    session.add(topics_row)
    session.commit()
    pat = sign_in(app, PAT)
    page = page_of(pat, mixed)
    assert page["topics"] == [] and "Across scopes" not in json.dumps(page)
    assert all(line["kind"] != "topic" for line in page["suggested_agenda"])


def test_the_topics_job_reads_only_the_rows_own_scope(session, template, world, pats):
    """Y's and Pat's private meetings of one recurring event never reach the model for X's row."""
    pat = world.pat
    x_row = pats.x_row
    for n in (0, 1):
        meeting = week(session, template, x_row, n, f"Xtopic {n}", user=pat)
        meeting.shared_team_id = world.x.id
    for n, row in ((2, pats.y_row), (3, pats.me_row)):
        meeting = week(session, template, row, n, f"LEAKY{n}", user=pat, shared=False)
        meeting.series_id = x_row.id  # a legacy straggler inside X's row
        meeting.shared_team_id = world.y.id if n == 2 else None
    session.commit()
    sources = topics.source_meetings(session, x_row)
    assert all(m.shared_team_id == world.x.id for m in sources) and len(sources) >= 2
    fake = FakeTopics()
    run(session, fake)
    text = "".join(message_of(call) for call in fake.calls)
    assert "Xtopic" in text and "LEAKY" not in text


def test_keeps_coming_up_is_never_asked_nor_shown_on_a_personal_row(
    app, session, template, world, pats
):
    pat = world.pat
    solo_state = []  # the 0043 state: private meetings recorded as "shared" to the personal team
    for n in range(3):
        meeting = week(session, template, pats.me_row, n, f"Private theme {n}", user=pat)
        meeting.shared_team_id = pats.me
        solo_state.append(meeting)
    session.add(
        SeriesTopic(
            series_id=pats.me_row.id,
            position=0,
            label="Private theme",
            agenda="Decide the private theme",
            reason="no_decision",
            meeting_ids=[m.id for m in solo_state[:2]],
            source_meeting_ids=[m.id for m in solo_state[:2]],
        )
    )
    session.commit()
    assert topics.source_meetings(session, pats.me_row) == []
    fake = FakeTopics()
    run(session, fake)
    assert fake.calls == [] and stored(session, pats.me_row.id) == []  # asked nothing, cleared
    session.add(
        SeriesTopic(
            series_id=pats.me_row.id,
            position=0,
            label="Stale topic",
            agenda="Decide the stale topic",
            reason="no_decision",
            meeting_ids=[m.id for m in solo_state[:2]],
            source_meeting_ids=[m.id for m in solo_state[:2]],
        )
    )
    session.commit()
    page = page_of(sign_in(app, PAT), pats.me_row)
    assert page["topics"] == []
    assert all(line["kind"] != "topic" for line in page["suggested_agenda"])


# ---- case 6: typed agenda lines stay on the page they were typed on ----


def test_a_line_typed_on_xs_page_is_on_no_other_rows_page_list_or_mail(
    app, session, template, world, pats
):
    pat = sign_in(app, PAT)
    assert pat.post(f"/api/series/{pats.x_row.id}/agenda", {"text": "Budget review"}).status_code
    assert [i["text"] for i in page_of(pat, pats.x_row)["agenda"]] == ["Budget review"]
    for other in (pats.y_row, pats.me_row):
        assert page_of(pat, other)["agenda"] == []
        assert pat.get(f"/api/series/{other.id}/agenda").json() == []
    # the personal row's Tuesday meeting links to its own row and consumes nothing of X's
    lines = session.scalars(select(SeriesAgendaItem)).all()
    assert [(i.series_id, i.consumed_at) for i in lines] == [(pats.x_row.id, None)]
    # mail: the line is in X's agenda mail and in no mail built from Y's row
    ada = world.ada
    sent_x = series_mail.send_agenda(session, ada, pats.x_row, NOW)
    assert [p.user_id for p in sent_x] == [world.pat.id]
    session.commit()
    [x_mail] = queued(session, "series_agenda")
    assert "Budget review" in build(session, x_mail).text
    sent_y = series_mail.send_agenda(session, world.quinn, pats.y_row, NOW)
    session.commit()
    assert [p.user_id for p in sent_y] == [world.pat.id]
    y_mail = queued(session, "series_agenda")[-1]
    assert "Budget review" not in build(session, y_mail).text


# ---- case 7: agenda mail ----


def test_an_agenda_from_xs_row_names_nothing_of_a_y_or_private_meeting(session, world, pats, mixed):
    sent = series_mail.send_agenda(session, world.ada, mixed, NOW)
    session.commit()
    assert [p.user_id for p in sent] == [world.pat.id]
    [row] = queued(session, "series_agenda")
    assert row.to_email == PAT
    message = build(session, row)
    text = message.text + message.html
    for hidden in ("Wednesday", "Tuesday", "Wanda", "Tessa"):
        assert hidden not in text, hidden
    assert "Monday task" in text and "Monday question" in text
    assert all(p.user_id != world.quinn.id for p in sent)  # Quinn is never a recipient


def test_a_row_whose_only_meetings_are_of_another_scope_has_no_recipients(session, world, pats):
    """Pat attended only a Y meeting that a legacy X row still holds: X's agenda goes to nobody."""
    only_wednesday = standup(session, world.x.id, rid="rid-w", owner_id=world.ada.id)
    pats.wednesday.series_id = only_wednesday.id
    session.commit()
    assert series_rules.recipients(session, only_wednesday) == []
    assert series_rules.attendees(session, only_wednesday, world.pat) == []
    with pytest.raises(series_mail.AgendaRefused) as refused:
        series_mail.send_agenda(session, world.ada, only_wednesday, NOW)
    assert refused.value.code == "no_attendees"


def test_the_personal_row_mails_nobody_else(session, world, pats):
    with pytest.raises(series_mail.AgendaRefused) as refused:
        series_mail.send_agenda(session, world.pat, pats.me_row, NOW)
    assert refused.value.code == "no_attendees"
    # Ada (X's owner) is named in the private meeting: she is not a member of the personal team,
    # so she is not matched, whatever team's members the code would look at
    speak(session, pats.tuesday, "Owner")
    assert [a.user_id for a in series_rules.recipients(session, pats.me_row)] == [world.pat.id]


# ---- case 8: reminder mail ----


def turn_on(session, *team_ids):
    """Reminders on, one team at a time and in this order (each is an UPDATE, so the table's
    natural order becomes this order: the code must not depend on it)."""
    for team_id in team_ids:
        session.get(Team, team_id).reminders_on = True
        session.commit()


def test_a_person_in_two_teams_and_personal_gets_one_mail_from_the_ordinary_lower_id_team(
    session, world, pats
):
    assert world.x.id < world.y.id and pats.me < world.x.id  # the personal team has the lowest id
    turn_on(session, world.y.id, pats.me, world.x.id)  # natural order now: Y, personal, X
    calendar_event(session, world.pat, RID, NOW + timedelta(minutes=55))
    assert series_mail.queue_due_reminders(session, NOW) == 1
    [reminder] = session.scalars(select(SeriesReminder)).all()
    assert reminder.series_id == pats.x_row.id and reminder.user_id == world.pat.id
    [row] = queued(session, "series_reminder")
    message = build(session, row)
    text = message.text + message.html
    for hidden in ("Wednesday", "Tuesday", "Wanda", "Tessa"):
        assert hidden not in text, hidden  # never mixed from the other rows
    assert series_mail.queue_due_reminders(session, NOW) == 0  # and still only once


def test_the_lower_team_id_wins_between_two_ordinary_teams(session, world, pats):
    turn_on(session, world.y.id, world.x.id)
    calendar_event(session, world.pat, RID, NOW + timedelta(minutes=55))
    assert series_mail.queue_due_reminders(session, NOW) == 1
    assert session.scalar(select(SeriesReminder.series_id)) == pats.x_row.id


def test_the_personal_row_reminds_only_its_owner_and_only_when_nothing_else_does(
    session, world, pats
):
    turn_on(session, pats.me)
    calendar_event(session, world.pat, RID, NOW + timedelta(minutes=55))
    assert series_mail.queue_due_reminders(session, NOW) == 1
    [row] = queued(session, "series_reminder")
    assert row.to_email == PAT  # never another person: the personal row has no one else
    assert session.scalar(select(SeriesReminder.series_id)) == pats.me_row.id


def test_a_row_with_nothing_in_scope_has_nothing_to_say_and_stays_quiet(
    session, world, pats, mixed
):
    """A legacy X row holding only a Y meeting has nothing to say, so it sends no reminder."""
    stray = standup(session, world.x.id, rid="rid-stray", owner_id=world.ada.id)
    pats.wednesday.series_id = stray.id
    session.commit()
    turn_on(session, world.x.id)
    calendar_event(session, world.ada, "rid-stray", NOW + timedelta(minutes=55))
    calendar_event(session, world.pat, "rid-stray", NOW + timedelta(minutes=55))
    assert series_mail.queue_due_reminders(session, NOW) == 0


# ---- case 9: Ask ----


def test_ask_never_answers_a_member_of_x_from_an_occurrence_of_another_scope(
    api, session, world, pats, model
):
    for meeting in (pats.wednesday, pats.tuesday):
        meeting.notes = "## Summary\nZanzibar plan."
    session.commit()
    ada_api = api  # the signed-in owner: Ada, in X
    assert post(ada_api, "zanzibar plan").json()["kind"] == "none"
    assert model.calls == []
    # and Ask's own count of what it can search is Ada's readable meetings, none of the others
    assert ada_api.get("/api/ask/scope").json()["meetings"] == len(
        list(session.scalars(visibility.visible_meetings(world.ada)))
    )


# ---- case 10: a person who left X ----


def test_a_person_who_left_x_loses_xs_rows_mail_and_topic_and_keeps_the_meeting_privately(
    app, session, template, world, pats
):
    ada_monday = put(
        session, template, pats.x_row, who=world.ada, team=world.x.id, when=MONDAY - timedelta(7)
    )
    session.add(
        SeriesTopic(
            series_id=pats.x_row.id,
            position=0,
            label="Cites Pat",
            agenda="Decide it",
            reason="no_decision",
            meeting_ids=[ada_monday.id, pats.monday.id],
            source_meeting_ids=[ada_monday.id, pats.monday.id],
        )
    )
    session.commit()
    teams.leave(session, world.pat, NOW, team_id=world.x.id)
    session.commit()
    pat, ada = sign_in(app, PAT), sign_in(app, OWNER_EMAIL)
    assert pat.get(f"/api/series/{pats.x_row.id}").status_code == 404
    assert get(pat, "/api/series", world.x.id).status_code == 404
    assert past_ids(page_of(ada, pats.x_row)) == [ada_monday.id]
    assert page_of(ada, pats.x_row)["topics"] == []  # a cited meeting is private now (readability)
    mine = page_of(pat, pats.me_row)
    assert set(past_ids(mine)) == {pats.tuesday.id, pats.monday.id}  # it follows him, private
    assert world.pat.id not in {a.user_id for a in series_rules.recipients(session, pats.x_row)}
    turn_on(session, world.x.id)
    calendar_event(session, world.pat, RID, NOW + timedelta(minutes=55))
    series_mail.queue_due_reminders(session, NOW)
    assert PAT not in [r.to_email for r in queued(session, "series_reminder")]
    with pytest.raises(series_mail.AgendaRefused):  # nobody of X attended any more
        series_mail.send_agenda(session, world.ada, pats.x_row, NOW)


# ---- case 11: a moved meeting whose recorder left ----


def test_a_moved_meeting_stays_in_xs_row_and_the_leaver_reads_nothing_of_it(
    app, session, template, world, pats, model
):
    moved = put(session, template, pats.x_row, who=world.pat, moved=world.x.id, when=WEDNESDAY)
    moved.notes = "## Summary\nZanzibar plan."
    other = put(
        session, template, pats.x_row, who=world.ada, team=world.x.id, when=MONDAY - timedelta(7)
    )
    session.add(
        SeriesTopic(
            series_id=pats.x_row.id,
            position=0,
            label="Moved topic",
            agenda="Decide moved",
            reason="no_decision",
            meeting_ids=[other.id, moved.id],
            source_meeting_ids=[other.id, moved.id],
        )
    )
    session.commit()
    teams.leave(session, world.pat, NOW, team_id=world.x.id)
    session.commit()
    ada, pat = sign_in(app, OWNER_EMAIL), sign_in(app, PAT)
    page = page_of(ada, pats.x_row)
    assert moved.id in past_ids(page)
    assert [t["label"] for t in page["topics"]] == ["Moved topic"]
    assert pat.get(f"/api/series/{pats.x_row.id}").status_code == 404
    assert moved.id not in {m.id for m in session.scalars(visibility.visible_meetings(world.pat))}
    mine = page_of(pat, pats.me_row)
    assert moved.id not in past_ids(mine)  # a team's meeting is on no personal page
    assert PAT not in [r.to_email for r in queued(session)]
    assert post(pat, "zanzibar plan").json()["kind"] == "none"
    assert world.pat.id not in {a.user_id for a in series_rules.recipients(session, pats.x_row)}


# ---- case 12: the recorder deleted their account, the moved meeting stays ----


def test_a_deleted_recorders_moved_meeting_stays_readable_and_gets_no_mail(
    app, session, template, world, pats
):
    moved = put(session, template, pats.x_row, who=world.pat, moved=world.x.id, when=WEDNESDAY)
    other = put(
        session, template, pats.x_row, who=world.ada, team=world.x.id, when=MONDAY - timedelta(7)
    )
    teams.leave(session, world.pat, NOW, team_id=world.x.id)
    session.commit()
    deletion.purge_user(session, session.get(User, world.pat.id), NoVexa(), force=True)
    session.expire_all()
    ada = sign_in(app, OWNER_EMAIL)
    page = page_of(ada, pats.x_row)
    assert {moved.id, other.id} <= set(past_ids(page))
    assert session.get(User, world.pat.id).status == "deleted"
    assert world.pat.id not in {a.user_id for a in series_rules.recipients(session, pats.x_row)}
    turn_on(session, world.x.id)
    calendar_event(session, world.ada, RID, NOW + timedelta(minutes=55))
    series_mail.queue_due_reminders(session, NOW)
    assert PAT not in [r.to_email for r in session.scalars(select(MailOutbox))]


# ---- Adebayo's answers: "next meeting" and the team on the payload ----


def test_a_personal_row_hides_next_meeting_and_a_team_row_shows_it(app, session, world, pats):
    calendar_event(session, world.pat, RID, NOW + timedelta(days=5))  # on his own calendar
    pat = sign_in(app, PAT)
    [team_card] = cards_of(pat, world.x.id)
    assert team_card["next_starts_at"] is not None and team_card["next_text"]
    [personal_card] = cards_of(pat, pats.me)
    assert personal_card["next_starts_at"] is None and personal_card["next_text"] is None
    team_page, personal_page = page_of(pat, pats.x_row), page_of(pat, pats.me_row)
    assert team_page["next_starts_at"] is not None
    assert personal_page["next_starts_at"] is None
    assert personal_page["next_text"] is None and personal_page["sprint_note"] is None


def test_the_card_and_the_page_name_the_rows_team(app, session, world, pats):
    pat = sign_in(app, PAT)
    expected = {
        world.x.id: pats.x_row,
        world.y.id: pats.y_row,
        pats.me: pats.me_row,
    }
    for team_id, row in expected.items():
        name = session.get(Team, team_id).name
        [card] = cards_of(pat, team_id)
        assert card["team"] == {"id": team_id, "name": name}
        assert page_of(pat, row)["team"] == {"id": team_id, "name": name}
