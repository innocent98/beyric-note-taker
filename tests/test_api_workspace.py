"""The Decisions and Review screens' API (`app/api_workspace.py`); fixtures from test_api.py."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import workspace
from app.models import Decision, Meeting, ReviewItem, VocabularyTerm
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
    make_user,
    owner,
    ready,
    stranger,
    template,
)
from tests.test_workspace import GOOD, NOTES, TRANSCRIPT, FakeChat

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
OTHER_NOTES = NOTES.replace("Launch moves to 14 March.", "We hire two engineers.")


def extracted(session, template, *, notes=NOTES, created=NOW, **fields) -> Meeting:
    meeting = ready(
        session, template, notes=notes, speaker_transcript=TRANSCRIPT, created_at=created, **fields
    )
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)
    return meeting


def ids(response, key="groups"):
    return [group["meeting"]["id"] for group in response.json()[key]]


# ---- decisions ----


def test_decisions_are_grouped_by_meeting_newest_first(api, session, template):
    older = extracted(session, template, created=NOW - timedelta(days=2))
    newer = extracted(session, template, notes=OTHER_NOTES, native_id="klm-nopq-rst")
    body = api.get("/api/decisions").json()
    assert [g["meeting"]["id"] for g in body["groups"]] == [newer.id, older.id]
    first = body["groups"][0]
    assert first["meeting"]["title"] == "Google Meet klm-nopq-rst"
    assert first["meeting"]["platform"] == "google_meet"
    assert first["meeting"]["created_at"].endswith("Z")
    assert [d["text"] for d in first["decisions"]] == [
        "We hire two engineers.",
        "Budget is capped at £4,000.",
    ]
    assert set(first["decisions"][0]) == {"id", "text"}
    assert (body["has_more"], body["next_before_id"]) == (False, None)


def test_search_matches_decision_text_or_meeting_title_and_ignores_case(api, session, template):
    a = extracted(session, template)
    b = extracted(session, template, notes=OTHER_NOTES, native_id="zzz-yyyy-xxx")
    assert ids(api.get("/api/decisions?q=LAUNCH+moves")) == [a.id]
    only_text = api.get("/api/decisions?q=launch").json()["groups"][0]["decisions"]
    assert [d["text"] for d in only_text] == ["Launch moves to 14 March."]
    # a title match shows every decision of that meeting
    by_title = api.get("/api/decisions?q=zzz-yyyy").json()["groups"]
    assert [g["meeting"]["id"] for g in by_title] == [b.id]
    assert len(by_title[0]["decisions"]) == 2
    assert api.get("/api/decisions?q=nothing+like+this").json()["groups"] == []


def test_search_treats_percent_and_underscore_as_plain_characters(api, session, template):
    extracted(session, template)
    assert api.get("/api/decisions?q=%25").json()["groups"] == []
    assert api.get("/api/decisions?q=_").json()["groups"] == []


def test_decisions_page_by_meeting(api, session, template):
    metas = [extracted(session, template, created=NOW - timedelta(days=i)) for i in range(3)]
    page = api.get("/api/decisions?limit=2").json()
    assert [g["meeting"]["id"] for g in page["groups"]] == [metas[2].id, metas[1].id]
    assert page["has_more"] is True and page["next_before_id"] == metas[1].id
    rest = api.get(f"/api/decisions?limit=2&before_id={page['next_before_id']}").json()
    assert [g["meeting"]["id"] for g in rest["groups"]] == [metas[0].id]
    assert rest["has_more"] is False


def test_bad_paging_is_a_validation_error(api):
    for query in ("limit=0", "limit=51", "before_id=0", "q=" + "x" * 201):
        error_of(api.get(f"/api/decisions?{query}"), 422, "validation_error")


def test_a_hidden_decision_leaves_the_log_and_stays_hidden_after_a_rerun(api, session, template):
    meeting = extracted(session, template)
    target = api.get("/api/decisions").json()["groups"][0]["decisions"][0]
    assert api.post(f"/api/decisions/{target['id']}/hide").status_code == 204
    assert api.post(f"/api/decisions/{target['id']}/hide").status_code == 204  # again: same
    left = api.get("/api/decisions").json()["groups"][0]["decisions"]
    assert target["id"] not in [d["id"] for d in left]
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)
    again = api.get("/api/decisions").json()["groups"][0]["decisions"]
    assert target["id"] not in [d["id"] for d in again]


def test_a_meeting_whose_decisions_are_all_hidden_drops_out(api, session, template):
    extracted(session, template)
    for group in api.get("/api/decisions").json()["groups"]:
        for decision in group["decisions"]:
            api.post(f"/api/decisions/{decision['id']}/hide")
    assert api.get("/api/decisions").json()["groups"] == []


def test_nobody_sees_or_hides_another_users_decisions(api, session, template, stranger):
    from tests.test_web import Template  # noqa: PLC0415

    theirs = Template(owner_user_id=stranger.id, name="General", instructions="x")
    session.add(theirs)
    session.commit()
    foreign = extracted(session, theirs)
    assert api.get("/api/decisions").json()["groups"] == []
    decision = session.scalars(select(Decision).where(Decision.meeting_id == foreign.id)).first()
    error_of(api.post(f"/api/decisions/{decision.id}/hide"), 404, "not_found")
    error_of(api.post("/api/decisions/99999/hide"), 404, "not_found")
    session.refresh(decision)
    assert decision.hidden_at is None


# ---- review ----


def test_review_lists_open_items_with_their_meeting(api, session, template):
    meeting = extracted(session, template)
    body = api.get("/api/review").json()
    assert body["open_count"] == 2 and body["truncated"] is False
    first, second = body["items"]
    assert set(first) == {"id", "text", "heard_as", "checked_at", "meeting"}
    assert (first["text"], first["heard_as"], first["checked_at"]) == (
        '"Metrika" may be misheard.',
        "Metrika",
        None,
    )
    assert first["meeting"]["id"] == meeting.id and first["meeting"]["title"].startswith("Google")
    assert second["heard_as"] is None


def test_check_and_uncheck_move_an_item_between_the_lists(api, session, template):
    extracted(session, template)
    item = api.get("/api/review").json()["items"][0]
    checked = api.post(f"/api/review/{item['id']}/check")
    assert checked.status_code == 200 and checked.json()["checked_at"].endswith("Z")
    assert api.get("/api/review").json()["open_count"] == 1
    done = api.get("/api/review?state=checked").json()
    assert [i["id"] for i in done["items"]] == [item["id"]]
    undone = api.post(f"/api/review/{item['id']}/uncheck")
    assert undone.status_code == 200 and undone.json()["checked_at"] is None
    assert api.get("/api/review").json()["open_count"] == 2
    assert api.get("/api/review?state=checked").json()["items"] == []


def test_checking_twice_keeps_the_first_time(api, session, template):
    extracted(session, template)
    item_id = api.get("/api/review").json()["items"][0]["id"]
    first = api.post(f"/api/review/{item_id}/check").json()["checked_at"]
    assert api.post(f"/api/review/{item_id}/check").json()["checked_at"] == first


def test_an_unknown_review_state_is_a_validation_error(api):
    error_of(api.get("/api/review?state=all"), 422, "validation_error")


def test_adding_to_vocabulary_creates_the_term_and_checks_the_item(api, session, template, owner):
    extracted(session, template)
    item = api.get("/api/review").json()["items"][0]
    response = api.post(f"/api/review/{item['id']}/vocabulary", {"term": "  Metrika "})
    assert response.status_code == 201
    body = response.json()
    assert body["term"]["term"] == "Metrika" and body["item"]["checked_at"] is not None
    assert session.scalars(select(VocabularyTerm.term)).all() == ["Metrika"]


def test_a_term_that_exists_is_409_and_the_item_is_still_checked(api, session, template, owner):
    extracted(session, template)
    session.add(VocabularyTerm(owner_user_id=owner.id, term="Metrika"))
    session.commit()
    item_id = api.get("/api/review").json()["items"][0]["id"]
    error_of(
        api.post(f"/api/review/{item_id}/vocabulary", {"term": "Metrika"}), 409, "duplicate_term"
    )
    assert api.get("/api/review?state=checked").json()["items"][0]["id"] == item_id


def test_a_bad_term_is_400_and_nothing_is_checked(api, session, template):
    extracted(session, template)
    item_id = api.get("/api/review").json()["items"][0]["id"]
    error_of(api.post(f"/api/review/{item_id}/vocabulary", {"term": "  "}), 400, "invalid_term")
    error_of(api.post(f"/api/review/{item_id}/vocabulary", {"term": "a<b"}), 400, "invalid_term")
    assert api.get("/api/review").json()["open_count"] == 2


def test_review_items_belong_to_their_owner_alone(api, session, template, stranger):
    from tests.test_web import Template  # noqa: PLC0415

    theirs = Template(owner_user_id=stranger.id, name="General", instructions="x")
    session.add(theirs)
    session.commit()
    foreign = extracted(session, theirs)
    assert api.get("/api/review").json()["open_count"] == 0
    item = session.scalars(select(ReviewItem).where(ReviewItem.meeting_id == foreign.id)).first()
    for action, body in (("check", None), ("uncheck", None), ("vocabulary", {"term": "x"})):
        error_of(api.post(f"/api/review/{item.id}/{action}", body), 404, "not_found")
    session.refresh(item)
    assert item.checked_at is None
    assert session.scalars(select(VocabularyTerm)).all() == []


# ---- the sidebar badge and the meeting's state ----


def test_the_session_counts_open_review_items_for_the_badge(api, anon_client, session, template):
    assert api.get("/api/session").json()["badges"] == {"review_open": 0, "tasks_new": 0}
    extracted(session, template)
    assert api.get("/api/session").json()["badges"] == {"review_open": 2, "tasks_new": 1}
    item_id = api.get("/api/review").json()["items"][0]["id"]
    api.post(f"/api/review/{item_id}/check")
    assert api.get("/api/session").json()["badges"] == {"review_open": 1, "tasks_new": 1}


def test_a_logged_out_session_has_no_badges(anon):
    assert anon.get("/api/session").json()["badges"] is None


def test_the_meeting_reports_where_its_extraction_stands(api, session, template):
    pending = ready(session, template)
    assert api.get(f"/api/meetings/{pending.id}").json()["extraction_status"] is None
    workspace.mark_pending(pending)
    session.commit()
    assert api.get(f"/api/meetings/{pending.id}").json()["extraction_status"] == "pending"
    done = extracted(session, template)
    assert api.get(f"/api/meetings/{done.id}").json()["extraction_status"] == "done"
    unfinished = api.get("/api/meetings").json()["meetings"][0]
    assert "extraction_status" not in unfinished  # the list stays as it was


# ---- the route list ----


def test_the_workspace_routes_are_exactly_these(app):
    ours = {(m, p) for m, p in api_routes(app) if p.startswith(("/api/decisions", "/api/review"))}
    assert ours == {
        ("GET", "/api/decisions"),
        ("POST", "/api/decisions/{decision_id}/hide"),
        ("GET", "/api/review"),
        ("POST", "/api/review/{item_id}/check"),
        ("POST", "/api/review/{item_id}/uncheck"),
        ("POST", "/api/review/{item_id}/vocabulary"),
    }
