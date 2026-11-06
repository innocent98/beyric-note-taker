"""W8 Series: linking meetings to a recurring meeting, the Series page and cards, the agenda,
carry-over, and who may see and change what.

The cast: `owner` (owner of the team and of the meetings), `daniel` (a member of the owner's team,
built by hand because invites are W3) and `outsider` (a team of their own).
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import api_series, google_oauth, pipeline, series, teams
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Meeting,
    OpenQuestion,
    ScheduledJoin,
    Series,
    SeriesAgendaItem,
    Task,
    TeamMember,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
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
from tests.test_team_visibility import OTHER_EMAIL, outsider, share, sign_in  # noqa: F401
from tests.test_web import make_user, stranger  # noqa: F401

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)  # a Wednesday
DANIEL_EMAIL = "daniel@example.test"
MONDAY = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
SPEAKERS = "[Owner]: We ship Friday.\n[Daniel Okoro]: I will do checkout.\n[Guest Visitor]: Hello."


def patch(who, url, body):
    return who.call("PATCH", url, body)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(api_series, "_now", lambda: NOW)


@pytest.fixture
def daniel(session, owner):
    user = make_user(session, DANIEL_EMAIL, name="Daniel Okoro")
    team = teams.ensure_team(session, owner)
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return user


@pytest.fixture
def weekly(session, owner):
    team = teams.ensure_team(session, owner)
    found = Series(team_id=team.id, owner_user_id=owner.id, title="Monday planning")
    session.add(found)
    session.commit()
    return found


def meeting_in(session, template, found, *, when=MONDAY, user=None, **fields):
    """A ready meeting of `user` (default the template's owner) inside the series."""
    meeting = ready(
        session,
        template,
        series_id=found.id,
        created_at=when,
        speaker_transcript=SPEAKERS,
        **fields,
    )
    if user is not None:
        meeting.owner_user_id = user.id
    session.commit()
    return meeting


def task_in(session, team_id, meeting, title, *, status="todo", assignee=None, done_at=None):
    task = Task(
        team_id=team_id,
        meeting_id=meeting.id,
        title=title,
        source="ai",
        ai_fingerprint=f"fp-{title}",
        status=status,
        assignee_user_id=assignee.id if assignee else None,
        done_at=done_at,
        assigned_on=date(2026, 11, 2),
    )
    session.add(task)
    session.commit()
    return task


def question_in(session, meeting, text, position=0, **fields):
    row = OpenQuestion(
        meeting_id=meeting.id, text=text, position=position, fingerprint=f"fp-{text}", **fields
    )
    session.add(row)
    session.commit()
    return row


def calendar_event(session, user, recurring_id, starts, **fields) -> CalendarEvent:
    conn = session.scalars(
        select(CalendarConnection).where(CalendarConnection.user_id == user.id)
    ).first()
    if conn is None:
        conn = CalendarConnection(user_id=user.id, granted_scopes=google_oauth.CALENDAR_SCOPE)
        session.add(conn)
        session.commit()
    values = {
        "user_id": user.id,
        "connection_id": conn.id,
        "source_event_id": f"{recurring_id}-{starts.isoformat()}",
        "recurring_event_id": recurring_id,
        "title": "Monday planning",
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=1),
        "platform": "google_meet",
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "native_id": "abc-defg-hij",
        "status": "confirmed",
        **fields,
    }
    event = CalendarEvent(**values)
    session.add(event)
    session.commit()
    return event


def join_for(session, user, event, meeting=None, **fields) -> ScheduledJoin:
    row = ScheduledJoin(
        user_id=user.id,
        calendar_event_id=event.id,
        platform="google_meet",
        native_id=event.native_id,
        meeting_url=event.meeting_url,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        run_at=event.starts_at,
        deadline_at=event.ends_at,
        meeting_id=meeting.id if meeting else None,
        **fields,
    )
    session.add(row)
    session.commit()
    return row


# ---- linking a calendar meeting ----


