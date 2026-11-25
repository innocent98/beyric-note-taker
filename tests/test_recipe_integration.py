# ruff: noqa: F811
"""The seams between the recipes slices (plan T17, spec 5.4a): what deleting a meeting does to its
drafts, when a draft is labelled older than the meeting, and that regenerating is always a new
draft. Real HTTP against the app, the real worker step with a fake model."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import deletion, teams
from app.models import Document, DocumentMeeting, TeamBrief
from app.recipes import cleanup
from app.recipes import service as svc
from tests.test_api import error_of  # noqa: F401
from tests.test_api_recipes import make, meeting_of, url, work
from tests.test_recipe_generate import line, para
from tests.test_team_visibility import (  # noqa: F401  (fixtures used by name)
    TEAMMATE_EMAIL,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    fake_bot,
    fake_openai,
    make_user,
    owner,
    sign_in,
    stranger,
    teammate,
    template,
)

ONE = SimpleNamespace(
    key="follow_up_email",
    name="Follow-up email",
    instruction="Write a friendly follow-up.",
    output_type="email",
    scope="one",
    uses_brief=True,
)
MANY = SimpleNamespace(
    key="status_report",
    name="Status report",
    instruction="Progress over the meetings.",
    output_type="document",
    scope="many",
    uses_brief=True,
)


@pytest.fixture(autouse=True)
def starters(monkeypatch):
    monkeypatch.setattr(svc, "starter_for", {"follow_up_email": ONE, "status_report": MANY}.get)


@pytest.fixture
def team(session, owner, teammate):
    found = teams.ensure_team(session, owner)
    found.is_personal = False
    session.commit()
    return found


def make_many(api, *meetings, **body):
    return api.post(
        "/api/documents",
        {"recipe_key": "status_report", "meeting_ids": [m.id for m in meetings], **body},
    )


def documents(session):
    session.expire_all()
    return {d.id for d in session.scalars(select(Document))}


# ---- deleting a meeting (spec 5.4a) ----


def test_deleting_a_meeting_deletes_its_single_meeting_drafts_and_keeps_cross_meeting_ones(
    api, session, template
):
    first = meeting_of(session, template, title="First", native_id="aaa-aaaa-aaa")
    second = meeting_of(session, template, title="Second", native_id="bbb-bbbb-bbb")
    single = make(api, first).json()["id"]
    work(session, [para("About the first.", [line(first.id, 1)])])
    other_single = make(api, second).json()["id"]
    work(session)
    cross = make_many(api, first, second).json()["id"]
    work(session, [para("Both of them.", [line(first.id, 1)])])
    assert documents(session) == {single, other_single, cross}

    assert api.delete(f"/api/meetings/{first.id}").status_code == 204
    assert documents(session) == {other_single, cross}  # the one made from it alone is gone
    error_of(api.get(url(single)), 404, "not_found")
    kept = api.get(url(cross)).json()
    assert kept["status"] == "ready" and kept["meeting_ids"] == [second.id]
    assert kept["sources_available"] is True  # the second meeting can still be opened
    assert kept["blocks"][0]["text"] == "Both of them."  # the draft's own words stay

    assert api.delete(f"/api/meetings/{second.id}").status_code == 204
    assert documents(session) == set()  # nothing is left for the last draft to stand on


def test_a_cross_meeting_draft_whose_other_meeting_goes_reads_not_available(api, session, template):
    first = meeting_of(session, template, native_id="aaa-aaaa-aaa")
    second = meeting_of(session, template, native_id="bbb-bbbb-bbb")
    cross = make_many(api, first, second).json()["id"]
    work(
        session,
        [
            para(
                "Both.",
                [
                    {
                        "kind": "meeting",
                        "meeting_id": second.id,
                        "line": None,
                        "quote": None,
                        "ref": None,
                    }
                ],
            )
        ],
    )
    api.delete(f"/api/meetings/{second.id}")
    got = api.get(url(cross)).json()
    assert got["meeting_ids"] == [first.id]
    # the deleted meeting's source is hidden for the creator too: no id, line or quote, no dead link
    assert got["blocks"][0]["sources"] == [
        {"kind": "hidden", "meeting_id": None, "line": None, "quote": None, "ref": None}
    ]
    assert got["sources_available"] is True  # the first meeting can still be opened
    assert api.get(f"/api/meetings/{second.id}").status_code == 404


def test_a_shared_draft_goes_with_its_meeting_for_the_teammate_too(
    api, session, template, team, app
):
    meeting = meeting_of(session, template)
    meeting.shared_team_id = team.id
    session.commit()
    doc_id = make(api, meeting).json()["id"]
    work(session)
    assert api.call("POST", url(doc_id) + "/share").status_code == 200
    reader = sign_in(app, TEAMMATE_EMAIL)
    assert reader.get(url(doc_id)).status_code == 200
    api.delete(f"/api/meetings/{meeting.id}")
    error_of(reader.get(url(doc_id)), 404, "not_found")
    assert reader.get("/api/documents?shared=true").json()["documents"] == []


def test_the_ledger_keeps_the_cost_of_a_draft_whose_meeting_was_deleted(api, session, template):
    from app.models import AiRun

    meeting = meeting_of(session, template)
    make(api, meeting)
    work(session)
    before = len(session.scalars(select(AiRun).where(AiRun.kind == "recipe")).all())
    api.delete(f"/api/meetings/{meeting.id}")
    session.expire_all()
    assert len(session.scalars(select(AiRun).where(AiRun.kind == "recipe")).all()) == before == 1


def test_deleting_an_account_removes_drafts_made_only_from_its_meetings(
    api, session, template, owner, team
):
    from tests.test_web import OTHER_EMAIL

    mine = meeting_of(session, template, native_id="aaa-aaaa-aaa")
    theirs = ready_for(session, template, make_user(session, OTHER_EMAIL), "bbb-bbbb-bbb")
    single = make(api, mine).json()["id"]
    work(session)
    cross = make_many(api, mine).json()["id"]  # one meeting, a cross-meeting recipe
    work(session)
    other = make_doc_for(session, owner_id=owner.id, meetings=[mine.id, theirs.id])
    cleanup.delete_drafts_of(session, [mine.id])
    session.commit()
    assert documents(session) == {other}  # a draft with a meeting left stays, the rest go
    assert single and cross


def ready_for(session, template, user, native_id):
    return meeting_of(session, template, native_id=native_id, owner_user_id=user.id)


def make_doc_for(session, *, owner_id, meetings):
    doc = Document(
        owner_user_id=owner_id,
        title="Report",
        output_type="document",
        status="ready",
        blocks=[{"type": "paragraph", "text": "x", "sources": []}],
    )
    session.add(doc)
    session.flush()
    session.add_all(DocumentMeeting(document_id=doc.id, meeting_id=m) for m in meetings)
    session.commit()
    return doc.id


def test_purging_an_account_runs_the_same_cleanup(api, session, template, owner, fake_bot):
    meeting = meeting_of(session, template)
    keeper_id = make_doc_for(session, owner_id=owner.id, meetings=[meeting.id])
    other = make_user(session, "second@example.test")
    their = meeting_of(session, template, native_id="zzz-zzzz-zzz", owner_user_id=other.id)
    cross = make_doc_for(session, owner_id=other.id, meetings=[meeting.id, their.id])
    deletion.purge_user(session, owner, fake_bot, force=True)
    session.expire_all()
    assert session.get(Document, keeper_id) is None  # their own draft goes with the account
    assert session.get(Document, cross) is not None  # someone else's cross-meeting draft stays
    assert [
        m
        for (m,) in session.execute(
            select(DocumentMeeting.meeting_id).where(DocumentMeeting.document_id == cross)
        )
    ] == [their.id]


# ---- stale: older than the meeting text ----


def test_a_draft_is_stale_when_the_notes_are_rewritten_and_regenerating_makes_a_new_one(
    api, session, template
):
    meeting = meeting_of(session, template)
    old_id = make(api, meeting).json()["id"]
    work(session)
    assert api.get(url(old_id)).json()["stale"] is False
    meeting.notes = "## Summary\nThe plan changed: onboarding is now 80,000 naira."
    session.commit()
    assert api.get(url(old_id)).json()["stale"] is True

    new_id = make(api, meeting).json()["id"]  # Regenerate is a plain new request
    assert new_id != old_id
    work(session)
    assert api.get(url(new_id)).json()["stale"] is False
    assert api.get(url(old_id)).json()["stale"] is True  # the old one is untouched
    assert documents(session) == {old_id, new_id}


def test_a_draft_is_stale_when_the_transcript_is_edited(api, session, template):
    """The transcript editor writes the shown transcript column (`speaker_transcript`, else
    `transcript`), which is what the draft was hashed from; speaker corrections change it too."""
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)
    meeting.speaker_transcript = meeting.speaker_transcript.replace("50,000", "70,000")
    session.commit()
    assert api.get(url(doc_id)).json()["stale"] is True


def test_a_draft_is_stale_when_the_brief_changes(api, session, template, owner, team):
    meeting = meeting_of(session, template, workspace_team_id=team.id)
    session.add(TeamBrief(team_id=team.id, text="Onboarding costs 50,000 naira."))
    session.commit()
    doc_id = make(api, meeting).json()["id"]
    work(session)
    assert api.get(url(doc_id)).json()["stale"] is False
    session.scalars(select(TeamBrief)).one().text = "Onboarding costs 90,000 naira."
    session.commit()
    assert api.get(url(doc_id)).json()["stale"] is True


def test_an_edit_does_not_make_a_draft_stale_and_a_regenerate_never_overwrites(
    api, session, template
):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session, [para("Original words.")])
    edit = api.call(
        "PATCH",
        url(doc_id),
        {"blocks": [{"type": "paragraph", "text": "My words.", "sources": []}]},
    )
    assert edit.json()["stale"] is False and edit.json()["edited"] is True
    again = make(api, meeting).json()["id"]
    work(session, [para("Second try.")])
    assert api.get(url(doc_id)).json()["blocks"][0]["text"] == "My words."
    assert api.get(url(again)).json()["blocks"][0]["text"] == "Second try."


def test_the_daily_cap_counts_each_regeneration(api, session, template, monkeypatch):
    monkeypatch.setenv("RECIPE_DAILY_LIMIT", "2")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        meeting = meeting_of(session, template)
        make(api, meeting)
        work(session)
        make(api, meeting)
        work(session)
        error_of(make(api, meeting), 429, "recipe_limit")
    finally:
        get_settings.cache_clear()


# ---- discarding a draft before the worker picked it up (spec 5.6) ----


def _limit_of(monkeypatch, n):
    from tests.test_web import make_settings

    settings = make_settings(recipe_daily_limit=n)
    monkeypatch.setattr(svc, "get_settings", lambda: settings)


def _recipe_rows(session):
    from app.models import AiRun

    session.expire_all()
    return session.scalars(select(AiRun).where(AiRun.kind == "recipe")).all()


def test_discarding_a_draft_nobody_started_gives_its_place_in_the_day_back(
    api, session, template, monkeypatch
):
    _limit_of(monkeypatch, 1)
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    assert len(_recipe_rows(session)) == 1  # asked for: counted from now on
    assert api.delete(url(doc_id)).status_code == 204  # discarded before the worker claimed it
    assert _recipe_rows(session) == []  # no model was called: nothing to count
    assert make(api, meeting).status_code == 202  # the one place is free again


def test_discarding_a_draft_the_worker_already_started_still_counts(
    api, session, template, monkeypatch
):
    _limit_of(monkeypatch, 1)
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)  # claimed, called the model, settled
    assert api.delete(url(doc_id)).status_code == 204
    assert [r.status for r in _recipe_rows(session)] == ["ok"]
    error_of(make(api, meeting), 429, "recipe_limit")


def test_a_draft_claimed_but_not_finished_keeps_its_place_when_discarded(
    api, session, template, monkeypatch
):
    from datetime import UTC, datetime

    from app.models import Document

    _limit_of(monkeypatch, 1)
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    session.get(Document, doc_id).started_at = datetime.now(UTC)  # the worker has it
    session.commit()
    assert api.delete(url(doc_id)).status_code == 204
    assert len(_recipe_rows(session)) == 1  # the call may be running: it counts
    error_of(make(api, meeting), 429, "recipe_limit")
