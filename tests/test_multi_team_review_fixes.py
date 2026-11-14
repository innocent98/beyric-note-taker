"""Multi-team stage 2a, the Opus review's findings (PR #182): written before the fixes.

Cast and fixtures are `tests/test_multi_team_leaks.py`'s (`world`) and `test_multi_team_moved.py`'s.
Ada records and owns team X; Pat is in X and Y; Quinn owns Y; Zed owns Z.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import threading
import time
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import deletion, meeting_teams, services, teams, workspace
from app import notifications as notifications_rules
from app.models import (
    CalendarConnection,
    Integration,
    Meeting,
    Milestone,
    Notification,
    ReviewItem,
    Task,
    User,
    UserSettings,
)
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
    ready,
    template,
)
from tests.test_api_tasks import add_task
from tests.test_multi_team_leaks import PAT, QUINN, multi_team_on, new_team, world  # noqa: F401
from tests.test_multi_team_moved import ADA, DEE, NoVexa, dee, move  # noqa: F401
from tests.test_team_visibility import sign_in
from tests.test_workspace import GOOD, NOTES, FakeChat


def add_review(session, meeting, text="Metrika may be misheard", position=0):
    session.add(
        ReviewItem(
            meeting_id=meeting.id,
            owner_user_id=meeting.owner_user_id,
            text=text,
            position=position,
            fingerprint=f"fp-{position}",
        )
    )
    session.commit()


def ada_leaves_x(session, world):
    """Ada hands X to Pat (the pilot admin does it by hand) and leaves it."""
    teams.transfer_owner(session, world.x.id, world.pat.id)
    session.commit()
    teams.leave(session, world.ada, datetime.now(UTC), team_id=world.x.id)
    session.commit()


# ---- 1. the Review screen reads through the one rule ----


def test_a_recorder_who_left_the_team_gets_nothing_of_the_moved_meeting_from_the_review_screen(
    app, session, world
):
    add_review(session, world.private)
    ada = sign_in(app, ADA)
    assert move(ada, world.private, world.x).status_code == 200
    body = ada.get("/api/review").json()
    assert [i["text"] for i in body["items"]] == ["Metrika may be misheard"]  # still a member
    ada_leaves_x(session, world)
    body = ada.get("/api/review").json()
    assert body["items"] == [] and body["open_count"] == 0
    assert ada.get("/api/review?state=checked").json()["items"] == []
    item = session.scalars(select(ReviewItem)).one()
    for path in ("check", "uncheck", "vocabulary"):
        error_of(ada.post(f"/api/review/{item.id}/{path}", {"term": "Metrika"}), 404, "not_found")
    assert workspace.open_review_count(session, world.ada) == 0
    assert workspace.review_item(session, world.ada, item.id) is None
    assert ada.get("/api/session").json()["badges"]["review_open"] == 0


def test_a_rewrite_after_the_recorder_left_writes_no_review_items_for_them(session, world):
    ada_leaves_x(session, world)
    world.private.owner_team_id = world.x.id
    world.private.notes = NOTES
    world.private.extraction_status = "pending"
    session.commit()
    workspace.run_extraction(session, world.private, client=FakeChat([GOOD]))
    assert (
        session.scalars(select(ReviewItem)).all() == []
    )  # nothing for a person who cannot read it


def test_the_recorder_who_is_still_a_member_keeps_their_review_items_on_a_rewrite(session, world):
    world.private.owner_team_id = world.x.id
    world.private.notes = NOTES
    world.private.extraction_status = "pending"
    session.commit()
    workspace.run_extraction(session, world.private, client=FakeChat([GOOD]))
    assert len(session.scalars(select(ReviewItem)).all()) == 2


# ---- 2. extraction files into the team the meeting belongs to NOW ----


def other_session(session) -> Session:
    return Session(session.get_bind())


def run_like_the_worker(session, meeting_id, client):
    """`app/db.py` sessions do not expire on commit: the meeting object a running extraction holds
    keeps what it read when it claimed the meeting, however long the model takes."""
    with Session(session.get_bind(), expire_on_commit=False) as worker:
        workspace.run_extraction(worker, worker.get(Meeting, meeting_id), client=client)


def test_a_move_during_the_model_call_files_the_tasks_in_the_new_team(app, session, world):
    meeting_id = world.in_x.id  # shared to X
    world.in_x.notes = NOTES
    world.in_x.extraction_status = "pending"
    other = new_team(session, world.ada, "Other")
    session.commit()

    def moved_meanwhile():
        with other_session(session) as s2:
            m = s2.get(Meeting, meeting_id)
            meeting_teams.move(s2, s2.get(User, world.ada.id), m, other.id)
            s2.commit()

    run_like_the_worker(session, meeting_id, FakeChat([GOOD], on_call=moved_meanwhile))
    session.expire_all()
    found = session.scalars(select(Task).where(Task.meeting_id == meeting_id)).all()
    assert found and {t.team_id for t in found} == {other.id}  # never X, which cannot read it now


def test_an_unshare_during_the_model_call_files_nothing_in_the_old_team(app, session, world):
    meeting_id = world.in_x.id
    world.in_x.notes = NOTES
    world.in_x.extraction_status = "pending"
    session.commit()
    home = teams.personal_team(session, world.ada.id)

    def unshared_meanwhile():
        with other_session(session) as s2:
            meeting_teams.unshare(s2, s2.get(Meeting, meeting_id))
            s2.commit()

    run_like_the_worker(session, meeting_id, FakeChat([GOOD], on_call=unshared_meanwhile))
    session.expire_all()
    found = session.scalars(select(Task).where(Task.meeting_id == meeting_id)).all()
    assert found and {t.team_id for t in found} == {home.id}


def test_no_milestone_or_post_goes_to_a_team_that_can_no_longer_read_the_meeting(
    app, session, world, monkeypatch
):
    from app import integrations

    posted = []
    monkeypatch.setattr(
        integrations, "after_extraction", lambda s, m, team_id, *a, **k: posted.append(team_id)
    )
    meeting_id = world.in_x.id
    world.in_x.notes = NOTES
    world.in_x.extraction_status = "pending"
    other = new_team(session, world.ada, "Other")
    session.commit()

    def moved_meanwhile():
        with other_session(session) as s2:
            meeting_teams.move(
                s2, s2.get(User, world.ada.id), s2.get(Meeting, meeting_id), other.id
            )
            s2.commit()

    run_like_the_worker(session, meeting_id, FakeChat([GOOD], on_call=moved_meanwhile))
    assert world.x.id not in posted
    assert session.scalars(select(Milestone).where(Milestone.team_id == world.x.id)).all() == []


# ---- 3. email shares on a moved meeting are the recorder's ----


def test_team_admins_cannot_list_or_create_email_shares_of_a_moved_meeting(
    app, session, world, dee
):
    assert move(sign_in(app, ADA), world.private, world.x).status_code == 200
    admin = sign_in(app, DEE)
    error_of(admin.get(f"/api/meetings/{world.private.id}/shares"), 404, "not_found")
    body = {"recipients": [{"email": "x@example.test"}], "language": "en"}
    error_of(admin.post(f"/api/meetings/{world.private.id}/shares", body), 404, "not_found")
    ada = sign_in(app, ADA)
    assert ada.get(f"/api/meetings/{world.private.id}/shares").status_code == 200


# ---- 4. a meeting given back by a deleted team keeps its tasks ----


def test_a_meeting_given_back_by_a_deleted_team_keeps_its_tasks_and_milestones(app, session, world):
    """Pat owns W alone; Ada's meeting was moved to W and Ada left. Pat's account is deleted: the
    meeting is Ada's again, with its tasks (in her personal team) and its date proposals."""
    team_w = new_team(session, world.pat, "W", world.ada)
    world.private.owner_team_id = team_w.id
    session.commit()
    task = add_task(session, team_w, world.private, title="Send the deck", assignee=world.ada)
    session.add(
        Milestone(
            team_id=team_w.id,
            name="Launch",
            due_date=date(2026, 12, 1),
            source_meeting_id=world.private.id,
        )
    )
    session.commit()
    teams.leave(session, world.ada, datetime.now(UTC), team_id=team_w.id)
    session.commit()
    deletion.purge_user(session, session.get(User, world.pat.id), NoVexa(), force=True)
    session.expire_all()
    meeting = session.get(Meeting, world.private.id)
    assert meeting.owner_team_id is None and meeting.owner_user_id == world.ada.id
    home = teams.personal_team(session, world.ada.id)
    kept = session.get(Task, task.id)
    assert kept is not None and kept.team_id == home.id
    proposals = session.scalars(
        select(Milestone).where(Milestone.source_meeting_id == world.private.id)
    ).all()
    assert [m.team_id for m in proposals] == [home.id]
    assert [t["id"] for t in sign_in(app, ADA).get("/api/tasks").json()["tasks"]] == [task.id]


