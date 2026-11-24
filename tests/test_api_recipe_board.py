# ruff: noqa: F811
"""`PATCH /api/documents/{id}/board` (`app/api_recipe_board.py`): one operation per call, strict
bodies, only the creator edits, refused while writing, a stale screen is a 409. A stand-in `board`
starter and a fake model: the real starter's wording is slice S3's."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Document, DocumentShare
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
from tests.test_recipe_board_generate import card, column
from tests.test_recipe_generate import FakeModel, line, para, reply
from tests.test_team_visibility import OTHER_EMAIL, sign_in, teammate  # noqa: F401

TRANSCRIPT = (
    "[Ada]: Thanks for joining Daniel.\n"
    "[Daniel]: We want onboarding for 50,000 naira, starting 2026-12-01.\n"
    "[Ada]: Agreed. I will send the plan by Friday."
)
BOARD = SimpleNamespace(
    key="board",
    name="Board",
    instruction="Group the meeting by theme.",
    output_type="board",
    scope="one",
    uses_brief=True,
)
EMAIL = SimpleNamespace(
    key="follow_up_email",
    name="Follow-up email",
    instruction="Write a follow-up.",
    output_type="email",
    scope="one",
    uses_brief=True,
)


@pytest.fixture(autouse=True)
def starters(monkeypatch):
    monkeypatch.setattr(svc, "starter_for", {"board": BOARD, "follow_up_email": EMAIL}.get)


def meeting_of(session, template):
    return ready(
        session,
        template,
        notes="## Summary\nOnboarding for 50,000 naira.",
        speaker_transcript=TRANSCRIPT,
        title="Onboarding call",
    )


def board_blocks(meeting_id):
    return [
        column(
            "Pricing",
            [
                card("Onboarding is 50,000 naira", "decision", None, [line(meeting_id, 2)]),
                card("Who signs?", "question"),
            ],
        ),
        column("Next steps", [card("Send the plan", "action", "Ada", [line(meeting_id, 3)])]),
        column("Later", [card("Revisit pricing in spring", "note")]),
    ]


def make_board(api, session, template, blocks=None, recipe="board"):
    meeting = meeting_of(session, template)
    made = api.post("/api/documents", {"recipe_key": recipe, "meeting_ids": [meeting.id]})
    assert made.status_code == 202
    model = FakeModel(reply(blocks or board_blocks(meeting.id), "Onboarding board"))
    svc.process_next(session, datetime.now(UTC), client=model)
    return meeting, made.json()["id"]


def url(doc_id):
    return f"/api/documents/{doc_id}/board"


def get(api, doc_id):
    return api.get(f"/api/documents/{doc_id}").json()


def edit(api, doc_id, **op):
    """One operation, sent with the revision the screen last saw (the draft's `updated_at`)."""
    body = {"revision": get(api, doc_id)["updated_at"], **op}
    return api.call("PATCH", url(doc_id), body)


def column_cards(body):
    return [[c["text"] for c in col["cards"]] for col in body["blocks"]]


def test_the_model_makes_a_board_and_the_page_gets_columns_of_cards(api, session, template):
    meeting, doc_id = make_board(api, session, template)
    body = get(api, doc_id)
    assert (body["status"], body["output_type"], body["title"]) == (
        "ready",
        "board",
        "Onboarding board",
    )
    assert [(b["type"], b["title"]) for b in body["blocks"]] == [
        ("column", "Pricing"),
        ("column", "Next steps"),
        ("column", "Later"),
    ]
    first = body["blocks"][0]["cards"][0]
    assert first["kind"] == "decision" and first["sources"][0]["line"] == 2


def test_move_card_changes_the_board_marks_it_edited_and_answers_with_the_new_one(
    api, session, template
):
    _, doc_id = make_board(api, session, template)
    before = get(api, doc_id)
    response = edit(api, doc_id, op="move_card", card=1, to_column=2, index=0)
    assert response.status_code == 200
    body = response.json()
    assert column_cards(body) == [
        ["Onboarding is 50,000 naira"],
        ["Send the plan"],
        ["Who signs?", "Revisit pricing in spring"],
    ]
    assert body["edited"] is True and body["updated_at"] != before["updated_at"]
    assert column_cards(get(api, doc_id)) == column_cards(body)  # it was saved


@pytest.mark.parametrize(
    "op, expect",
    [
        (
            {"op": "rename_column", "column": 1, "title": "Actions"},
            lambda b: b["blocks"][1]["title"] == "Actions",
        ),
        ({"op": "add_column", "title": "Risks"}, lambda b: b["blocks"][3]["title"] == "Risks"),
        ({"op": "delete_column", "column": 2}, lambda b: len(b["blocks"]) == 2),
        (
            {
                "op": "add_card",
                "column": 2,
                "text": "Call Daniel",
                "kind": "action",
                "owner": "Ada",
            },
            lambda b: (
                b["blocks"][2]["cards"][-1]["owner"] == "Ada"
                and b["blocks"][2]["cards"][-1]["sources"] == []
            ),
        ),
        (
            {"op": "edit_card", "card": 0, "text": "Onboarding is 60,000 naira"},
            lambda b: (
                b["blocks"][0]["cards"][0]["text"] == "Onboarding is 60,000 naira"
                and b["blocks"][0]["cards"][0]["sources"][0]["line"] == 2
            ),
        ),
        ({"op": "delete_card", "card": 3}, lambda b: b["blocks"][2]["cards"] == []),
        (
            {"op": "recolour", "card": 1, "kind": "decision"},
            lambda b: b["blocks"][0]["cards"][1]["kind"] == "decision",
        ),
    ],
    ids=lambda v: v["op"] if isinstance(v, dict) else None,
)
def test_each_operation_is_saved(api, session, template, op, expect):
    _, doc_id = make_board(api, session, template)
    response = edit(api, doc_id, **op)
    assert response.status_code == 200
    assert expect(response.json()) and expect(get(api, doc_id))


def test_an_edit_made_by_hand_is_audited_like_any_text(api, session, template):
    _, doc_id = make_board(api, session, template)
    edit(api, doc_id, op="add_card", column=0, text="Setup costs ₦450,000 start [need: date]")
    checks = get(api, doc_id)["checks"]
    assert [f["text"] for f in checks["flags"]] == ["₦450,000"]
    assert [x["what"] for x in checks["blanks"]] == ["date"]


def test_card_text_from_a_person_is_plain_and_bounded(api, session, template):
    _, doc_id = make_board(api, session, template)
    body = edit(
        api, doc_id, op="add_card", column=0, text="<b>Hi</b> [x](http://evil.test) " + "w" * 900
    ).json()
    text = body["blocks"][0]["cards"][-1]["text"]
    assert "<" not in text and "http" not in text and len(text) <= 300


def test_a_screen_that_is_out_of_date_is_409_and_nothing_changes(api, session, template):
    _, doc_id = make_board(api, session, template)
    old = get(api, doc_id)["updated_at"]
    assert edit(api, doc_id, op="delete_card", card=0).status_code == 200
    saved = get(api, doc_id)
    stale = api.call("PATCH", url(doc_id), {"revision": old, "op": "delete_card", "card": 0})
    error_of(stale, 409, "stale_board")
    assert get(api, doc_id)["blocks"] == saved["blocks"]


def test_an_index_that_no_longer_exists_is_409(api, session, template):
    _, doc_id = make_board(api, session, template)
    error_of(edit(api, doc_id, op="delete_card", card=40), 409, "stale_board")
    error_of(edit(api, doc_id, op="move_card", card=0, to_column=9, index=0), 409, "stale_board")
    error_of(edit(api, doc_id, op="move_card", card=0, to_column=1, index=5), 409, "stale_board")
    assert get(api, doc_id)["edited"] is False


def test_the_revision_is_required_and_must_be_a_time(api, session, template):
    _, doc_id = make_board(api, session, template)
    error_of(
        api.call("PATCH", url(doc_id), {"op": "delete_card", "card": 0}), 422, "revision_required"
    )
    error_of(
        api.call("PATCH", url(doc_id), {"revision": "yesterday", "op": "delete_card", "card": 0}),
        422,
        "revision_invalid",
    )


@pytest.mark.parametrize(
    "body",
    [
        {"op": "teleport", "card": 0},
        {"op": "move_card", "card": 0, "to_column": 0},
        {"op": "move_card", "card": -1, "to_column": 0, "index": 0},
        {"op": "move_card", "card": "0", "to_column": 0, "index": 0},
        {"op": "move_card", "card": 0, "to_column": 0, "index": 0, "sneaky": True},
        {"op": "add_card", "column": 0, "text": "x" * 2001},
        {"op": "add_card", "column": 0, "text": "x", "kind": "red"},
        {"op": "rename_column", "column": 0, "title": "t" * 2001},
        {"op": "edit_card", "card": 0},
        {"blocks": []},
    ],
)
def test_bodies_are_strict_and_bounded(api, session, template, body):
    _, doc_id = make_board(api, session, template)
    full = {"revision": get(api, doc_id)["updated_at"], **body}
    error_of(api.call("PATCH", url(doc_id), full), 422, "validation_error")
    assert get(api, doc_id)["edited"] is False


def test_an_operation_that_cannot_be_done_says_why(api, session, template):
    _, doc_id = make_board(api, session, template)
    error_of(edit(api, doc_id, op="rename_column", column=0, title="<i> </i>"), 422, "title_empty")
    error_of(edit(api, doc_id, op="add_card", column=0, text="  "), 422, "card_empty")


def test_a_board_and_a_column_have_a_size_limit(api, session, template):
    full = [column(f"T{n}", [card(f"c{n}-{m}") for m in range(30)]) for n in range(12)]
    _, doc_id = make_board(api, session, template, full)
    error_of(edit(api, doc_id, op="add_column", title="One too many"), 422, "board_full")
    error_of(edit(api, doc_id, op="add_card", column=0, text="one too many"), 422, "column_full")
    error_of(edit(api, doc_id, op="move_card", card=30, to_column=0, index=0), 422, "column_full")
    assert edit(api, doc_id, op="move_card", card=0, to_column=0, index=29).status_code == 200


def test_the_last_column_cannot_be_deleted(api, session, template):
    _, doc_id = make_board(api, session, template, [column("Only", [card("x")])])
    error_of(edit(api, doc_id, op="delete_column", column=0), 422, "last_column")


def test_a_draft_that_is_not_a_board_is_409(api, session, template):
    meeting = meeting_of(session, template)
    made = api.post(
        "/api/documents", {"recipe_key": "follow_up_email", "meeting_ids": [meeting.id]}
    )
    svc.process_next(session, datetime.now(UTC), client=FakeModel(reply([para("Hi")])))
    doc_id = made.json()["id"]
    error_of(edit(api, doc_id, op="delete_card", card=0), 409, "not_a_board")


def test_a_board_still_being_written_is_409_writing(api, session, template):
    meeting = meeting_of(session, template)
    made = api.post("/api/documents", {"recipe_key": "board", "meeting_ids": [meeting.id]}).json()
    body = {"revision": made["updated_at"], "op": "delete_card", "card": 0}
    error_of(api.call("PATCH", url(made["id"]), body), 409, "writing")


def test_a_board_that_failed_is_409_not_ready(api, session, template):
    meeting = meeting_of(session, template)
    made = api.post("/api/documents", {"recipe_key": "board", "meeting_ids": [meeting.id]}).json()
    svc.process_next(session, datetime.now(UTC), client=FakeModel(reply([column("Empty", [])])))
    assert get(api, made["id"])["failure_code"] == "structure"
    body = {"revision": made["updated_at"], "op": "delete_card", "card": 0}
    error_of(api.call("PATCH", url(made["id"]), body), 409, "not_ready")


def test_csrf_is_required(api, session, template):
    _, doc_id = make_board(api, session, template)
    body = {"revision": get(api, doc_id)["updated_at"], "op": "delete_card", "card": 0}
    for token in (False, "wrong"):
        error_of(api.call("PATCH", url(doc_id), body, csrf=token), 403, "csrf_rejected")


def test_another_person_gets_404_and_changes_nothing(api, session, template, stranger, app):
    _, doc_id = make_board(api, session, template)
    revision = get(api, doc_id)["updated_at"]
    other = sign_in(app, OTHER_EMAIL)
    body = {"revision": revision, "op": "delete_card", "card": 0}
    error_of(other.call("PATCH", url(doc_id), body), 404, "not_found")
    assert get(api, doc_id)["edited"] is False


def test_only_the_creator_edits_even_a_teammate_it_is_shared_with(
    api, session, template, owner, teammate, app
):
    from app import teams
    from tests.test_team_visibility import TEAMMATE_EMAIL

    _, doc_id = make_board(api, session, template)
    team = teams.ensure_team(session, owner)
    session.add(DocumentShare(document_id=doc_id, team_id=team.id, shared_by_user_id=owner.id))
    session.commit()
    mate = sign_in(app, TEAMMATE_EMAIL)
    body = {"revision": get(api, doc_id)["updated_at"], "op": "delete_card", "card": 0}
    error_of(mate.call("PATCH", url(doc_id), body), 404, "not_found")
    assert get(api, doc_id)["edited"] is False


def test_a_draft_that_does_not_exist_is_404_for_any_id(api):
    body = {"revision": "2026-11-24T12:00:00Z", "op": "delete_card", "card": 0}
    for doc_id in (1, 999999, 99999999999):
        error_of(api.call("PATCH", url(doc_id), body), 404, "not_found")


def test_two_edits_at_once_cannot_both_win(api, session, template):
    """Two tabs that saw the same revision: the second is stale, whatever the order of the calls."""
    _, doc_id = make_board(api, session, template)
    revision = get(api, doc_id)["updated_at"]
    one = {"revision": revision, "op": "delete_card", "card": 0}
    two = {"revision": revision, "op": "delete_card", "card": 1}
    assert api.call("PATCH", url(doc_id), one).status_code == 200
    error_of(api.call("PATCH", url(doc_id), two), 409, "stale_board")
    assert session.scalar(select(Document.edited).where(Document.id == doc_id)) is True


def test_a_board_exports_to_pdf_and_word_through_the_documents_export_route(api, session, template):
    """S2's export route, the renderer every draft uses: a board is a section per column with its
    cards as bullets, each with its kind and owner."""
    import io

    from docx import Document as Docx

    _, doc_id = make_board(api, session, template)
    pdf = api.get(f"/api/documents/{doc_id}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    docx = api.get(f"/api/documents/{doc_id}/export?format=docx")
    assert docx.status_code == 200
    text = "\n".join(p.text for p in Docx(io.BytesIO(docx.content)).paragraphs)
    assert "Pricing" in text and "Decision: Onboarding is 50,000 naira" in text
    assert "Action: Send the plan (Ada)" in text
    assert api.get(f"/api/documents/{doc_id}/export?format=csv").status_code == 409  # no table
    # and the export uses the board as it was last edited
    edit(api, doc_id, op="rename_column", column=0, title="Costs")
    again = api.get(f"/api/documents/{doc_id}/export?format=docx")
    assert "Costs" in "\n".join(p.text for p in Docx(io.BytesIO(again.content)).paragraphs)
