"""The Decisions record (spec 2026-11-08, section 3): what extract-v3 stores per decision, what a
person's hand-set status survives, who may change it, and who sees which decision, task and link.

The cast is the Tasks API tests': `owner` (owner of the team and the meetings), `member` on the
owner's team, `outsider` on a team of their own.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import extraction, workspace
from app.extraction import ModelDecision, ModelExtraction, ParsedNotes, check_extraction
from app.models import Decision, DecisionTask, Task, UserSettings
from tests.test_api_tasks import (  # noqa: F401  (fixtures used by name)
    MEMBER_EMAIL,
    Api,
    add_task,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    as_user,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    member,
    outsider,
    owner,
    ready,
    share,
    stranger,
    team,
    template,
)
from tests.test_workspace import NOTES, TRANSCRIPT, FakeChat

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
LAUNCH, BUDGET = "Launch moves to 14 March.", "Budget is capped at £4,000."
ACTION = {
    "index": 0,
    "title": "send the contract",
    "spoken_owner": None,
    "due_phrase": "Friday",
    "due_date": None,
    "quote": None,
    "confidence": "high",
    "reason": None,
}
GRACE_SAID = "Launch is fourteenth of March."


def reply(decisions, actions=(ACTION,)):
    return {
        "action_items": list(actions),
        "review": [],
        "milestones": [],
        "decisions": decisions,
    }


GOOD = reply(
    [
        {
            "index": 0,
            "decided_by": "Grace",
            "quote": GRACE_SAID,
            "proposed": False,
            "related_actions": [0],
        },
        {
            "index": 1,
            "decided_by": "Nobody Here",
            "quote": "We never said this at all.",
            "proposed": True,
            "related_actions": [0, 0, 5, -1],
        },
    ]
)


def extract(session, meeting, script=GOOD):
    client = FakeChat([script])
    workspace.run_extraction(session, meeting, client=client, now=NOW)
    session.expire_all()
    return client


def decisions_of(session, meeting) -> dict[str, Decision]:
    rows = session.scalars(
        select(Decision).where(Decision.meeting_id == meeting.id).order_by(Decision.position)
    )
    return {d.text: d for d in rows}


@pytest.fixture
def meeting(session, template, owner):
    return ready(
        session,
        template,
        notes=NOTES,
        speaker_transcript=TRANSCRIPT,
        created_at=NOW,
    )


# ---- the checks (pure) ----


NOTES_PARSED = ParsedNotes(["A", "B", "C"], ["task one", "task two"], [], [])


def checked(*entries):
    return check_extraction(
        ModelExtraction(decisions=list(entries)), NOTES_PARSED, TRANSCRIPT, NOW.date()
    ).decisions


def test_a_decider_must_be_a_name_in_the_transcript_and_a_quote_must_have_been_said():
    got = checked(
        ModelDecision(index=0, decided_by="Grace", quote=GRACE_SAID),
        ModelDecision(index=1, decided_by="Zebediah", quote="They agreed on pricing."),
    )
    assert (got[0].decided_by, got[0].quote) == ("Grace", GRACE_SAID)
    assert (got[1].decided_by, got[1].quote) == (None, None)


def test_a_decider_must_be_a_speaker_not_just_words_said_in_the_meeting():
    # "Metrika" and "March" are said in the meeting; "Grace" and "Adebayo" are its speakers
    for phrase in (
        "Metrika",
        "March",
        "the budget",
        "everyone",
        "Grace and Adebayo agreed it",
        "Grace 2",
    ):
        assert checked(ModelDecision(index=0, decided_by=phrase))[0].decided_by is None, phrase
    assert checked(ModelDecision(index=0, decided_by="grace"))[0].decided_by == "grace"
    assert checked(ModelDecision(index=0, decided_by="Adebayo"))[0].decided_by == "Adebayo"


def test_a_first_name_matches_a_fuller_speaker_label_and_an_unlabelled_transcript_has_none():
    full = "[Grace Okoro]: Launch is fourteenth of March.\n[Tunde]: Fine."
    notes = ParsedNotes(["A"], [], [], [])
    entry = ModelExtraction(decisions=[ModelDecision(index=0, decided_by="Grace")])
    assert check_extraction(entry, notes, full, NOW.date()).decisions[0].decided_by == "Grace"
    longer = ModelExtraction(decisions=[ModelDecision(index=0, decided_by="Grace Okoro Junior")])
    assert check_extraction(longer, notes, full, NOW.date()).decisions[0].decided_by is None
    plain = "Grace said launch is fourteenth of March."  # no speaker labels at all
    assert check_extraction(entry, notes, plain, NOW.date()).decisions[0].decided_by is None


def test_a_quote_that_is_almost_there_still_passes_the_same_check_tasks_use():
    got = checked(ModelDecision(index=0, quote="launch is fourteenth of march"))
    assert got[0].quote == "launch is fourteenth of march"


def test_related_actions_keep_only_real_indices_once_each_in_order():
    got = checked(ModelDecision(index=0, related_actions=[1, 0, 1, 2, -1, 99]))
    assert got[0].related_actions == (1, 0)


def test_every_decision_bullet_gets_an_entry_and_stray_or_repeated_indices_are_ignored():
    got = checked(
        ModelDecision(index=2, proposed=True),
        ModelDecision(index=2, proposed=False),  # the second answer for an index is ignored
        ModelDecision(index=7, decided_by="Grace"),  # no such bullet
    )
    assert [(d.index, d.proposed) for d in got] == [(0, False), (1, False), (2, True)]
    assert all(d.decided_by is None and d.quote is None for d in got)


def test_the_reply_may_leave_decisions_out_entirely():
    result = extraction.ModelExtraction.model_validate({"action_items": []})
    assert result.decisions == []


def test_the_prompt_lists_the_decisions_and_the_schema_asks_for_them():
    msg = extraction._user_message(NOTES_PARSED, "t", NOW.date(), "UTC", [], True)
    assert "<decisions>\n0: A\n1: B\n2: C\n</decisions>" in msg
    assert "decisions" in extraction.EXTRACT_SCHEMA_FULL["required"]
    item = extraction.EXTRACT_SCHEMA_FULL["properties"]["decisions"]["items"]
    assert set(item["required"]) == set(item["properties"])  # strict mode wants all of them
    assert extraction.EXTRACT_PROMPT_VERSION == "extract-v3"


# ---- what a run stores ----


def test_a_run_stores_the_checked_answer_per_decision_and_links_the_tasks(session, meeting):
    extract(session, meeting)
    rows = decisions_of(session, meeting)
    launch, budget = rows[LAUNCH], rows[BUDGET]
    assert (launch.status, launch.decided_by, launch.quote) == ("approved", "Grace", GRACE_SAID)
    assert (budget.status, budget.decided_by, budget.quote) == ("proposed", None, None)
    task = session.scalars(select(Task).where(Task.meeting_id == meeting.id)).one()
    links = session.execute(select(DecisionTask.decision_id, DecisionTask.task_id)).all()
    assert sorted(links) == sorted([(launch.id, task.id), (budget.id, task.id)])
    assert all(d.edited_at is None for d in rows.values())


def test_a_failed_check_stores_nothing_rather_than_a_guess(session, meeting):
    bad = reply(
        [
            {
                "index": 0,
                "decided_by": "Zebediah",
                "quote": "never said",
                "proposed": False,
                "related_actions": [9],
            }
        ]
    )
    extract(session, meeting, bad)
    launch = decisions_of(session, meeting)[LAUNCH]
    assert (launch.status, launch.decided_by, launch.quote) == ("approved", None, None)
    assert session.scalars(select(DecisionTask)).all() == []


def test_a_meeting_with_decisions_and_no_actions_is_asked_about_too(session, template, owner):
    notes = NOTES.replace("- Tunde: send the contract (by Friday)", "None noted")
    bare = ready(session, template, notes=notes, speaker_transcript=TRANSCRIPT, created_at=NOW)
    client = extract(session, bare, reply(GOOD["decisions"][:1], actions=[]))
    assert len(client.calls) == 1
    assert decisions_of(session, bare)[LAUNCH].decided_by == "Grace"
    assert session.scalars(select(DecisionTask)).all() == []


def test_the_ledger_records_the_new_prompt_version(session, meeting):
    from app.models import AiRun

    extract(session, meeting)
    assert session.scalars(select(AiRun)).one().prompt_version == "extract-v3"
    session.refresh(meeting)
    assert meeting.extraction_version == "extract-v3"


def test_a_decision_the_notes_parser_made_before_the_call_is_approved_with_no_decider(
    session, meeting
):
    workspace.run_extraction(session, meeting, client=FakeChat(["not json"]), now=NOW)
    session.expire_all()
    rows = decisions_of(session, meeting)
    assert {d.status for d in rows.values()} == {"approved"}
    assert {d.decided_by for d in rows.values()} == {None}


# ---- a person's changes survive a re-extraction ----


def rerun(session, meeting, script):
    workspace.mark_pending(meeting)
    session.commit()
    workspace.run_extraction(session, meeting, client=FakeChat([script]), now=NOW)
    session.expire_all()


def test_hand_set_status_revisit_and_replaced_by_survive_a_rerun(session, meeting):
    extract(session, meeting)
    rows = decisions_of(session, meeting)
    launch, budget = rows[LAUNCH], rows[BUDGET]
    launch.status, launch.revisit_on = "rejected", date(2026, 11, 20)
    budget.status, budget.replaced_by_id, budget.revisit_on = (
        "replaced",
        launch.id,
        date(2026, 12, 1),
    )
    for row in (launch, budget):
        row.edited_at = NOW
    session.commit()
    # the model now says the opposite of both
    flipped = reply(
        [
            {**GOOD["decisions"][0], "proposed": True},
            {**GOOD["decisions"][1], "proposed": False},
        ]
    )
    rerun(session, meeting, flipped)
    rows = decisions_of(session, meeting)
    launch, budget = rows[LAUNCH], rows[BUDGET]
    assert (launch.status, launch.revisit_on) == ("rejected", date(2026, 11, 20))
    assert (budget.status, budget.replaced_by_id) == ("replaced", launch.id)
    assert budget.revisit_on == date(2026, 12, 1)


def test_the_model_may_still_move_a_decision_nobody_has_touched(session, meeting):
    extract(session, meeting)
    assert decisions_of(session, meeting)[BUDGET].status == "proposed"
    rerun(session, meeting, reply([{**GOOD["decisions"][1], "proposed": False}]))
    assert decisions_of(session, meeting)[BUDGET].status == "approved"


def test_a_hidden_decision_stays_hidden_and_keeps_its_row_across_a_rerun(session, meeting):
    extract(session, meeting)
    row = decisions_of(session, meeting)[LAUNCH]
    row_id = row.id
    row.hidden_at = NOW
    session.commit()
    rerun(session, meeting, GOOD)
    again = decisions_of(session, meeting)[LAUNCH]
    assert (again.id, again.hidden_at is not None) == (row_id, True)


def test_a_failed_second_call_leaves_the_first_answer_as_it_was(session, meeting):
    extract(session, meeting)
    workspace.mark_pending(meeting)
    session.commit()
    workspace.run_extraction(session, meeting, client=FakeChat(["not json"]), now=NOW)
    session.expire_all()
    assert decisions_of(session, meeting)[LAUNCH].decided_by == "Grace"


def test_links_follow_the_latest_answer_and_go_with_a_task_that_goes(session, meeting):
    extract(session, meeting)
    assert len(session.scalars(select(DecisionTask)).all()) == 2
    rerun(session, meeting, reply([{**GOOD["decisions"][0], "related_actions": []}]))
    assert session.scalars(select(DecisionTask)).all() == []
    rerun(session, meeting, GOOD)
    task = session.scalars(select(Task)).one()
    session.delete(task)
    session.commit()
    assert session.scalars(select(DecisionTask)).all() == []  # cascade


# ---- the API: list shape, tabs, counts, banner ----


def listed(api, query=""):
    return api.get(f"/api/decisions{query}").json()


def flat(body):
    return [d for g in body["groups"] for d in g["decisions"]]


def by_text(body):
    return {d["text"]: d for d in flat(body)}


def test_the_list_carries_status_evidence_line_and_the_related_tasks(api, session, meeting):
    extract(session, meeting)
    body = listed(api)
    launch, budget = by_text(body)[LAUNCH], by_text(body)[BUDGET]
    task = session.scalars(select(Task)).one()
    assert launch == {
        "id": launch["id"],
        "text": LAUNCH,
        "status": "approved",
        "decided_by": "Grace",
        "quote": GRACE_SAID,
        "quote_speaker": "Grace",
        "line": 2,
        "revisit_on": None,
        "replaced_by": None,
        "replaces": [],
        "related_tasks": [{"id": task.id, "title": task.title, "status": "todo"}],
        "can_edit": True,
    }
    assert (budget["status"], budget["quote"], budget["line"]) == ("proposed", None, None)
    assert budget["quote_speaker"] is None


def test_counts_cover_the_whole_log_whatever_the_tab_or_search_says(api, session, meeting):
    extract(session, meeting)
    first = by_text(listed(api))[LAUNCH]["id"]
    assert api.call("PATCH", f"/api/decisions/{first}", {"status": "rejected"}).status_code == 200
    expected = {"all": 2, "approved": 0, "proposed": 1, "replaced": 0, "rejected": 1}
    for query in ("", "?status=proposed", "?q=budget", "?status=replaced"):
        assert listed(api, query)["counts"] == expected
    assert [d["text"] for d in flat(listed(api, "?status=rejected"))] == [LAUNCH]
    assert [d["text"] for d in flat(listed(api, "?status=proposed"))] == [BUDGET]
    assert listed(api, "?status=approved")["groups"] == []
    error_of(api.get("/api/decisions?status=bogus"), 422, "validation_error")


def test_a_hidden_decision_is_in_no_count(api, session, meeting):
    extract(session, meeting)
    first = by_text(listed(api))[LAUNCH]["id"]
    assert api.post(f"/api/decisions/{first}/hide").status_code == 204
    assert listed(api)["counts"]["all"] == 1
    error_of(api.call("PATCH", f"/api/decisions/{first}", {"status": "rejected"}), 404, "not_found")


def monday_to_sunday(today: date):
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def test_the_banner_counts_open_decisions_due_this_week_in_the_persons_zone(
    api, session, meeting, owner
):
    extract(session, meeting)
    rows = decisions_of(session, meeting)
    session.add(UserSettings(user_id=owner.id, time_zone="Pacific/Kiritimati"))  # UTC+14
    session.commit()
    today = (
        datetime.now(UTC).astimezone(__import__("zoneinfo").ZoneInfo("Pacific/Kiritimati")).date()
    )
    monday, sunday = monday_to_sunday(today)
    rows[LAUNCH].revisit_on = sunday
    rows[BUDGET].revisit_on = monday - timedelta(days=1)  # last week: not counted
    session.commit()
    assert listed(api)["due_this_week"] == 1
    rows[BUDGET].revisit_on = monday
    session.commit()
    assert listed(api)["due_this_week"] == 2
    rows[BUDGET].status = "replaced"  # a replaced or rejected one is no longer to be revisited
    session.commit()
    assert listed(api)["due_this_week"] == 1
    rows[LAUNCH].revisit_on = sunday + timedelta(days=1)
    session.commit()
    assert listed(api)["due_this_week"] == 0


# ---- the API: PATCH ----


def patch(api, decision_id, **body):
    return api.call("PATCH", f"/api/decisions/{decision_id}", body)


def ids_of(session, meeting):
    return {text: d.id for text, d in decisions_of(session, meeting).items()}


def test_the_owner_changes_status_and_revisit_date_and_gets_the_decision_back(
    api, session, meeting
):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    response = patch(api, ids[LAUNCH], status="rejected", revisit_on="2026-11-20")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["revisit_on"], body["can_edit"]) == (
        "rejected",
        "2026-11-20",
        True,
    )
    assert body["related_tasks"] and body["decided_by"] == "Grace"
    session.expire_all()
    assert session.get(Decision, ids[LAUNCH]).edited_at is not None
    cleared = patch(api, ids[LAUNCH], revisit_on=None)
    assert (cleared.json()["revisit_on"], cleared.json()["status"]) == (None, "rejected")


def test_replaced_by_names_another_decision_and_both_sides_show_it(api, session, meeting):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    done = patch(api, ids[BUDGET], status="replaced", replaced_by_id=ids[LAUNCH])
    assert done.status_code == 200, done.text
    assert done.json()["replaced_by"] == {"id": ids[LAUNCH], "text": LAUNCH}
    body = listed(api)
    assert by_text(body)[LAUNCH]["replaces"] == [{"id": ids[BUDGET], "text": BUDGET}]
    assert by_text(body)[BUDGET]["replaced_by"] == {"id": ids[LAUNCH], "text": LAUNCH}


def test_moving_off_replaced_clears_the_link_and_replaced_by_needs_replaced(api, session, meeting):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    patch(api, ids[BUDGET], status="replaced", replaced_by_id=ids[LAUNCH])
    back = patch(api, ids[BUDGET], status="approved")
    assert (back.json()["status"], back.json()["replaced_by"]) == ("approved", None)
    assert by_text(listed(api))[LAUNCH]["replaces"] == []
    error_of(patch(api, ids[BUDGET], replaced_by_id=ids[LAUNCH]), 422, "replaced_by_needs_replaced")
    # Replaced with none chosen is allowed, and clearing a chosen one keeps it Replaced
    assert patch(api, ids[BUDGET], status="replaced").json()["replaced_by"] is None
    patch(api, ids[BUDGET], replaced_by_id=ids[LAUNCH])
    assert patch(api, ids[BUDGET], replaced_by_id=None).json()["status"] == "replaced"


def test_replaced_by_refuses_itself_cycles_and_decisions_outside_the_log(
    api, session, meeting, template, owner, outsider
):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    error_of(
        patch(api, ids[LAUNCH], status="replaced", replaced_by_id=ids[LAUNCH]),
        422,
        "replaced_by_self",
    )
    assert patch(api, ids[LAUNCH], status="replaced", replaced_by_id=ids[BUDGET]).status_code == 200
    error_of(
        patch(api, ids[BUDGET], status="replaced", replaced_by_id=ids[LAUNCH]),
        422,
        "replaced_by_cycle",
    )
    error_of(patch(api, ids[LAUNCH], replaced_by_id=999999), 422, "replaced_by_not_found")
    # a decision of someone else's private meeting is not in the caller's log
    theirs = ready(session, template, notes=NOTES, speaker_transcript=TRANSCRIPT)
    theirs.owner_user_id = outsider.id
    session.commit()
    workspace.run_extraction(session, theirs, with_ai=False)
    foreign = session.scalars(select(Decision).where(Decision.meeting_id == theirs.id)).first()
    error_of(patch(api, ids[LAUNCH], replaced_by_id=foreign.id), 422, "replaced_by_not_found")
    # a hidden decision is not a choice either
    assert api.post(f"/api/decisions/{ids[BUDGET]}/hide").status_code == 204
    error_of(patch(api, ids[LAUNCH], replaced_by_id=ids[BUDGET]), 422, "replaced_by_not_found")


def test_a_bad_body_is_a_validation_error_and_changes_nothing(api, session, meeting):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    for body in (
        {"status": "done"},
        {"status": None},
        {"revisit_on": "next friday"},
        {"replaced_by_id": 0},
        {"title": "x"},
    ):
        error_of(patch(api, ids[LAUNCH], **body), 422, "validation_error")
    session.expire_all()
    assert session.get(Decision, ids[LAUNCH]).edited_at is None


def test_a_teammate_who_can_read_it_is_not_the_owner_and_gets_403(
    app, session, meeting, owner, member
):
    share(session, meeting, owner)
    extract(session, meeting)
    ids = ids_of(session, meeting)
    mate = as_user(app, MEMBER_EMAIL)
    seen = by_text(mate.get("/api/decisions").json())
    assert seen[LAUNCH]["can_edit"] is False
    err = error_of(patch(mate, ids[LAUNCH], status="rejected"), 403, "not_owner")
    assert "recorded the meeting" in err["message"]
    session.expire_all()
    row = session.get(Decision, ids[LAUNCH])
    assert (row.status, row.edited_at) == ("approved", None)


def test_someone_who_cannot_read_the_meeting_gets_404_not_403(
    app, session, meeting, owner, member, outsider
):
    extract(session, meeting)  # private
    ids = ids_of(session, meeting)
    for who in (MEMBER_EMAIL, "other@example.test"):
        other = as_user(app, who)
        error_of(patch(other, ids[LAUNCH], status="rejected"), 404, "not_found")
    error_of(patch(as_user(app, MEMBER_EMAIL), 999999, status="rejected"), 404, "not_found")


def test_the_patch_needs_a_login_and_the_csrf_token(anon, api, session, meeting):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    url = f"/api/decisions/{ids[LAUNCH]}"
    no_token = api.call("PATCH", url, {"status": "rejected"}, csrf=False)
    assert no_token.status_code == 403
    session.expire_all()
    assert session.get(Decision, ids[LAUNCH]).status == "approved"


def test_the_patch_is_refused_without_a_login(app, session, meeting):
    from fastapi.testclient import TestClient

    extract(session, meeting)
    ids = ids_of(session, meeting)
    with TestClient(app) as client:
        token = client.get("/api/session").json()["csrf_token"]
        response = client.patch(
            f"/api/decisions/{ids[LAUNCH]}",
            json={"status": "rejected"},
            headers={"X-CSRF-Token": token},
        )
    assert response.status_code == 401


# ---- the API: who sees which decision, task and link ----


def test_a_private_meetings_decisions_and_tasks_never_reach_a_teammate_or_outsider(
    app, session, meeting, owner, member, outsider
):
    extract(session, meeting)  # not shared
    for who in (MEMBER_EMAIL, "other@example.test"):
        body = as_user(app, who).get("/api/decisions").json()
        assert body["groups"] == []
        assert body["counts"]["all"] == 0 and body["due_this_week"] == 0


def test_a_shared_meeting_shows_decisions_and_only_the_tasks_the_reader_may_see(
    app, session, meeting, owner, member, team
):
    share(session, meeting, owner)
    extract(session, meeting)
    ids = ids_of(session, meeting)
    mate = as_user(app, MEMBER_EMAIL)
    related = by_text(mate.get("/api/decisions").json())[LAUNCH]["related_tasks"]
    assert [t["title"] for t in related] == ["send the contract"]
    # unshare: the teammate sees nothing again, the owner everything
    meeting.shared_team_id = None
    session.commit()
    assert mate.get("/api/decisions").json()["groups"] == []
    assert flat(as_user(app, "owner@example.test").get("/api/decisions").json())
    assert ids


def test_suggested_and_dismissed_tasks_are_never_a_related_task(api, session, meeting, team):
    extract(session, meeting)
    task = session.scalars(select(Task)).one()
    launch = by_text(listed(api))[LAUNCH]
    assert [t["id"] for t in launch["related_tasks"]] == [task.id]
    task.suggested = True
    session.commit()
    assert by_text(listed(api))[LAUNCH]["related_tasks"] == []
    task.suggested, task.deleted_at = False, NOW  # dismissed: a tombstone
    session.commit()
    assert by_text(listed(api))[LAUNCH]["related_tasks"] == []
    task.suggested, task.deleted_at = False, None  # accepted: it appears (the link was kept)
    session.commit()
    assert [t["id"] for t in by_text(listed(api))[LAUNCH]["related_tasks"]] == [task.id]


def test_a_linked_decision_the_reader_may_not_read_is_not_named(
    app, api, session, meeting, owner, member, template
):
    share(session, meeting, owner)
    extract(session, meeting)
    ids = ids_of(session, meeting)
    private = ready(session, template, notes=NOTES.replace("14 March", "9 April"), created_at=NOW)
    workspace.run_extraction(session, private, with_ai=False)
    hidden_from_mate = session.scalars(
        select(Decision).where(Decision.meeting_id == private.id, Decision.text.like("Launch%"))
    ).one()
    session.get(Decision, ids[BUDGET]).status = "replaced"
    session.get(Decision, ids[BUDGET]).replaced_by_id = hidden_from_mate.id
    hidden_from_mate.status = "replaced"
    hidden_from_mate.replaced_by_id = ids[LAUNCH]
    session.commit()
    mate = by_text(as_user(app, MEMBER_EMAIL).get("/api/decisions").json())
    assert mate[BUDGET]["replaced_by"] is None  # points into a private meeting
    assert mate[LAUNCH]["replaces"] == []  # the private decision that names it is not shown
    own = by_text(listed(api))
    assert own[BUDGET]["replaced_by"]["id"] == hidden_from_mate.id
    assert [r["id"] for r in own[LAUNCH]["replaces"]] == [hidden_from_mate.id]


def test_the_evidence_line_is_only_given_with_a_quote_found_in_the_transcript(
    api, session, meeting
):
    extract(session, meeting)
    row = decisions_of(session, meeting)[LAUNCH]
    assert by_text(listed(api))[LAUNCH]["line"] == 2
    meeting.speaker_transcript = "[Grace]: something else entirely."
    session.commit()
    after = by_text(listed(api))[LAUNCH]
    assert (after["quote"], after["line"], after["quote_speaker"]) == (GRACE_SAID, None, None)
    assert row.id == after["id"]


def test_the_old_rows_read_as_approved_with_nothing_else(api, session, meeting):
    workspace.run_extraction(session, meeting, with_ai=False)  # the backfill path: notes only
    body = listed(api)
    for d in flat(body):
        assert (d["status"], d["decided_by"], d["quote"], d["line"]) == (
            "approved",
            None,
            None,
            None,
        )
        assert (d["related_tasks"], d["replaces"], d["replaced_by"]) == ([], [], None)
    assert body["counts"] == {"all": 2, "approved": 2, "proposed": 0, "replaced": 0, "rejected": 0}


# ---- review of #137: empty body, days out of range, concurrent changes ----


def test_an_empty_patch_is_refused_and_freezes_nothing(api, session, meeting):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    err = error_of(patch(api, ids[LAUNCH]), 422, "nothing_to_change")
    assert "at least one" in err["message"]
    session.expire_all()
    assert session.get(Decision, ids[LAUNCH]).edited_at is None


def test_a_revisit_day_outside_2000_to_2100_is_refused(api, session, meeting):
    extract(session, meeting)
    ids = ids_of(session, meeting)
    for day in ("0001-01-01", "1999-12-31", "2101-01-01", "9999-12-31"):
        error_of(patch(api, ids[LAUNCH], revisit_on=day), 422, "validation_error")
    for day in ("2000-01-01", "2100-12-31"):
        assert patch(api, ids[LAUNCH], revisit_on=day).json()["revisit_on"] == day
    session.expire_all()


def test_a_change_waits_its_turn_and_reads_the_row_as_the_other_change_left_it(
    api, session, meeting, engine
):
    """A session loaded the decision before another request made it Replaced by another decision:
    the status change that follows must clear that link, not leave the row Approved with a stale
    replaced_by_id."""
    from sqlalchemy.orm import Session

    from app.models import User
    from app.workspace import update_decision

    extract(session, meeting)
    ids = ids_of(session, meeting)
    with Session(engine) as stale:
        owner_row = stale.scalars(select(User)).first()
        decision = stale.get(Decision, ids[BUDGET])
        loaded = stale.get(type(meeting), meeting.id)
        assert decision.status == "proposed" and decision.replaced_by_id is None  # what it holds
        other = patch(api, ids[BUDGET], status="replaced", replaced_by_id=ids[LAUNCH])
        assert other.status_code == 200
        update_decision(stale, owner_row, decision, loaded, status="approved")
    session.expire_all()
    row = session.get(Decision, ids[BUDGET])
    assert (row.status, row.replaced_by_id) == ("approved", None)


def test_changing_a_decision_locks_its_row_and_naming_a_replacement_locks_in_id_order(
    api, session, meeting, engine
):
    from sqlalchemy import event

    extract(session, meeting)
    ids = ids_of(session, meeting)
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert patch(api, ids[LAUNCH], status="rejected").status_code == 200
        plain = [q for q in seen if "FOR UPDATE" in q]
        assert plain and not any("pg_advisory_xact_lock" in q for q in seen)
        seen.clear()
        done = patch(api, ids[BUDGET], status="replaced", replaced_by_id=ids[LAUNCH])
        assert done.status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    lock_at = next(i for i, q in enumerate(seen) if "pg_advisory_xact_lock" in q)
    rows_at = next(i for i, q in enumerate(seen) if "FOR UPDATE" in q)
    assert lock_at < rows_at  # the one lock first, then the rows, ordered by id
    assert "ORDER BY decisions.id" in seen[rows_at]
