# ruff: noqa: F811
"""Sharing a draft with the team (plan T13, Review Focus 1): who may read, what a reader sees of the
sources, and that every write stays the creator's. Real HTTP against the app, three people: the
creator, a teammate in the same team and an outsider in another."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import teams
from app.models import DocumentShare, TeamMember
from app.recipes import blocks as blocks_mod
from app.recipes import service as svc
from app.recipes import visibility
from tests.test_api import error_of  # noqa: F401
from tests.test_api_recipes import (  # noqa: F401  (fixtures used by name)
    make,
    meeting_of,
    url,
    work,
)
from tests.test_recipe_generate import line, para
from tests.test_team_visibility import (  # noqa: F401
    TEAMMATE_EMAIL,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    fake_bot,
    fake_openai,
    make_user,
    outsider,
    owner,
    sign_in,
    stranger,
    teammate,
    template,
)

STARTER = SimpleNamespace(
    key="follow_up_email",
    name="Follow-up email",
    instruction="Write a friendly follow-up.",
    output_type="email",
    scope="one",
    uses_brief=True,
)
SECRET_TITLE = "Acme acquisition, board only"


@pytest.fixture(autouse=True)
def starters(monkeypatch):
    monkeypatch.setattr(svc, "starter_for", {"follow_up_email": STARTER}.get)


@pytest.fixture
def team(session, owner, teammate):
    """A real team (not the owner's personal space) with the owner and the teammate in it."""
    found = teams.ensure_team(session, owner)
    found.is_personal = False
    session.commit()
    return found


@pytest.fixture
def reader(app, team):
    return sign_in(app, TEAMMATE_EMAIL)


def sourced_draft(api, session, template, team, *, meeting_shared: bool):
    meeting = meeting_of(session, template, title=SECRET_TITLE)
    if meeting_shared:
        meeting.shared_team_id = team.id
        session.commit()
    doc_id = make(api, meeting).json()["id"]
    work(session, [para("The fee is 50,000 naira.", [line(meeting.id, 2)])])
    return meeting, doc_id


def share(api, doc_id):
    return api.call("POST", url(doc_id) + "/share")


# ---- who may read ----


def test_a_draft_is_private_until_shared_and_unsharing_takes_effect_at_once(
    api, reader, session, template, team
):
    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    error_of(reader.get(url(doc_id)), 404, "not_found")
    assert reader.get("/api/documents?shared=true").json()["documents"] == []

    assert share(api, doc_id).status_code == 200
    seen = reader.get(url(doc_id))
    assert seen.status_code == 200
    got = seen.json()
    assert (got["access"], got["can_edit"], got["status"]) == ("shared", False, "ready")
    assert got["blocks"][0]["text"] == "The fee is 50,000 naira."
    assert [d["id"] for d in reader.get("/api/documents?shared=true").json()["documents"]] == [
        doc_id
    ]

    assert api.call("DELETE", url(doc_id) + "/share").status_code == 204
    error_of(reader.get(url(doc_id)), 404, "not_found")
    assert reader.get("/api/documents?shared=true").json()["documents"] == []
    assert api.get(url(doc_id)).status_code == 200  # the creator still has it


def test_an_outsider_never_reads_a_shared_draft(api, session, template, team, app, outsider):
    from tests.test_web import OTHER_EMAIL

    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    assert share(api, doc_id).status_code == 200
    stranger_api = sign_in(app, OTHER_EMAIL)
    error_of(stranger_api.get(url(doc_id)), 404, "not_found")
    assert stranger_api.get("/api/documents?shared=true").json()["documents"] == []
    error_of(stranger_api.get(url(doc_id) + "/export?format=md"), 404, "not_found")


def test_sharing_is_idempotent_and_the_creator_sees_who_it_is_shared_with(
    api, session, template, team
):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    first, second = share(api, doc_id), share(api, doc_id)
    assert first.status_code == second.status_code == 200
    assert second.json()["shared_with"] == [{"id": team.id, "name": team.name}]
    assert len(session.scalars(select(DocumentShare)).all()) == 1
    assert api.call("DELETE", url(doc_id) + "/share").status_code == 204
    assert api.call("DELETE", url(doc_id) + "/share").status_code == 204  # again: nothing to do
    assert api.get(url(doc_id)).json()["shared_with"] == []


def test_only_a_finished_draft_in_a_real_team_can_be_shared(api, session, template, team, owner):
    meeting = meeting_of(session, template)
    writing = make(api, meeting).json()["id"]
    error_of(share(api, writing), 409, "not_ready")
    work(session, [para("Hi.")])
    assert share(api, writing).status_code == 200
    # a draft made in the owner's own personal workspace: nobody to share with
    personal = teams.personal_team(session, owner.id)
    api.client.headers["X-Team-Id"] = str(personal.id)
    try:
        own = make(api, meeting).json()["id"]
    finally:
        del api.client.headers["X-Team-Id"]
    work(session, [para("Hi.")])
    error_of(share(api, own), 409, "personal_team")
    error_of(api.call("POST", "/api/documents/999999/share"), 404, "not_found")


def test_an_unshared_draft_is_never_in_a_teammates_list(api, reader, session, template, team):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    assert reader.get("/api/documents").json()["documents"] == []
    assert reader.get("/api/documents?mine=true&shared=true").json()["documents"] == []
    share(api, doc_id)
    mine = api.get("/api/documents?shared=true").json()["documents"]
    assert mine == []  # the creator's own are not "shared with me"
    both = reader.get("/api/documents?mine=true&shared=true").json()["documents"]
    assert [d["id"] for d in both] == [doc_id]
    assert reader.get("/api/documents?mine=true").json()["documents"] == []


# ---- leaving the team ----


def test_leaving_the_team_ends_a_readers_access_at_once(api, reader, session, template, team):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    share(api, doc_id)
    assert reader.get(url(doc_id)).status_code == 200
    session.execute(
        TeamMember.__table__.delete().where(TeamMember.user_id == make_user_id(session))
    )
    session.commit()
    error_of(reader.get(url(doc_id)), 404, "not_found")


def test_the_creator_leaving_the_team_ends_the_teams_access(
    api, reader, session, template, team, owner
):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    share(api, doc_id)
    assert reader.get(url(doc_id)).status_code == 200
    session.execute(TeamMember.__table__.delete().where(TeamMember.user_id == owner.id))
    session.commit()
    error_of(reader.get(url(doc_id)), 404, "not_found")


def make_user_id(session) -> int:
    from app.models import User

    return session.scalar(select(User.id).where(User.email == TEAMMATE_EMAIL))


# ---- what a reader sees of the sources (Review Focus 1) ----


def test_a_reader_who_cannot_read_the_meeting_sees_no_title_no_line_and_no_quote(
    api, reader, session, template, team
):
    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=False)
    share(api, doc_id)
    owner_view = api.get(url(doc_id)).json()
    assert owner_view["blocks"][0]["sources"][0]["kind"] == "line"
    assert owner_view["blocks"][0]["sources"][0]["quote"]  # the creator has the quote

    response = reader.get(url(doc_id))
    got = response.json()
    assert got["blocks"][0]["sources"] == [
        {"kind": "hidden", "meeting_id": None, "line": None, "quote": None, "ref": None}
    ]
    assert got["meeting_ids"] == [] and got["sources_available"] is False
    assert (got["left_out_count"], got["options"], got["use_brief"]) == (0, {}, False)
    wire = response.text
    assert SECRET_TITLE not in wire and "Daniel" not in wire  # nothing of the meeting
    assert str(meeting.id) not in json.dumps(got["meeting_ids"])
    # the list row carries no meeting id either
    row = reader.get("/api/documents?shared=true").json()["documents"][0]
    assert row["meeting_ids"] == [] and row["access"] == "shared"