# ---- 5. one meeting is never both owned by a team and shared with another ----


def test_the_database_refuses_a_meeting_owned_by_one_team_and_shared_with_another(session, world):
    from sqlalchemy.exc import IntegrityError

    world.private.owner_team_id = world.x.id
    world.private.shared_team_id = world.y.id
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_share_waits_for_a_move_in_progress_and_then_refuses(app, session, world):
    """Two tabs: the move holds the meeting's row; the share must wait for it and then see the
    meeting is the team's (409), never write `shared_team_id` on top of it."""
    meeting_id = world.private.id
    engine = session.get_bind()
    s_move = Session(engine)
    mover = s_move.get(User, world.ada.id)
    meeting_teams.move(s_move, mover, s_move.get(Meeting, meeting_id), world.x.id)  # not committed
    outcome: dict = {}

    def share_meanwhile():
        with Session(engine) as s_share:
            try:
                meeting_teams.share(
                    s_share,
                    s_share.get(User, world.ada.id),
                    s_share.get(Meeting, meeting_id),
                    world.x.id,
                )
                s_share.commit()
                outcome["result"] = "shared"
            except services.Refused as exc:
                outcome["result"] = exc.code

    thread = threading.Thread(target=share_meanwhile)
    thread.start()
    time.sleep(1.0)
    assert thread.is_alive()  # waiting on the row lock
    s_move.commit()
    s_move.close()
    thread.join(10)
    assert outcome["result"] == "meeting_team_owned"
    session.expire_all()
    row = session.get(Meeting, meeting_id)
    assert row.owner_team_id == world.x.id and row.shared_team_id is None