def test_a_calendar_meeting_joins_the_series_of_its_recurring_event(session, owner, template):
    first = calendar_event(session, owner, "rid-1", MONDAY)
    meeting = ready(session, template, source="calendar", created_at=MONDAY)
    join_for(session, owner, first, meeting, state="completed")
    made = series.link_from_calendar(session, meeting)
    assert made.title == "Monday planning" and made.calendar_recurring_id == "rid-1"
    assert meeting.series_id == made.id
    assert made.team_id == teams.team_of(session, owner.id).id
    # the next week's meeting lands in the same series, not a second one
    again = calendar_event(session, owner, "rid-1", MONDAY + timedelta(days=7))
    second = ready(session, template, source="calendar")
    join_for(session, owner, again, second, state="completed")
    assert series.link_from_calendar(session, second).id == made.id
    assert session.scalars(select(Series)).all() == [made]


def test_a_meeting_without_a_recurring_event_or_not_from_the_calendar_is_not_linked(
    session, owner, template
):
    single = calendar_event(session, owner, None, MONDAY)
    meeting = ready(session, template, source="calendar")
    join_for(session, owner, single, meeting, state="completed")
    assert series.link_from_calendar(session, meeting) is None
    pasted = ready(session, template)  # source manual
    assert series.link_from_calendar(session, pasted) is None
    assert session.scalars(select(Series)).all() == []


def test_a_persons_own_choice_of_series_is_not_overwritten(session, owner, template, weekly):
    event = calendar_event(session, owner, "rid-1", MONDAY)
    meeting = ready(session, template, source="calendar", series_id=weekly.id)
    join_for(session, owner, event, meeting, state="completed")
    assert series.link_from_calendar(session, meeting).id == weekly.id
    assert session.scalars(select(Series)).all() == [weekly]


def test_the_pipeline_link_never_fails_the_notes(session, owner, template, monkeypatch):
    meeting = ready(session, template, source="calendar")

    def boom(*args):
        raise RuntimeError("down")

    monkeypatch.setattr(series, "link_from_calendar", boom)
    pipeline._link_series(session, meeting)  # logged, not raised
    assert meeting.status == "ready"


def test_two_teammates_with_the_same_recurring_event_share_one_series(
    session, owner, daniel, template
):
    for who in (owner, daniel):
        event = calendar_event(session, who, "rid-1", MONDAY)
        meeting = ready(session, template, source="calendar")
        meeting.owner_user_id = who.id
        session.commit()
        join_for(session, who, event, meeting, state="completed")
        series.link_from_calendar(session, meeting)
    assert len(session.scalars(select(Series)).all()) == 1


def test_the_sync_makes_series_only_for_events_that_will_be_joined(session, owner):
    joined = calendar_event(session, owner, "rid-joined", NOW + timedelta(days=1))
    join_for(session, owner, joined, state="scheduled")
    skipped = calendar_event(session, owner, "rid-skipped", NOW + timedelta(days=1))
    join_for(session, owner, skipped, state="skipped")
    calendar_event(session, owner, "rid-lunch", NOW + timedelta(days=1))  # no join at all
    assert series.sync_from_joins(session, NOW) == 1
    assert series.sync_from_joins(session, NOW) == 0
    assert [s.calendar_recurring_id for s in session.scalars(select(Series))] == ["rid-joined"]


def test_attaching_by_hand_needs_your_meeting_and_your_teams_series(
    api, session, owner, template, weekly, outsider
):
    mine = ready(session, template)
    body = api.put(f"/api/meetings/{mine.id}/series", {"series_id": weekly.id}).json()
    assert body == {"series": {"id": weekly.id, "title": "Monday planning"}}
    session.expire_all()
    assert session.get(Meeting, mine.id).series_id == weekly.id
    assert api.put(f"/api/meetings/{mine.id}/series", {"series_id": None}).json() == {
        "series": None
    }
    # another team's series is not found, even for your own meeting
    theirs = Series(team_id=teams.ensure_team(session, outsider).id, title="Theirs")
    session.add(theirs)
    session.commit()
    error_of(api.put(f"/api/meetings/{mine.id}/series", {"series_id": theirs.id}), 404, "not_found")