def test_a_reader_who_can_read_the_meeting_keeps_the_links(api, reader, session, template, team):
    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    share(api, doc_id)
    got = reader.get(url(doc_id)).json()
    source = got["blocks"][0]["sources"][0]
    assert (source["kind"], source["meeting_id"], source["line"]) == ("line", meeting.id, 2)
    assert got["meeting_ids"] == [meeting.id] and got["sources_available"] is True


def test_redact_hides_every_place_a_source_can_stand():
    src = blocks_mod.Source(kind="line", meeting_id=7, line=1, quote="Thanks for joining")
    mine = blocks_mod.Source(kind="meeting", meeting_id=8)
    blocks = [
        blocks_mod.Paragraph(text="p", sources=[src, mine]),
        blocks_mod.Bullets(items=[blocks_mod.Item(text="b", sources=[src])]),
        blocks_mod.Table(columns=["a"], rows=[[blocks_mod.Cell(text="c", sources=[src])]]),
        blocks_mod.Column(title="t", cards=[blocks_mod.Card(text="k", sources=[src])]),
    ]
    out = visibility.redact(blocks, {8})
    kinds = [
        [s.kind for s in out[0].sources],
        [s.kind for s in out[1].items[0].sources],
        [s.kind for s in out[2].rows[0][0].sources],
        [s.kind for s in out[3].cards[0].sources],
    ]
    assert kinds == [["hidden", "meeting"], ["hidden"], ["hidden"], ["hidden"]]
    assert blocks[0].sources[0].kind == "line"  # the original is untouched
    assert "Thanks for joining" not in json.dumps([b.model_dump() for b in out])


