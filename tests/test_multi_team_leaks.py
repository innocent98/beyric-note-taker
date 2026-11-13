"""Multi-team stage 1: nothing of one team reaches someone who is only in another team.

Written before the code (docs/superpowers/specs/2026-11-13-multi-team-design.md, stage 1). The cast,
all with a personal team of their own and `MULTI_TEAM_ENABLED` on:

* `owner` (Ada) records the meetings and owns team X.
* `pat` is in X and in Y: the person with two memberships.
* `quinn` owns team Y with `pat` in it, and is not in X: "only in Y".
* `zed` owns team Z, which neither Ada nor Pat is in.

The one rule is `app/visibility.py`; these tests prove it through the service, every meeting route,
the derived reads (decisions, open questions, tasks), and the mail's "Open notes" button, and that
no single-team lookup raises or picks an arbitrary row once a person has two memberships.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app import mail, mail_templates, teams, visibility
from app.models import (
    Decision,
    MailOutbox,
    Meeting,
    MeetingTranslation,
    OpenQuestion,
    Team,
    TeamMember,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    template,
)
from tests.test_api_tasks import add_task
from tests.test_shares import body as share_body
from tests.test_shares import send_all
from tests.test_shares import url as shares_url
from tests.test_team_visibility import OWNER_ONLY, READS, call, sign_in
from tests.test_web import make_user

PAT, QUINN, ZED = "pat@example.test", "quinn@example.test", "zed@example.test"


@pytest.fixture(autouse=True)
def multi_team_on(monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: True)


def new_team(session, boss, name, *members) -> Team:
    """An ordinary (not personal) team: `boss` owns it, `members` are plain members."""
    team = Team(owner_user_id=boss.id, name=name, is_personal=False)
    session.add(team)
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=boss.id, role="owner"))
    for user in members:
        session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return team


def ids_of(session, user) -> list[int]:
    return sorted(m.id for m in session.scalars(visibility.visible_meetings(user)))


class World:
    """Everything the tests need, built once per test."""


@pytest.fixture
def world(session, template, owner) -> World:
    w = World()
    w.ada, w.pat = owner, make_user(session, PAT)
    w.quinn, w.zed = make_user(session, QUINN), make_user(session, ZED)
    for user in (w.ada, w.pat, w.quinn, w.zed):
        teams.ensure_personal_team(session, user)
    session.commit()
    w.x = new_team(session, w.ada, "X", w.pat)
    w.y = new_team(session, w.quinn, "Y", w.pat)
    w.z = new_team(session, w.zed, "Z")
    w.in_x = ready(session, template, shared_team_id=w.x.id)  # Ada's, shared to X
    w.private = ready(session, template)  # Ada's, shared to nobody
    w.in_z = ready(session, template, owner_user_id=w.zed.id, shared_team_id=w.z.id)
    for meeting in (w.in_x, w.private, w.in_z):
        session.add(
            MeetingTranslation(
                meeting_id=meeting.id,
                language="fr",
                status="ready",
                notes_md="Notes",
                transcript="Texte",
                source_hash="x",
                model="m",
            )
        )
    session.commit()
    return w


# ---- the one rule, with two memberships ----


def test_a_person_in_x_and_y_reads_a_meeting_shared_to_x_through_the_x_membership(session, world):
    assert ids_of(session, world.pat) == [world.in_x.id]
    session.execute(
        delete(TeamMember).where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.y.id
        )
    )
    session.commit()
    assert ids_of(session, world.pat) == [world.in_x.id]  # Y was never the way in
    session.execute(
        delete(TeamMember).where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.x.id
        )
    )
    session.commit()
    assert ids_of(session, world.pat) == []  # X was


def test_no_meeting_shared_to_a_team_they_are_not_in_is_readable(session, world):
    assert world.in_z.id not in ids_of(session, world.pat)
    assert ids_of(session, world.zed) == [world.in_z.id]
    assert ids_of(session, world.ada) == sorted([world.in_x.id, world.private.id])


def test_a_person_only_in_y_never_sees_a_meeting_of_x(session, world):
    assert ids_of(session, world.quinn) == []
    clause = visibility.can_read_clause(world.quinn)
    assert (
        session.scalars(select(Meeting.id).where(Meeting.id == world.in_x.id, clause)).all() == []
    )


def test_unsharing_hides_the_meeting_again_from_the_person_in_two_teams(session, world):
    world.in_x.shared_team_id = None
    session.commit()
    assert ids_of(session, world.pat) == []


def test_a_meeting_shared_to_a_personal_team_is_readable_by_nobody_else(session, world):
    """The personal team has one member: sharing to it cannot widen who reads."""
    personal = teams.personal_team(session, world.ada.id)
    world.private.shared_team_id = personal.id
    session.commit()
    assert world.private.id not in ids_of(session, world.pat)
    assert world.private.id not in ids_of(session, world.quinn)


def test_a_meeting_owned_by_a_team_is_read_by_that_teams_members_only(session, world):
    """Stage 2 fills `owner_team_id`; the rule already honours it (unused until then)."""
    world.private.owner_team_id = world.x.id
    session.commit()
    assert world.private.id in ids_of(session, world.pat)
    assert world.private.id not in ids_of(session, world.quinn)
    assert world.private.id not in ids_of(session, world.zed)


# ---- every meeting route ----


def test_a_person_only_in_y_gets_404_on_every_meeting_route(app, world):
    quinn = sign_in(app, QUINN)
    for meeting in (world.in_x, world.private):
        for method, path in sorted(READS | OWNER_ONLY):
            error_of(call(quinn, method, path, meeting), 404, "not_found")


def test_the_person_in_two_teams_reads_x_and_never_changes_it_or_reads_the_private_one(app, world):
    pat = sign_in(app, PAT)
    for method, path in sorted(READS):
        assert call(pat, method, path, world.in_x).status_code == 200, (method, path)
    for method, path in sorted(OWNER_ONLY):
        error_of(call(pat, method, path, world.in_x), 404, "not_found")
    for method, path in sorted(READS | OWNER_ONLY):
        error_of(call(pat, method, path, world.private), 404, "not_found")
        error_of(call(pat, method, path, world.in_z), 404, "not_found")
    assert [m["id"] for m in pat.get("/api/meetings").json()["meetings"]] == [world.in_x.id]


def test_unsharing_closes_every_read_route_for_the_person_in_two_teams(app, session, world):
    pat = sign_in(app, PAT)
    assert pat.get(f"/api/meetings/{world.in_x.id}").status_code == 200
    world.in_x.shared_team_id = None
    session.commit()
    for method, path in sorted(READS):
        error_of(call(pat, method, path, world.in_x), 404, "not_found")


# ---- derived data: decisions, open questions, tasks ----


def add_derived(session, meeting, label, team):
    session.add(
        Decision(meeting_id=meeting.id, text=f"{label} choice", position=0, fingerprint=label)
    )
    session.add(
        OpenQuestion(
            meeting_id=meeting.id, text=f"{label} question?", position=0, fingerprint=label
        )
    )
    session.commit()
    return add_task(session, team, meeting, title=f"{label} task")


def test_decisions_open_questions_and_tasks_follow_the_meeting_never_the_team_alone(
    app, session, world
):
    in_x = add_derived(session, world.in_x, "shared", world.x)
    hidden = add_derived(session, world.private, "private", world.x)  # a legacy task on a team
    quinn, pat = sign_in(app, QUINN), sign_in(app, PAT)

    assert quinn.get("/api/decisions").json()["groups"] == []
    assert quinn.get("/api/tasks").json()["tasks"] == []
    error_of(quinn.get(f"/api/tasks/{in_x.id}"), 404, "not_found")
    error_of(quinn.get(f"/api/meetings/{world.in_x.id}/tasks"), 404, "not_found")
    question = session.scalars(
        select(OpenQuestion).where(OpenQuestion.meeting_id == world.in_x.id)
    ).one()
    from app import series

    assert series.question_for(session, world.quinn, question.id) is None
    assert series.question_for(session, world.pat, question.id) is not None

    groups = pat.get("/api/decisions").json()["groups"]
    assert [g["meeting"]["id"] for g in groups] == [world.in_x.id]
    assert [t["id"] for t in pat.get("/api/tasks").json()["tasks"]] == [in_x.id]
    assert pat.get(f"/api/tasks/{in_x.id}").status_code == 200
    error_of(pat.get(f"/api/tasks/{hidden.id}"), 404, "not_found")


def test_a_manual_task_of_team_x_is_not_seen_by_a_person_only_in_y_and_is_by_both_x_members(
    app, session, world
):
    manual_x = add_task(session, world.x, title="Book the room")
    manual_y = add_task(session, world.y, title="Pay the invoice")
    ada, pat, quinn = sign_in(app, "owner@example.test"), sign_in(app, PAT), sign_in(app, QUINN)
    assert {t["id"] for t in ada.get("/api/tasks").json()["tasks"]} == {manual_x.id}
    assert {t["id"] for t in pat.get("/api/tasks").json()["tasks"]} == {manual_x.id, manual_y.id}
    assert {t["id"] for t in quinn.get("/api/tasks").json()["tasks"]} == {manual_y.id}


# ---- the mail's "Open notes" button ----


def open_notes_for(app, session, world, email) -> bool:
    """Share the Ada's meeting by mail to `email`; True when the mail links to the meeting."""
    sender = sign_in(app, "owner@example.test")
    assert sender.post(shares_url(world.in_x), share_body(email)).status_code == 202
    _, mailer = send_all(session, mail_recipient_daily_cap=0)
    message = next(m for m, *_ in mailer.sent if m.to == email)
    return f"/meetings/{world.in_x.id}" in message.html