def test_only_a_recorded_calendar_meeting_consumes_the_agenda_a_hand_attach_does_not(
    api, session, owner, template, weekly
):
    before = SeriesAgendaItem(
        series_id=weekly.id, text="Budget", created_by=owner.id, created_at=MONDAY - timedelta(1)
    )
    after = SeriesAgendaItem(
        series_id=weekly.id, text="Hiring", created_by=owner.id, created_at=MONDAY + timedelta(1)
    )
    session.add_all([before, after])
    session.commit()
    weekly.calendar_recurring_id = "rid-1"
    session.commit()
    attached = ready(session, template, created_at=MONDAY)
    api.put(f"/api/meetings/{attached.id}/series", {"series_id": weekly.id})
    assert len(series.agenda_items(session, weekly)) == 2  # a hand attach clears nothing
    event = calendar_event(session, owner, "rid-1", MONDAY)
    recorded = ready(session, template, source="calendar", created_at=MONDAY)
    join_for(session, owner, event, recorded, state="completed")
    series.link_from_calendar(session, recorded)
    assert [i.text for i in series.agenda_items(session, weekly)] == ["Hiring"]


def test_a_teammates_private_recurring_call_is_not_published_to_the_team(
    api, app, session, owner, daniel, template
):
    event = calendar_event(session, daniel, "rid-private", NOW + timedelta(days=1), title="1:1")
    join_for(session, daniel, event, state="scheduled")
    series.sync_from_joins(session, NOW)
    made = session.scalars(select(Series)).one()
    mine = sign_in(app, DANIEL_EMAIL)
    assert [c["title"] for c in mine.get("/api/series").json()["series"]] == ["1:1"]
    assert api.get("/api/series").json()["series"] == []  # the owner is not on that call
    error_of(api.get(f"/api/series/{made.id}"), 404, "not_found")
    # once a meeting of it is shared with the team, the team sees it
    meeting = meeting_in(session, template, made, user=daniel)
    share(session, meeting, daniel)
    assert [c["title"] for c in api.get("/api/series").json()["series"]] == ["1:1"]


# ---- the cards ----


def test_cards_count_only_what_the_caller_may_read(
    api, app, session, owner, daniel, template, weekly
):
    team_id = weekly.team_id
    mine = meeting_in(session, template, weekly)
    task_in(session, team_id, mine, "Owner private task")
    question_in(session, mine, "Owner private question")
    shared = meeting_in(session, template, weekly, when=MONDAY - timedelta(days=7))
    share(session, shared, owner)
    task_in(session, team_id, shared, "Shared task")
    question_in(session, shared, "Shared question")
    cards = api.get("/api/series").json()["series"]
    assert [(c["title"], c["unfinished_tasks"], c["open_questions"]) for c in cards] == [
        ("Monday planning", 2, 2)
    ]
    # Daniel sees only the shared meeting's task and question
    other = sign_in(app, DANIEL_EMAIL)
    cards = other.get("/api/series").json()["series"]
    assert [(c["unfinished_tasks"], c["open_questions"]) for c in cards] == [(1, 1)]


def test_a_card_has_a_cadence_and_the_next_date_from_the_calendar(api, session, owner, weekly):
    weekly.calendar_recurring_id = "rid-1"
    session.commit()
    for days in (0, 7, 14):
        calendar_event(session, owner, "rid-1", NOW + timedelta(days=5 + days, hours=3))
    card = api.get("/api/series").json()["series"][0]
    assert card["cadence"] == "Weekly on Mondays, 13:00"
    assert card["next_starts_at"] == "2026-11-09T13:00:00Z"
    assert card["next_text"] == "Mon 9 Nov, 13:00"
    assert api.get("/api/series").json()["timezone"] == "UTC"


def test_a_series_with_no_calendar_has_no_next_date(api, weekly):
    card = api.get("/api/series").json()["series"][0]
    assert (card["next_starts_at"], card["next_text"], card["cadence"]) == (
        None,
        None,
        "Recurring",
    )


def test_another_teams_series_are_not_listed_or_readable(api, app, session, outsider, weekly):
    other = sign_in(app, OTHER_EMAIL)
    assert other.get("/api/series").json()["series"] == []
    error_of(other.get(f"/api/series/{weekly.id}"), 404, "not_found")
    error_of(patch(other, f"/api/series/{weekly.id}", {"title": "x"}), 404, "not_found")
    error_of(other.post(f"/api/series/{weekly.id}/agenda", {"text": "x"}), 404, "not_found")
    error_of(other.post(f"/api/series/{weekly.id}/agenda/send"), 404, "not_found")