def test_the_model_and_edits_can_never_write_a_hidden_source(api, session, template, team):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    edit = api.call(
        "PATCH",
        url(doc_id),
        {"blocks": [{"type": "paragraph", "text": "x", "sources": [{"kind": "hidden"}]}]},
    )
    assert edit.status_code == 200
    assert edit.json()["blocks"][0]["sources"] == []


# ---- only the creator writes ----


def test_a_reader_cannot_edit_discard_share_unshare_or_edit_the_board(
    api, reader, session, template, team
):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    share(api, doc_id)
    body = {"blocks": [{"type": "paragraph", "text": "tampered", "sources": []}]}
    error_of(reader.call("PATCH", url(doc_id), body), 404, "not_found")
    error_of(reader.call("DELETE", url(doc_id)), 404, "not_found")
    error_of(reader.call("POST", url(doc_id) + "/share"), 404, "not_found")
    error_of(reader.call("DELETE", url(doc_id) + "/share"), 404, "not_found")
    op = {"op": "add_column", "title": "n", "revision": "2026-11-24T12:00:00Z"}
    error_of(reader.call("PATCH", url(doc_id) + "/board", op), 404, "not_found")
    still = api.get(url(doc_id)).json()
    assert still["blocks"][0]["text"] == "The fee is 50,000 naira." and still["edited"] is False
    assert api.get(url(doc_id)).json()["shared_with"]  # the share survived the reader's attempts


def test_a_reader_can_export_the_text_they_were_shared(api, reader, session, template, team):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=False)
    share(api, doc_id)
    response = reader.get(url(doc_id) + "/export?format=md")
    assert response.status_code == 200 and "50,000 naira" in response.text
    assert SECRET_TITLE not in response.text


def test_a_reader_who_cannot_read_every_meeting_gets_checks_from_what_they_can_read(
    api, reader, session, template, team
):
    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=False)
    share(api, doc_id)
    # 50,000 is in the meeting the reader cannot read: from their redacted view it is not found
    # anywhere, so it is flagged "check this" for them (and not for the creator).
    assert api.get(url(doc_id)).json()["checks"]["flags"] == []
    flags = reader.get(url(doc_id)).json()["checks"]["flags"]
    assert [f["text"] for f in flags] == ["50,000 naira"]


def test_stale_is_false_for_a_reader_with_a_hidden_source_and_true_when_they_read_all(
    api, reader, session, template, team
):
    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=False)
    share(api, doc_id)
    meeting.notes = "## Summary\nThe plan changed: 80,000 naira."
    session.commit()
    assert api.get(url(doc_id)).json()["stale"] is True
    assert reader.get(url(doc_id)).json()["stale"] is False  # a hidden meeting: say nothing
    meeting.shared_team_id = team.id  # now the reader can read the meeting
    session.commit()
    assert reader.get(url(doc_id)).json()["stale"] is True


def test_a_reader_who_cannot_read_every_meeting_sees_a_generic_title(
    api, reader, session, template, team
):
    from datetime import datetime

    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=False)
    share(api, doc_id)
    mine = api.get(url(doc_id)).json()
    assert mine["title"] == "Thanks for today"  # the model's own title, for the creator
    when = datetime.fromisoformat(mine["created_at"].replace("Z", "+00:00"))
    expected = f"Follow-up email, {when.day} {when.strftime('%b')}"
    seen = reader.get(url(doc_id)).json()
    assert seen["title"] == expected
    row = reader.get("/api/documents?shared=true").json()["documents"][0]
    assert row["title"] == expected
    meeting.shared_team_id = team.id
    session.commit()
    assert reader.get(url(doc_id)).json()["title"] == "Thanks for today"  # reads every source


# ---- a share must not come back (leaving and rejoining the team) ----


def test_a_share_does_not_come_back_when_its_creator_leaves_and_rejoins(
    api, session, template, team, teammate, app
):
    from datetime import UTC, datetime

    maker = sign_in(app, TEAMMATE_EMAIL)  # the teammate makes and shares; the owner reads
    meeting = meeting_of(session, template, title=SECRET_TITLE)
    meeting.shared_team_id = team.id
    session.commit()
    doc_id = make(maker, meeting).json()["id"]
    work(session)
    assert share(maker, doc_id).status_code == 200
    assert api.get(url(doc_id)).status_code == 200

    teams.leave(session, teammate, datetime.now(UTC), team_id=team.id)
    session.commit()
    assert session.scalars(select(DocumentShare)).all() == []  # the share went with the membership
    session.add(TeamMember(team_id=team.id, user_id=teammate.id, role="member"))
    session.commit()  # they join again
    error_of(api.get(url(doc_id)), 404, "not_found")
    assert api.get("/api/documents?shared=true").json()["documents"] == []
    assert maker.get(url(doc_id)).json()["shared_with"] == []