def test_the_open_notes_button_reaches_the_person_in_two_teams_and_not_the_one_only_in_y(
    app, session, world
):
    assert open_notes_for(app, session, world, PAT) is True
    assert open_notes_for(app, session, world, QUINN) is False


def test_the_open_notes_button_is_gone_after_unsharing(app, session, world):
    world.in_x.shared_team_id = None
    session.commit()
    assert open_notes_for(app, session, world, PAT) is False


def test_the_assigned_task_mail_names_a_meeting_only_to_someone_who_may_read_it(session, world):
    from app.config import Settings  # noqa: F401
    from tests.test_web import make_settings

    settings = make_settings(public_base_url="https://notes.example.test")
    task_shared = add_task(session, world.x, world.in_x, title="Ship it", assignee=world.pat)
    task_private = add_task(
        session, world.x, world.private, title="Secret plan", assignee=world.pat
    )
    for meeting in (world.in_x, world.private):
        mail.queue_tasks_assigned(session, meeting, [world.pat.id])
    session.commit()
    rows = {
        r.payload["meeting_id"]: r
        for r in session.scalars(select(MailOutbox).order_by(MailOutbox.id))
    }
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    shared = mail_templates.prepare(
        session, rows[world.in_x.id].template, rows[world.in_x.id].payload, PAT, settings, now
    )
    private = mail_templates.prepare(
        session, rows[world.private.id].template, rows[world.private.id].payload, PAT, settings, now
    )
    assert task_shared.title in shared.text
    assert task_private.title in private.text  # the assignee's own task is told
    assert "A meeting of owner" in private.text  # without the meeting's name: they may not read it
    assert "A meeting of owner" not in shared.text