def test_making_and_renaming_a_series(api, app, session, owner, daniel, weekly, template):
    share(session, meeting_in(session, template, weekly), owner)  # so the team can see it
    created = api.post("/api/series", {"title": "  Friday  review  "})
    assert created.status_code == 201 and created.json()["title"] == "Friday review"
    error_of(api.post("/api/series", {"title": "   "}), 422, "invalid_title")
    mine = created.json()["id"]
    # Daniel (a member) cannot rename the owner's series, but can rename his own
    other = sign_in(app, DANIEL_EMAIL)
    error_of(patch(other, f"/api/series/{weekly.id}", {"title": "Mine now"}), 403, "team_forbidden")
    own = other.post("/api/series", {"title": "Daniel's"}).json()["id"]
    assert patch(other, f"/api/series/{own}", {"title": "Renamed"}).json()["title"] == "Renamed"
    # a series nobody else can read a meeting of is its maker's own: even the owner of the team
    # does not see it (named after a person's meeting, it is private until one is shared)
    error_of(patch(api, f"/api/series/{own}", {"title": "By manager"}), 404, "not_found")
    assert patch(api, f"/api/series/{mine}", {"title": "Friday"}).json()["title"] == "Friday"


# ---- the page ----


def test_the_page_carries_every_block_the_designer_drew(
    api, session, owner, daniel, template, weekly
):
    team_id = weekly.team_id
    last = meeting_in(session, template, weekly, recorded_seconds=2760)
    earlier = meeting_in(session, template, weekly, when=MONDAY - timedelta(days=7))
    task_in(session, team_id, last, "Open with owner", assignee=daniel)
    task_in(session, team_id, earlier, "Open without owner")
    task_in(
        session,
        team_id,
        earlier,
        "Finished since",
        status="done",
        done_at=MONDAY + timedelta(days=1),
    )
    task_in(
        session,
        team_id,
        earlier,
        "Finished long ago",
        status="done",
        done_at=MONDAY - timedelta(days=3),
    )
    question_in(session, last, "Which pricing model?")
    question_in(session, earlier, "Which pricing model?")  # asked twice: shown once
    question_in(session, earlier, "Resolved one", resolved_at=NOW)
    question_in(session, earlier, "Not carried", carry=False)
    session.add(SeriesAgendaItem(series_id=weekly.id, text="Budget", created_by=owner.id))
    session.commit()
    page = api.get(f"/api/series/{weekly.id}").json()
    assert page["title"] == "Monday planning" and page["can_rename"] is True
    assert {t["title"]: t["assignee"] for t in page["unfinished"]}.keys() == {
        "Open with owner",
        "Open without owner",
    }
    assert [t["title"] for t in page["finished"]] == ["Finished since"]
    assert page["finished_since_text"] == "Mon 2 Nov"
    assert [q["text"] for q in page["questions"]] == ["Which pricing model?"]
    assert page["questions"][0]["meeting"]["id"] == last.id  # from the newest
    assert [a["text"] for a in page["agenda"]] == ["Budget"]
    assert [(m["id"], m["tasks_count"]) for m in page["past_meetings"]] == [
        (last.id, 1),
        (earlier.id, 3),
    ]
    assert page["past_meetings"][0]["duration_seconds"] == 2760
    names = {a["name"]: a["is_teammate"] for a in page["attendees"]}
    assert names["Daniel Okoro"] is True and names["Guest Visitor"] is False
    assert page["agenda_recipients"] == 1  # Daniel; never the caller


def test_a_hand_made_series_is_private_to_its_maker_until_a_meeting_is_shared(
    api, app, session, owner, daniel, template, weekly
):
    meeting = meeting_in(session, template, weekly)
    other = sign_in(app, DANIEL_EMAIL)
    assert other.get("/api/series").json()["series"] == []
    error_of(other.get(f"/api/series/{weekly.id}"), 404, "not_found")
    share(session, meeting, owner)
    assert [c["title"] for c in other.get("/api/series").json()["series"]] == ["Monday planning"]


