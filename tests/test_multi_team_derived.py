"""Multi-team stage 1, the reads that sit on top of the rule: Ask, search, the series page, repeat
topics and the suggested agenda, with a person in two teams.

Same cast as `tests/test_multi_team_leaks.py` (`world`): Ada records and owns team X; Pat is in X
and Y; Quinn owns Y and is only in Y; Zed owns Z. Everyone has a personal team, the switch is on.
A series belongs to ONE team (X here) and a route that names no team acts on the caller's default
team, so a person with two ordinary teams reads the personal team's series until stage 2 adds an
explicit team: those cases are asserted as they are (a 404, never a leak, never a 500).
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import series as series_rules
from app import teams, topics
from app.models import Decision, Meeting, Series, TeamMember
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    template,
)
from tests.test_ask import NOTES as ASK_NOTES
from tests.test_ask import model, passages_of, post  # noqa: F401
from tests.test_multi_team_leaks import PAT, QUINN, multi_team_on, new_team, world  # noqa: F401
from tests.test_series import MONDAY, fixed_clock, meeting_in, question_in, task_in  # noqa: F401
from tests.test_team_visibility import sign_in
from tests.test_topics import THEN, FakeTopics, message_of, numbers_of, run, week
from tests.test_web import make_user

DAN = "dan@example.test"


def passages_text(call) -> str:
    return " ".join(passages_of(call).values())


# ---- Ask ----


def mark(meeting, word):
    meeting.notes = ASK_NOTES.replace("pricing", f"pricing {word}", 1)


def test_ask_finds_a_meeting_shared_to_x_through_x_and_never_one_the_asker_may_not_read(
    app, session, world, model
):
    mark(world.in_x, "XRAYMARK")
    mark(world.private, "PRIVMARK")
    mark(world.in_z, "ZULUMARK")
    session.commit()
    pat = sign_in(app, PAT)
    assert post(pat, "What was said about pricing?").status_code == 200
    seen = passages_text(model.calls[0])
    assert "XRAYMARK" in seen
    assert "PRIVMARK" not in seen and "ZULUMARK" not in seen


def test_ask_by_a_person_only_in_y_reads_nothing_of_x_and_makes_no_call(app, session, world, model):
    mark(world.in_x, "XRAYMARK")
    session.commit()
    quinn = sign_in(app, QUINN)
    answer = post(quinn, "What was said about pricing?")
    assert answer.status_code == 200
    assert all("XRAYMARK" not in passages_text(call) for call in model.calls)
    assert model.calls == []  # nothing readable: no passages, so no model call


def test_ask_stops_finding_a_meeting_once_it_is_unshared(app, session, world, model):
    mark(world.in_x, "XRAYMARK")
    world.in_x.shared_team_id = None
    session.commit()
    post(sign_in(app, PAT), "What was said about pricing?")
    assert model.calls == []


# ---- search (the decisions record) ----


def add_decision(session, meeting, text):
    session.add(Decision(meeting_id=meeting.id, text=text, position=0, fingerprint=text))
    session.commit()


def test_search_finds_a_decision_shared_to_x_for_a_member_of_x_and_for_nobody_only_in_y(
    app, session, world
):
    add_decision(session, world.in_x, "Raise the XRAYPRICE")
    add_decision(session, world.private, "Cut the PRIVPRICE")
    add_decision(session, world.in_z, "Freeze the ZULUPRICE")
    pat, quinn = sign_in(app, PAT), sign_in(app, QUINN)
    for word, expected in (("XRAYPRICE", [world.in_x.id]), ("PRIVPRICE", []), ("ZULUPRICE", [])):
        found = pat.get(f"/api/decisions?q={word}").json()["groups"]
        assert [g["meeting"]["id"] for g in found] == expected, word
        assert quinn.get(f"/api/decisions?q={word}").json()["groups"] == [], word
    world.in_x.shared_team_id = None
    session.commit()
    assert pat.get("/api/decisions?q=XRAYPRICE").json()["groups"] == []  # unsharing hides it again


# ---- the series page, repeat topics and the suggested agenda ----


@pytest.fixture
def dan(session, world):
    """A member of X and nothing else but a personal team: their default team is X."""
    user = make_user(session, DAN, name="Dan Okoro")
    teams.ensure_personal_team(session, user)
    session.add(TeamMember(team_id=world.x.id, user_id=user.id, role="member"))
    session.commit()
    return user


@pytest.fixture
def weekly_x(session, world):
    found = Series(team_id=world.x.id, owner_user_id=world.ada.id, title="Monday planning")
    session.add(found)
    session.commit()
    return found


def test_the_series_page_of_x_shows_a_member_of_x_only_what_was_shared_to_x(
    app, session, template, world, dan, weekly_x
):
    shared = meeting_in(session, template, weekly_x, when=MONDAY - timedelta(days=9))
    shared.shared_team_id = world.x.id
    private = meeting_in(session, template, weekly_x)
    # a series meeting its recorder shared to ANOTHER team (Y): not part of X's series view
    to_y = meeting_in(session, template, weekly_x, when=MONDAY - timedelta(days=18))
    to_y.shared_team_id = world.y.id
    task_in(session, world.x.id, private, "Private task")
    question_in(session, private, "Private question")
    session.commit()
    page = sign_in(app, DAN).get(f"/api/series/{weekly_x.id}")
    assert page.status_code == 200
    body = page.json()
    assert [m["id"] for m in body["past_meetings"]] == [shared.id]
    assert body["unfinished"] == [] and body["questions"] == []
    assert body["suggested_agenda"] == []


def test_a_person_only_in_y_gets_404_for_the_series_of_x_and_its_agenda(
    app, session, template, world, weekly_x
):
    meeting_in(session, template, weekly_x).shared_team_id = world.x.id
    session.commit()
    quinn = sign_in(app, QUINN)
    error_of(quinn.get(f"/api/series/{weekly_x.id}"), 404, "not_found")
    error_of(quinn.get(f"/api/series/{weekly_x.id}/agenda"), 404, "not_found")
    assert quinn.get("/api/series").json()["series"] == []


def test_a_person_in_two_ordinary_teams_reads_the_personal_teams_series_never_an_error(
    app, session, template, world, weekly_x
):
    """Stage 1: no team is named, so the default team (personal) is used. Not a leak, not a 500."""
    meeting_in(session, template, weekly_x).shared_team_id = world.x.id
    session.commit()
    pat = sign_in(app, PAT)
    error_of(pat.get(f"/api/series/{weekly_x.id}"), 404, "not_found")
    assert pat.get("/api/series").json()["series"] == []


def test_the_suggested_agenda_counts_only_what_a_two_team_reader_may_read(
    app, session, template, world, dan, weekly_x
):
    week(session, template, weekly_x, 0, "a")
    private = week(session, template, weekly_x, 1, "b", shared=False)
    late = task_in(session, world.x.id, private, "Hidden late")
    late.due_date = MONDAY.date() - timedelta(days=30)
    question_in(session, private, "A private question")
    session.commit()
    assert sign_in(app, DAN).get(f"/api/series/{weekly_x.id}").json()["suggested_agenda"] == []
    owner_page = sign_in(app, "owner@example.test").get(f"/api/series/{weekly_x.id}").json()
    assert [a["kind"] for a in owner_page["suggested_agenda"]] == ["overdue", "questions"]


def test_repeat_topics_read_only_meetings_shared_to_the_series_team(
    session, template, world, weekly_x
):
    week(session, template, weekly_x, 0, "Shared pricing talk")
    week(session, template, weekly_x, 1, "Shared pricing again")
    week(session, template, weekly_x, 2, "YWORD pricing for Y", shared=False,
         shared_team_id=world.y.id)  # fmt: skip
    week(session, template, weekly_x, 3, "PRIVWORD pricing", shared=False)
    fake = FakeTopics()
    run(session, fake)
    sent = message_of(fake.calls[0])
    assert "YWORD" not in sent and "PRIVWORD" not in sent
    assert numbers_of(fake.calls[0]) == [1, 2]
    assert {m.shared_team_id for m in topics.source_meetings(session, weekly_x)} == {world.x.id}
    # shared to the series team instead, it is read: the series team is the only team that counts
    to_y = session.scalars(select(Meeting).where(Meeting.shared_team_id == world.y.id)).one()
    to_y.shared_team_id = world.x.id
    series_rules.mark_topics_stale(session, weekly_x.id)
    session.commit()
    run(session, fake, THEN + timedelta(days=1))
    assert "YWORD" in message_of(fake.calls[1]) and "PRIVWORD" not in message_of(fake.calls[1])