# ---- nothing that looks up "their team" raises with two memberships ----


def test_no_single_team_lookup_raises_with_two_memberships(session, world, template):
    from app import series, shares, tasks, team_admin, workspace  # noqa: F401

    pat = world.pat
    assert len(session.scalars(select(TeamMember).where(TeamMember.user_id == pat.id)).all()) == 3
    # The default team of someone in two ordinary teams is their personal team.
    personal = teams.personal_team(session, pat.id)
    assert teams.team_of(session, pat.id).id == personal.id
    assert teams.ensure_team(session, pat).id == personal.id
    assert teams.membership(session, pat.id).team_id == personal.id
    assert teams.membership(session, pat.id, world.x.id).team_id == world.x.id
    assert team_admin.my_membership(session, pat).team_id == personal.id
    assert tasks.membership(session, pat).team_id == personal.id
    assert tasks.membership(session, pat, world.y.id).team_id == world.y.id
    assert series.team_id_of(session, pat) == personal.id
    assert series.membership(session, pat).team_id == personal.id
    assert series.is_manager(session, pat, world.x.id) is False
    assert series.is_manager(session, world.quinn, world.y.id) is True
    # someone with exactly one ordinary team keeps landing on it (what an old client sees)
    assert teams.team_of(session, world.ada.id).id == world.x.id
    assert teams.team_of(session, world.zed.id).id == world.z.id
    # who counts as a teammate of a sharer: any of the sender's teams
    found = shares._team_members_by_email(session, world.quinn, [PAT, "owner@example.test"])
    assert set(found) == {PAT}
    assert set(
        shares._team_members_by_email(session, pat, [PAT, QUINN, "owner@example.test", ZED])
    ) == {
        PAT,
        QUINN,
        "owner@example.test",
    }
    # the share default is read from the default team only and never raises
    meeting = ready(session, template, owner_user_id=pat.id)
    teams.apply_share_default(session, meeting)
    assert meeting.shared_team_id is None


def test_every_get_route_answers_without_an_error_for_a_person_with_two_memberships(
    app, session, world
):
    """A server error would raise out of the test client: `MultipleResultsFound` and friends."""
    pat = sign_in(app, PAT)
    for method, path in api_routes(app):
        if method == "GET" and "{" not in path and "/calendar" not in path and "/admin" not in path:
            assert pat.get(path).status_code < 500, path


def test_a_person_with_two_ordinary_teams_can_share_a_meeting_and_the_default_team_is_used(
    app, session, template, world
):
    mine = ready(session, template, owner_user_id=world.pat.id)
    pat = sign_in(app, PAT)
    assert pat.post(f"/api/meetings/{mine.id}/share").status_code == 200
    session.refresh(mine)
    assert mine.shared_team_id == teams.personal_team(session, world.pat.id).id  # default: personal
    ada = sign_in(app, "owner@example.test")
    assert ada.post(f"/api/meetings/{world.private.id}/share").status_code == 200
    session.refresh(world.private)
    assert world.private.shared_team_id == world.x.id  # exactly one ordinary team: that one