# ---- 6. suggested-task decisions use the same write rule ----


def test_a_team_admin_may_decide_the_suggestions_of_a_moved_meeting_and_a_member_may_not(
    app, session, world, dee
):
    from app import tasks as task_rules

    world.private.owner_team_id = world.x.id
    session.commit()
    suggestion = add_task(
        session, world.x, world.private, title="Maybe a follow-up", suggested=True
    )
    assert task_rules.owns_meeting_of(session, dee, suggestion) is True  # an admin of X
    assert task_rules.owns_meeting_of(session, world.ada, suggestion) is True  # the recorder
    assert task_rules.owns_meeting_of(session, world.pat, suggestion) is False  # a plain member
    teams.transfer_owner(session, world.x.id, world.pat.id)
    session.commit()
    ada_gone = session.get(User, world.ada.id)
    teams.leave(session, ada_gone, datetime.now(UTC), team_id=world.x.id)
    session.commit()
    assert task_rules.owns_meeting_of(session, world.ada, suggestion) is False  # left the team


# ---- 7. notifications never name a moved meeting to someone who cannot read it ----


def test_a_notification_about_a_moved_meeting_is_hidden_from_a_recorder_who_left(
    app, session, world
):
    session.add(
        Notification(
            user_id=world.ada.id,
            kind="x",
            title="A notes email was not sent",
            body="The notes email for the private meeting",
            meeting_id=world.private.id,
            dedupe_key="a",
        )
    )
    session.add(Notification(user_id=world.ada.id, kind="y", title="General", dedupe_key="b"))
    session.commit()
    assert notifications_rules.unread_count(session, world.ada) == 2
    assert move(sign_in(app, ADA), world.private, world.x).status_code == 200
    ada_leaves_x(session, world)
    assert notifications_rules.unread_count(session, world.ada) == 1
    titles = [n.title for n in notifications_rules.list_notifications(session, world.ada, 50, None)]
    assert titles == ["General"]


# ---- an erased account's linked rows are gone ----


def test_an_erased_accounts_linked_rows_are_gone_and_only_the_hollow_row_stays(session, world):
    world.private.owner_team_id = world.x.id
    session.commit()
    ada_id = world.ada.id
    session.add(
        CalendarConnection(
            user_id=ada_id,
            provider="google",
            status="connected",
            granted_scopes="x",
            account_email="ada@example.test",
        )
    )
    session.add(Notification(user_id=ada_id, kind="k", title="t", dedupe_key="d"))
    session.add(
        Integration(
            team_id=world.x.id,
            kind="slack_webhook",
            config_enc=b"x",
            key_version=1,
            connected_by=ada_id,
        )
    )
    session.commit()
    deletion.purge_user(session, session.get(User, ada_id), NoVexa(), force=True)
    session.expire_all()
    assert session.scalars(select(CalendarConnection)).all() == []
    assert session.scalars(select(Notification).where(Notification.user_id == ada_id)).all() == []
    assert session.scalars(select(UserSettings).where(UserSettings.user_id == ada_id)).all() == []
    assert session.scalars(select(Integration)).one().connected_by is None
    assert session.get(Meeting, world.private.id) is not None
    hollow = session.get(User, ada_id)
    assert hollow.status == "deleted" and hollow.name is None and hollow.password_hash is None