def test_a_teammates_private_meeting_is_invisible_on_the_page(
    api, app, session, owner, daniel, template, weekly
):
    team_id = weekly.team_id
    private = meeting_in(session, template, weekly)
    task_in(session, team_id, private, "Private task")
    question_in(session, private, "Private question")
    other = sign_in(app, DANIEL_EMAIL)
    error_of(
        other.get(f"/api/series/{weekly.id}"), 404, "not_found"
    )  # nothing of it is his to read
    other_meeting = meeting_in(session, template, weekly, when=MONDAY - timedelta(days=9))
    share(session, other_meeting, owner)
    page = other.get(f"/api/series/{weekly.id}").json()
    assert [t["title"] for t in page["unfinished"]] == []
    assert page["questions"] == [] and [m["id"] for m in page["past_meetings"]] == [
        other_meeting.id
    ]
    assert all(a["name"] != "x" for a in page["attendees"])
    share(session, private, owner)
    page = other.get(f"/api/series/{weekly.id}").json()
    assert [t["title"] for t in page["unfinished"]] == ["Private task"]
    assert [m["id"] for m in page["past_meetings"]] == [private.id, other_meeting.id]


# ---- the agenda ----


def test_the_agenda_is_plain_text_with_limits_and_author_or_manager_removal(
    api, app, session, owner, daniel, weekly, template
):
    share(session, meeting_in(session, template, weekly), owner)  # so the team can see the series
    url = f"/api/series/{weekly.id}/agenda"
    first = api.post(url, {"text": " Budget   review "})
    assert first.status_code == 201 and first.json()["text"] == "Budget review"
    error_of(api.post(url, {"text": "See https://evil.example/x"}), 422, "invalid_agenda_item")
    error_of(api.post(url, {"text": "go to www.evil.example"}), 422, "invalid_agenda_item")
    error_of(api.post(url, {"text": "   "}), 422, "invalid_agenda_item")
    other = sign_in(app, DANIEL_EMAIL)
    theirs = other.post(url, {"text": "Hiring"}).json()["id"]
    listed = other.get(url).json()
    assert {i["text"]: i["can_remove"] for i in listed} == {"Budget review": False, "Hiring": True}
    error_of(other.delete(f"{url}/{first.json()['id']}"), 403, "team_forbidden")
    assert api.delete(f"{url}/{theirs}").status_code == 204  # a manager may
    error_of(api.delete(f"{url}/{theirs}"), 404, "not_found")


def test_at_most_thirty_lines_wait(api, session, owner, weekly):
    url = f"/api/series/{weekly.id}/agenda"
    for n in range(series.MAX_AGENDA_ITEMS):
        assert api.post(url, {"text": f"Item {n}"}).status_code == 201
    error_of(api.post(url, {"text": "One more"}), 409, "agenda_full")


# ---- questions ----


def test_a_meetings_questions_and_the_toggle(api, session, owner, template, weekly):
    meeting = meeting_in(session, template, weekly)
    q = question_in(session, meeting, "Which pricing model?")
    detail = api.get(f"/api/meetings/{meeting.id}").json()
    assert detail["series"] == {"id": weekly.id, "title": "Monday planning"}
    assert detail["open_questions"] == [
        {
            "id": q.id,
            "text": "Which pricing model?",
            "carriable": True,
            "carry": True,
            "resolved": False,
            "can_update": True,
        }
    ]
    off = patch(api, f"/api/questions/{q.id}", {"carry": False}).json()
    assert (off["carry"], off["resolved"]) == (False, False)
    page = api.get(f"/api/series/{weekly.id}").json()
    assert page["questions"] == []
    on = patch(api, f"/api/questions/{q.id}", {"carry": True}).json()
    assert on["carry"] is True
    done = patch(api, f"/api/questions/{q.id}", {"resolved": True}).json()
    assert done["resolved"] is True
    session.expire_all()
    row = session.get(OpenQuestion, q.id)
    assert row.resolved_by == owner.id and row.resolved_at is not None
    # turning "On next agenda" back on reopens it, as the prototype's toggle does
    back = patch(api, f"/api/questions/{q.id}", {"carry": True}).json()
    assert (back["carry"], back["resolved"]) == (True, False)