def test_the_creators_shared_with_list_ignores_a_share_nobody_can_reach(
    api, session, template, team
):
    _, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    share(api, doc_id)
    assert api.get(url(doc_id)).json()["shared_with"][0]["id"] == team.id
    # a row that survived some other way (the sharer is no longer in the team)
    session.execute(TeamMember.__table__.delete().where(TeamMember.team_id == team.id))
    session.commit()
    assert api.get(url(doc_id)).json()["shared_with"] == []


# ---- the creator's own view is redacted too ----


def test_a_creator_who_lost_access_to_the_meeting_sees_hidden_sources(
    api, session, template, team, teammate, app
):
    maker = sign_in(app, TEAMMATE_EMAIL)
    meeting = meeting_of(session, template, title=SECRET_TITLE)
    meeting.shared_team_id = team.id
    session.commit()
    doc_id = make(maker, meeting).json()["id"]
    work(session, [para("The fee is 50,000 naira.", [line(meeting.id, 2)])])
    assert maker.get(url(doc_id)).json()["blocks"][0]["sources"][0]["kind"] == "line"
    meeting.shared_team_id = None  # the owner takes the meeting back from the team
    session.commit()
    got = maker.get(url(doc_id))
    assert got.json()["blocks"][0]["sources"] == [
        {"kind": "hidden", "meeting_id": None, "line": None, "quote": None, "ref": None}
    ]
    assert got.json()["sources_available"] is False and got.json()["access"] == "owner"
    assert SECRET_TITLE not in got.text and "Daniel" not in got.text.split('"blocks"')[0]


# ---- a draft is shared with the team it was made in, and only that one ----


def test_a_share_goes_to_the_drafts_own_team_whatever_team_is_active(
    api, session, template, team, owner
):
    from app.models import Team

    other = Team(owner_user_id=owner.id, name="Second team", is_personal=False)
    session.add(other)
    session.flush()
    session.add(TeamMember(team_id=other.id, user_id=owner.id, role="owner"))
    session.commit()
    meeting = meeting_of(session, template)
    api.client.headers["X-Team-Id"] = str(team.id)  # made in the first team
    try:
        made = api.call(
            "POST",
            "/api/documents",
            {"recipe_key": "follow_up_email", "meeting_ids": [meeting.id]},
        )
    finally:
        del api.client.headers["X-Team-Id"]
    doc_id = made.json()["id"]
    work(session)
    api.client.headers["X-Team-Id"] = str(other.id)  # the person has switched to the other team
    try:
        got = api.call("POST", url(doc_id) + "/share")
    finally:
        del api.client.headers["X-Team-Id"]
    assert got.status_code == 200
    assert got.json()["shared_with"] == [{"id": team.id, "name": team.name}]
    assert got.json()["team"] == {"id": team.id, "name": team.name}
    assert [s.team_id for s in session.scalars(select(DocumentShare))] == [team.id]


def test_a_draft_made_in_the_personal_space_cannot_be_shared(api, session, template, team):
    team.is_personal = True  # the owner's own space
    session.commit()
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)
    got = api.get(url(doc_id)).json()
    message = (
        "This draft was made in your own space. Make it in a team workspace to share it with "
        "that team."
    )
    assert got["share_blocked"] == message
    error = error_of(share(api, doc_id), 409, "personal_team")
    assert error["message"] == message
    assert session.scalars(select(DocumentShare)).all() == []


def test_a_draft_whose_creator_left_its_team_cannot_be_shared(api, session, template, team, owner):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)
    assert api.get(url(doc_id)).json()["share_blocked"] is None
    session.execute(TeamMember.__table__.delete().where(TeamMember.user_id == owner.id))
    session.commit()
    assert "no longer in the team" in api.get(url(doc_id)).json()["share_blocked"]
    error_of(share(api, doc_id), 409, "team_left")


def test_can_read_document_matches_the_routes(api, reader, session, template, team, owner):
    meeting, doc_id = sourced_draft(api, session, template, team, meeting_shared=True)
    from app.models import Document, User

    doc = session.get(Document, doc_id)
    other = session.scalar(select(User).where(User.email == TEAMMATE_EMAIL))
    assert visibility.can_read_document(session, owner, doc)
    assert not visibility.can_read_document(session, other, doc)
    share(api, doc_id)
    assert visibility.can_read_document(session, other, doc)
    assert visibility.readable_sources(session, other, doc) == {meeting.id}
