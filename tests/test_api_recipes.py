# ruff: noqa: F811
"""The draft API (`app/api_recipes.py`); fixtures from test_api.py, the model from
`tests/test_recipe_generate.py`."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AiRun, Document, Meeting
from app.recipes import service as svc
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_bot,
    fake_openai,
    make_user,
    owner,
    ready,
    stranger,
    template,
)
from tests.test_recipe_generate import FakeModel, para, reply

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
TRANSCRIPT = (
    "[Ada]: Thanks for joining Daniel.\n"
    "[Daniel]: We want onboarding for 50,000 naira, starting 2026-12-01.\n"
    "[Ada]: Agreed. I will send the plan by Friday."
)
NOTES = "## Summary\nOnboarding for 50,000 naira."
STARTER = SimpleNamespace(
    key="follow_up_email",
    name="Follow-up email",
    instruction="Write a friendly follow-up.",
    output_type="email",
    scope="one",
    uses_brief=True,
)


@pytest.fixture(autouse=True)
def starters(monkeypatch):
    monkeypatch.setattr(svc, "starter_for", {"follow_up_email": STARTER}.get)


def meeting_of(session, template, **fields):
    values = {"notes": NOTES, "speaker_transcript": TRANSCRIPT, "title": "Onboarding call"}
    return ready(session, template, **{**values, **fields})


def make(api, meeting, **body):
    payload = {"recipe_key": "follow_up_email", "meeting_ids": [meeting.id], **body}
    return api.post("/api/documents", payload)


def work(session, blocks=None, model=None):
    model = model or FakeModel(reply(blocks or [para("Hi Daniel. The fee is 50,000 naira.")]))
    return svc.process_next(session, datetime.now(UTC), client=model)


def url(doc_id):
    return f"/api/documents/{doc_id}"


def test_post_is_202_writing_then_get_is_200_with_the_blocks(api, session, template):
    meeting = meeting_of(session, template)
    response = make(api, meeting)
    assert response.status_code == 202
    body = response.json()
    assert (body["status"], body["blocks"], body["title"], body["output_type"]) == (
        "writing",
        [],
        "Follow-up email",
        "email",
    )
    assert body["meeting_ids"] == [meeting.id] and body["recipe_key"] == "follow_up_email"
    assert body["checks"] == {"blanks": [], "flags": []}
    assert (body["stale"], body["sources_available"], body["left_out_count"]) == (False, True, 0)
    assert (body["failure_code"], body["failure_message"], body["edited"]) == (None, None, False)
    # polling while it is written
    assert api.get(url(body["id"])).json()["status"] == "writing"
    work(session)
    done = api.get(url(body["id"]))
    assert done.status_code == 200
    got = done.json()
    assert got["status"] == "ready" and got["title"] == "Thanks for today"
    assert got["blocks"][0]["type"] == "paragraph"
    assert got["blocks"][0]["text"] == "Hi Daniel. The fee is 50,000 naira."
    assert got["checks"] == {"blanks": [], "flags": []}


def test_a_failed_draft_says_why_in_plain_words(api, session, template):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    svc.process_next(session, datetime.now(UTC), client=FakeModel(("not json", "stop")))
    got = api.get(url(doc_id)).json()
    assert (got["status"], got["failure_code"], got["blocks"]) == ("failed", "model_reply", [])
    assert got["failure_message"] == svc.FAILURE_MESSAGES["model_reply"]
    error_of(api.call("PATCH", url(doc_id), {"blocks": [para("x")]}), 409, "not_ready")


def test_the_recipe_must_be_named_once_and_the_meetings_chosen(api, session, template):
    meeting = meeting_of(session, template)
    error_of(api.post("/api/documents", {"meeting_ids": [meeting.id]}), 422, "recipe_invalid")
    both = {"recipe_key": "follow_up_email", "recipe_id": 1, "meeting_ids": [meeting.id]}
    error_of(api.post("/api/documents", both), 422, "recipe_invalid")
    error_of(
        api.post("/api/documents", {"recipe_key": "follow_up_email", "meeting_ids": []}),
        422,
        "validation_error",
    )
    error_of(make(api, meeting, recipe_key="nope"), 404, "not_found")
    assert session.scalar(select(Document.id)) is None


def test_an_unreadable_meeting_is_404_and_an_unready_one_409(api, session, template, stranger):
    from app.models import Template
    from tests.test_api import add_meeting

    theirs_template = Template(owner_user_id=stranger.id, name="G", instructions="x")
    session.add(theirs_template)
    session.commit()
    theirs = ready(session, theirs_template)
    error_of(make(api, theirs), 404, "not_found")
    mine = add_meeting(session, template)  # still bot_sent
    error_of(make(api, mine), 409, "not_ready")


def test_csrf_is_required_to_make_edit_or_discard(api, session, template):
    meeting = meeting_of(session, template)
    for token in (False, "wrong"):
        error_of(
            api.call("POST", "/api/documents", {"recipe_key": "x", "meeting_ids": [1]}, csrf=token),
            403,
            "csrf_rejected",
        )
    doc_id = make(api, meeting).json()["id"]
    work(session)
    error_of(api.call("PATCH", url(doc_id), {"blocks": []}, csrf=False), 403, "csrf_rejected")
    error_of(api.call("DELETE", url(doc_id), csrf=False), 403, "csrf_rejected")


def test_another_persons_draft_is_a_404_everywhere(api, session, template, stranger, app):
    from tests.test_team_visibility import OTHER_EMAIL, sign_in

    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)
    other = sign_in(app, OTHER_EMAIL)
    error_of(other.get(url(doc_id)), 404, "not_found")
    error_of(other.call("PATCH", url(doc_id), {"blocks": [para("x")]}), 404, "not_found")
    error_of(other.delete(url(doc_id)), 404, "not_found")
    assert other.get("/api/documents").json() == {"documents": []}
    assert api.get(url(doc_id)).status_code == 200


def test_a_draft_that_does_not_exist_is_404_for_any_id(api):
    for doc_id in (1, 999999, 99999999999):
        error_of(api.get(url(doc_id)), 404, "not_found")


def test_patch_while_writing_is_409(api, session, template):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    error_of(api.call("PATCH", url(doc_id), {"blocks": [para("x")]}), 409, "writing")


def test_patch_saves_the_blocks_marks_edited_and_reruns_the_audit(api, session, template):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(
        session,
        [para("Fee 50,000 naira.", [{"kind": "line", "meeting_id": meeting.id, "line": 2}])],
    )
    first = api.get(url(doc_id)).json()
    assert first["edited"] is False and first["checks"]["flags"] == []
    keep = first["blocks"][0]["sources"]
    edited = [
        {"type": "heading", "level": 1, "text": "Proposal <b>draft</b>"},
        {
            "type": "paragraph",
            "text": "Fee is ₦450,000, start [need: start date].",
            "sources": keep + [{"kind": "line", "meeting_id": meeting.id, "line": 3}],
        },
        {
            "type": "table",
            "columns": ["Item", "Cost"],
            "rows": [[{"text": "Setup", "sources": []}, {"text": "50,000", "sources": []}]],
        },
    ]
    response = api.call("PATCH", url(doc_id), {"blocks": edited, "title": "  My <i>proposal</i> "})
    assert response.status_code == 200
    body = response.json()
    assert body["edited"] is True and body["title"] == "My proposal"
    assert [b["type"] for b in body["blocks"]] == ["heading", "paragraph", "table"]
    assert body["blocks"][0]["text"] == "Proposal draft"
    # the source that was not there before is gone; the one it had is kept
    assert [s["line"] for s in body["blocks"][1]["sources"]] == [2]
    assert body["checks"]["blanks"] == [{"block_index": 1, "what": "start date"}]
    assert body["checks"]["flags"] == [{"block_index": 1, "text": "₦450,000", "kind": "figure"}]
    # the checks follow the CURRENT blocks: fix the figure and the flag goes
    edited[1]["text"] = "Fee is ₦50,000, start [need: start date]."
    again = api.call("PATCH", url(doc_id), {"blocks": edited}).json()
    assert again["checks"]["flags"] == [] and again["title"] == "My proposal"
    assert api.get(url(doc_id)).json()["blocks"][1]["text"].startswith("Fee is ₦50,000")


@pytest.mark.parametrize(
    "blocks",
    [
        [],
        [{"type": "chart"}],
        [{"type": "table", "columns": ["A", "B"], "rows": [[{"text": "x", "sources": []}]]}],
        "nope",
    ],
)
def test_patch_with_blocks_that_do_not_fit_is_422(api, session, template, blocks):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)
    response = api.call("PATCH", url(doc_id), {"blocks": blocks})
    assert response.status_code == 422
    assert api.get(url(doc_id)).json()["edited"] is False


def test_patch_with_a_malformed_body_is_a_422(api, session, template):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)
    error_of(
        api.call("PATCH", url(doc_id), {"blocks": [para("x")], "title": "t" * 201}),
        422,
        "validation_error",
    )
    error_of(api.call("PATCH", url(doc_id), {}), 422, "validation_error")


def test_checks_notice_that_the_notes_moved_on_and_that_the_meeting_is_gone(api, session, template):
    meeting = meeting_of(session, template)
    meeting_id = meeting.id
    doc_id = make(api, meeting).json()["id"]
    work(session)
    assert api.get(url(doc_id)).json()["stale"] is False
    meeting.notes = NOTES + "\n- Another point."
    session.commit()
    stale = api.get(url(doc_id)).json()
    assert stale["stale"] is True and stale["sources_available"] is True
    session.delete(session.get(Meeting, meeting_id))
    session.commit()
    gone = api.get(url(doc_id)).json()
    assert gone["sources_available"] is False and gone["meeting_ids"] == []
    assert gone["blocks"][0]["type"] == "paragraph"  # the draft stays; only its links go


def test_list_is_the_callers_own_newest_first_and_filters_by_meeting(api, session, template):
    first = meeting_of(session, template, title="First")
    second = meeting_of(session, template, title="Second")
    ids = [
        make(api, first, options={"n": 1}).json()["id"],
        make(api, second).json()["id"],
        make(api, first, options={"n": 2}).json()["id"],
    ]
    everything = api.get("/api/documents").json()["documents"]
    assert [d["id"] for d in everything] == ids[::-1]
    assert set(everything[0]) == {
        "id", "title", "output_type", "recipe_key", "recipe_id", "status", "edited",
        "meeting_ids", "created_at", "updated_at", "access", "owner_name", "shared_with",
    }  # fmt: skip
    only_first = api.get(f"/api/documents?meeting_id={first.id}").json()["documents"]
    assert [d["id"] for d in only_first] == [ids[2], ids[0]]
    page = api.get(f"/api/documents?limit=1&before_id={ids[2]}").json()["documents"]
    assert [d["id"] for d in page] == [ids[1]]
    assert api.get("/api/documents?limit=0").status_code == 422


def test_delete_is_204_and_the_draft_is_gone_but_the_call_still_counts(api, session, template):
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session)
    assert api.delete(url(doc_id)).status_code == 204
    error_of(api.get(url(doc_id)), 404, "not_found")
    error_of(api.delete(url(doc_id)), 404, "not_found")
    assert [r.status for r in session.scalars(select(AiRun).where(AiRun.kind == "recipe"))] == [
        "ok"
    ]


def test_the_daily_limit_is_429_with_its_plain_sentence(api, session, template, monkeypatch):
    from tests.test_web import make_settings

    settings = make_settings(recipe_daily_limit=1)
    monkeypatch.setattr(svc, "get_settings", lambda: settings)
    meeting = meeting_of(session, template)
    assert make(api, meeting, options={"n": 1}).status_code == 202
    error = error_of(make(api, meeting, options={"n": 2}), 429, "recipe_limit")
    assert error["message"] == "You have made 1 documents today. Try again tomorrow."


def test_asking_twice_in_a_row_returns_the_same_draft(api, session, template):
    meeting = meeting_of(session, template)
    first = make(api, meeting).json()["id"]
    second = make(api, meeting).json()["id"]
    assert first == second and len(list(session.scalars(select(Document)))) == 1


def test_the_team_header_names_the_team_and_a_foreign_one_is_404(api, session, template, owner):
    from app import teams

    team = teams.ensure_team(session, owner)
    session.commit()
    meeting = meeting_of(session, template)
    ok = api.call(
        "POST", "/api/documents", {"recipe_key": "follow_up_email", "meeting_ids": [meeting.id]}
    )
    assert ok.status_code == 202
    assert session.get(Document, ok.json()["id"]).team_id == team.id
    api.client.headers["X-Team-Id"] = "999999"
    try:
        error_of(make(api, meeting, options={"n": 2}), 404, "not_found")
    finally:
        del api.client.headers["X-Team-Id"]


def test_the_draft_carries_the_options_and_brief_choice_so_regenerate_can_repeat_them(
    api, session, template
):
    meeting = meeting_of(session, template)
    made = make(api, meeting, options={"tone": "formal", "recipient": "Daniel"}, use_brief=False)
    assert made.status_code == 202
    body = made.json()
    assert body["options"] == {"tone": "formal", "recipient": "Daniel"}
    assert body["use_brief"] is False
    assert api.get(url(body["id"])).json()["options"] == {"tone": "formal", "recipient": "Daniel"}