def test_a_question_outside_a_series_cannot_be_carried(api, session, template):
    meeting = ready(session, template)
    q = question_in(session, meeting, "Loose question")
    detail = api.get(f"/api/meetings/{meeting.id}").json()
    assert detail["series"] is None and detail["open_questions"][0]["carriable"] is False
    error_of(patch(api, f"/api/questions/{q.id}", {"carry": True}), 409, "not_in_series")
    assert patch(api, f"/api/questions/{q.id}", {"resolved": True}).status_code == 200
    error_of(patch(api, f"/api/questions/{q.id}", {}), 422, "validation_error")


def test_resolving_a_question_asked_twice_closes_both(api, session, template, weekly):
    a = question_in(session, meeting_in(session, template, weekly), "Pricing?")
    b = question_in(
        session, meeting_in(session, template, weekly, when=MONDAY - timedelta(days=7)), "Pricing?"
    )
    patch(api, f"/api/questions/{a.id}", {"resolved": True})
    session.expire_all()
    assert session.get(OpenQuestion, b.id).resolved_at is not None


def test_who_may_change_a_question(api, app, session, owner, daniel, template, weekly):
    meeting = meeting_in(session, template, weekly)
    share(session, meeting, owner)
    q = question_in(session, meeting, "Pricing?")
    member = sign_in(app, DANIEL_EMAIL)
    # a member can read it (the meeting is shared) but not change it
    detail = member.get(f"/api/meetings/{meeting.id}").json()
    assert detail["open_questions"][0]["can_update"] is False
    error_of(patch(member, f"/api/questions/{q.id}", {"resolved": True}), 403, "team_forbidden")
    # an admin of the team may
    team_member = session.scalars(select(TeamMember).where(TeamMember.user_id == daniel.id)).one()
    team_member.role = "admin"
    session.commit()
    assert patch(member, f"/api/questions/{q.id}", {"resolved": True}).status_code == 200


def test_a_question_you_cannot_read_is_not_found(
    api, app, session, owner, daniel, template, weekly
):
    meeting = meeting_in(session, template, weekly)  # private
    q = question_in(session, meeting, "Secret?")
    member = sign_in(app, DANIEL_EMAIL)
    error_of(patch(member, f"/api/questions/{q.id}", {"resolved": True}), 404, "not_found")
    error_of(patch(member, "/api/questions/999999", {"resolved": True}), 404, "not_found")


def test_a_teammate_does_not_see_the_series_of_a_meeting_when_their_team_is_not_its_team(
    api, app, session, owner, daniel, template, weekly
):
    meeting = meeting_in(session, template, weekly)
    share(session, meeting, owner)
    # Daniel leaves: a fresh team of one. The shared meeting is unshared and is not his to read.
    teams.leave(session, daniel, NOW)
    session.commit()
    other = sign_in(app, DANIEL_EMAIL)
    error_of(other.get(f"/api/meetings/{meeting.id}"), 404, "not_found")
    # and his own meetings left the old team's series
    mine = ready(session, template)
    mine.owner_user_id, mine.series_id = daniel.id, weekly.id
    session.commit()
    detail = other.get(f"/api/meetings/{mine.id}").json()
    assert detail["series"] is None


# ---- teams change ----


def test_joining_a_team_brings_the_series_along_and_merges_a_twin(session, owner, daniel, template):
    # Daniel's team of one has two series; one is the owner's team's recurring event too.
    own_team = teams.ensure_team(session, owner)
    twin_mine = Series(team_id=own_team.id, title="Monday", calendar_recurring_id="rid-1")
    session.add(twin_mine)
    session.commit()
    solo = make_user(session, "solo@example.test", name="Solo")
    solo_team = teams.ensure_team(session, solo)
    twin_theirs = Series(
        team_id=solo_team.id, title="Monday (theirs)", calendar_recurring_id="rid-1"
    )
    unique = Series(team_id=solo_team.id, title="Only mine", calendar_recurring_id="rid-2")
    session.add_all([twin_theirs, unique])
    session.commit()
    meeting = ready(session, template, series_id=twin_theirs.id)
    meeting.owner_user_id = solo.id
    session.add(SeriesAgendaItem(series_id=twin_theirs.id, text="Carry me", created_by=solo.id))
    session.commit()
    teams.join(session, solo, own_team.id, "member", None, NOW)
    session.commit()
    session.expire_all()
    assert session.get(Meeting, meeting.id).series_id == twin_mine.id  # merged into the twin
    assert session.get(Series, twin_theirs.id) is None
    assert session.get(Series, unique.id).team_id == own_team.id
    assert [i.text for i in series.agenda_items(session, twin_mine)] == ["Carry me"]


def test_leaving_a_team_takes_your_meetings_out_of_its_series(
    session, owner, daniel, template, weekly
):
    mine = meeting_in(session, template, weekly, user=daniel)
    theirs = meeting_in(session, template, weekly)
    teams.leave(session, daniel, NOW)
    session.commit()
    session.expire_all()
    assert session.get(Meeting, mine.id).series_id is None
    assert session.get(Meeting, theirs.id).series_id == weekly.id


# ---- reminder preference and the team's switch ----


def test_the_persons_reminder_preference(api, session, owner):
    assert api.get("/api/settings/reminders").json() == {"notify_reminders": True}
    assert api.put("/api/settings/reminders", {"notify_reminders": False}).json() == {
        "notify_reminders": False
    }
    assert api.get("/api/settings/reminders").json() == {"notify_reminders": False}


def test_only_a_manager_switches_the_teams_reminders(api, app, session, owner, daniel):
    team = api.get("/api/team").json()
    assert (team["reminders_on"], team["reminder_timing"]) == (False, "both")
    changed = patch(api, "/api/team", {"reminders_on": True, "reminder_timing": "30m"}).json()
    assert (changed["reminders_on"], changed["reminder_timing"]) == (True, "30m")
    error_of(patch(api, "/api/team", {"reminder_timing": "2h"}), 422, "validation_error")
    member = sign_in(app, DANIEL_EMAIL)
    error_of(patch(member, "/api/team", {"reminders_on": False}), 403, "team_forbidden")


# ---- "Make this a recurring meeting?" ----


def test_a_reused_link_is_offered_and_makes_a_series_of_every_meeting_with_that_code(
    api, session, owner, template
):
    first = ready(session, template, created_at=MONDAY, native_id="reuse-aaa")
    second = ready(session, template, created_at=MONDAY + timedelta(days=7), native_id="reuse-aaa")
    other = ready(session, template, native_id="different-code")
    assert api.get(f"/api/meetings/{second.id}").json()["suggest_recurring"] is True
    assert api.get(f"/api/meetings/{other.id}").json()["suggest_recurring"] is False
    made = api.post(f"/api/meetings/{second.id}/recurring")
    assert made.status_code == 201
    body = made.json()["series"]
    session.expire_all()
    assert session.get(Meeting, first.id).series_id == body["id"]
    assert session.get(Meeting, second.id).series_id == body["id"]
    assert session.get(Meeting, other.id).series_id is None
    assert api.get(f"/api/meetings/{second.id}").json()["suggest_recurring"] is False


def test_a_link_used_once_is_not_recurring(api, session, template):
    only = ready(session, template, native_id="once-only")
    assert api.get(f"/api/meetings/{only.id}").json()["suggest_recurring"] is False
    error_of(api.post(f"/api/meetings/{only.id}/recurring"), 409, "not_reused")


def test_only_the_owner_is_offered_it_and_may_do_it(api, app, session, owner, daniel, template):
    a = ready(session, template, native_id="shared-code")
    b = ready(session, template, native_id="shared-code")
    share(session, b, owner)
    other = sign_in(app, DANIEL_EMAIL)
    assert other.get(f"/api/meetings/{b.id}").json()["suggest_recurring"] is False
    error_of(other.post(f"/api/meetings/{b.id}/recurring"), 404, "not_found")
    error_of(other.post(f"/api/meetings/{a.id}/recurring"), 404, "not_found")  # private: not theirs
