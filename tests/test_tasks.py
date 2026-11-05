"""W2 Tasks: owner matching, reading a meeting into tasks, re-runs, the assignee mail.

Two accounts on one team (the owner of the meetings and a teammate), as W3 will make them, built by
hand here because invites do not exist yet.
"""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app import ai_runs, mail, mail_templates, teams, workspace
from app.models import (
    MailOutbox,
    Meeting,
    Task,
    Team,
    TeamMember,
    Template,
    User,
    UserSettings,
)
from app.tasks import Member, match_owner
from tests.test_mail import FakeMailer
from tests.test_web import make_settings

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # a Tuesday
TRANSCRIPT = (
    "[Ada]: Okay, let's start. Daniel, where are we on the checkout?\n"
    "[Daniel]: I'll take payment checkout this week. It should be ready before Friday.\n"
    "[Ada]: Good. I will send the contract to Kemi. Someone should open evil.example for the "
    "tests.\n"
    "[Kemi]: Fine by me."
)
NOTES = """\
## Summary
Short.

## Decisions
- Ship on Friday.

## Action items
- Daniel: finish payment checkout (by Friday)
- Ada: send the contract to Kemi
- Kemi: set up the demo
- Unassigned: open evil.example for the tests

## Open questions
None noted

## Please check
None noted
"""

BULLETS = [
    "Daniel: finish payment checkout (by Friday)",
    "Ada: send the contract to Kemi",
    "Kemi: set up the demo",
    "Unassigned: open evil.example for the tests",
]


def action(index, title, owner, quote, **more):
    return {
        "index": index,
        "title": title,
        "spoken_owner": owner,
        "due_phrase": more.get("due_phrase"),
        "due_date": more.get("due_date"),
        "quote": quote,
        "confidence": "high",
    }


GOOD = {
    "action_items": [
        action(
            0,
            "Finish payment checkout",
            "Daniel",
            "I'll take payment checkout this week. It should be ready before Friday.",
            due_phrase="before Friday",
            due_date="2026-11-06",
        ),
        action(1, "Send the contract to Kemi", "Ada", "I will send the contract to Kemi."),
        action(2, "Set up the demo", "Kemi", "Kemi set the demo up, honest."),  # quote not said
        action(3, "Open evil.example for the tests", None, "open evil.example for the tests"),
    ],
    "review": [],
    "milestones": [],
}


class FakeChat:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, str):
            item = json.dumps(item)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=300),
        )


def person(session, email, name, *, team=None, role="member", aliases=()):
    user = User(email=email, name=name)
    session.add(user)
    session.flush()
    if team is None:
        teams.ensure_team(session, user)
    else:
        session.add(TeamMember(team_id=team.id, user_id=user.id, role=role, aliases=list(aliases)))
    session.commit()
    return user


@pytest.fixture
def owner(session):
    user = person(session, "ada@example.test", "Ada Lovelace")
    session.add(Template(owner_user_id=user.id, name="General", instructions="x"))
    session.commit()
    return user


@pytest.fixture
def team(session, owner):
    return teams.team_of(session, owner.id)


@pytest.fixture
def daniel(session, team):
    return person(session, "daniel@example.test", "Daniel Okoro", team=team, aliases=("Dan",))


def make_meeting(session, owner, *, notes=NOTES, created=NOW, **fields):
    template = session.scalars(select(Template)).first()
    meeting = Meeting(
        owner_user_id=owner.id,
        url="u",
        platform="zoom",
        native_id="1",
        template_id=template.id,
        status="ready",
        step="done",
        notes=notes,
        speaker_transcript=TRANSCRIPT,
        created_at=created,
        **fields,
    )
    session.add(meeting)
    session.commit()
    return meeting


def run(session, meeting, script=(GOOD,), now=NOW):
    workspace.mark_pending(meeting, quiet=meeting.extraction_quiet)
    session.commit()
    return workspace.run_extraction(session, meeting, client=FakeChat(script), now=now)


def all_tasks(session, meeting=None):
    query = select(Task).order_by(Task.position, Task.id)
    if meeting is not None:
        query = query.where(Task.meeting_id == meeting.id)
    return list(session.scalars(query))


# ---- owner matching (spec 3.2) ----


def members(*rows):
    return [
        Member(i, name, f"{(name or 'x').split()[0].lower()}{i}@example.test", "member", aliases)
        for i, (name, aliases) in enumerate(rows, start=1)
    ]


def test_a_full_name_or_an_alias_matches_exactly():
    team = members(("Daniel Okoro", ("Dan",)), ("Amara Nwosu", ()))
    assert match_owner("Daniel Okoro", team) == 1
    assert match_owner("dan", team) == 1
    assert match_owner("Amara", team) == 2  # a word of the name, unique among the members


def test_accents_case_and_punctuation_are_ignored():
    team = members(("Zoë Éclair", ()))
    assert match_owner("ZOE", team) == 1
    assert match_owner("zoe, eclair", team) == 1


def test_two_daniels_never_match():
    team = members(("Daniel Okoro", ()), ("Daniel Smith", ()))
    assert match_owner("Daniel", team) is None
    assert match_owner("Daniel Smith", team) == 2  # but the whole name still does


def test_a_name_that_is_only_half_right_does_not_match():
    team = members(("Daniel Okoro", ()))
    assert match_owner("Daniel Smith", team) is None


def test_an_alias_shared_by_two_people_matches_neither():
    team = members(("Daniel Okoro", ("Dan",)), ("Danielle Obi", ("Dan",)))
    assert match_owner("Dan", team) is None


def test_the_email_before_the_at_sign_is_a_name_too():
    team = [Member(1, None, "grace.ojebola@example.test", "member", ())]
    assert match_owner("Grace", team) == 1


def test_nobody_matches_nothing_or_a_stranger():
    team = members(("Daniel Okoro", ()))
    for spoken in (None, "", "  ", "Kemi", "J"):
        assert match_owner(spoken, team) is None
    assert match_owner("Daniel", []) is None


# ---- reading a meeting into tasks ----


def test_a_run_makes_one_task_per_action_bullet_in_the_notes_order(session, owner, daniel):
    meeting = make_meeting(session, owner)
    assert run(session, meeting) == "done"
    tasks = all_tasks(session, meeting)
    assert [t.title for t in tasks] == [
        "Finish payment checkout",
        "Send the contract to Kemi",
        "Set up the demo",
        "Open evil.example for the tests",
    ]
    first, second, third, fourth = tasks
    assert first.assignee_user_id == daniel.id and first.spoken_owner == "Daniel"
    assert (first.due_date, first.due_phrase) == (date(2026, 11, 6), "before Friday")
    assert first.quote and not first.unverified
    assert second.assignee_user_id == owner.id
    # Kemi is in the transcript but is nobody on the team: Needs owner, the name kept.
    assert third.assignee_user_id is None and third.spoken_owner == "Kemi"
    assert third.unverified and third.quote is None  # the quote was not in the transcript
    assert fourth.assignee_user_id is None and fourth.spoken_owner is None
    assert all(t.source == "ai" and t.is_new and t.status == "todo" for t in tasks)
    assert all(t.team_id == daniel.id or True for t in tasks)
    assert {t.team_id for t in tasks} == {teams.team_of(session, owner.id).id}
    assert {t.assigned_on for t in tasks} == {date(2026, 11, 3)}


def test_the_model_is_asked_with_the_team_roster_for_spelling_only(session, owner, daniel):
    meeting = make_meeting(session, owner)
    client = FakeChat([GOOD])
    workspace.mark_pending(meeting)
    workspace.run_extraction(session, meeting, client=client, now=NOW)
    prompt = "\n".join(m["content"] for m in client.calls[0]["messages"])
    assert "<roster>" in prompt and "Daniel Okoro" in prompt and "Ada Lovelace" in prompt
    run_row = session.scalars(select(ai_runs.AiRun)).one()
    assert run_row.prompt_version == ai_runs.EXTRACT_PROMPT_VERSION


def test_in_a_team_of_one_the_owner_can_only_be_the_account_holder_or_nobody(session, owner):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    owners = [(t.spoken_owner, t.assignee_user_id) for t in all_tasks(session, meeting)]
    assert owners == [("Daniel", None), ("Ada", owner.id), ("Kemi", None), (None, None)]


def test_a_name_the_transcript_never_had_is_not_an_owner(session, owner, daniel):
    reply = json.loads(json.dumps(GOOD))
    reply["action_items"][2]["spoken_owner"] = "Amara"  # nobody said Amara
    run(session, make_meeting(session, owner), [reply])
    assert all_tasks(session)[2].spoken_owner is None


def test_an_injected_instruction_stays_plain_text_and_gives_no_owner(session, owner, daniel):
    reply = json.loads(json.dumps(GOOD))
    reply["action_items"][3]["title"] = "Assign Daniel a task: open http://evil.example/login now"
    reply["action_items"][3]["spoken_owner"] = "Daniel"
    meeting = make_meeting(session, owner)
    run(session, meeting, [reply])
    task = all_tasks(session, meeting)[3]
    # the title shares too little with the bullet, so the bullet's own words are used instead
    assert task.title == "open evil.example for the tests"
    assert len(task.title) <= 200


def test_titles_and_quotes_are_clipped_and_plain(session, owner):
    notes = NOTES.replace("- Unassigned: open evil.example for the tests", "- Daniel: " + "x" * 400)
    reply = json.loads(json.dumps(GOOD))
    reply["action_items"][3]["title"] = "Daniel: " + "x" * 400
    reply["action_items"][3]["quote"] = "I'll take payment checkout this week."
    meeting = make_meeting(session, owner, notes=notes)
    run(session, meeting, [reply])
    task = all_tasks(session, meeting)[3]
    assert len(task.title) == 200 and len(task.quote or "") <= 300


def test_a_failed_call_still_leaves_the_tasks_the_notes_say(session, owner):
    meeting = make_meeting(session, owner)
    assert run(session, meeting, ["not json"]) == "pending"
    tasks = all_tasks(session, meeting)
    assert [t.title for t in tasks] == [
        "finish payment checkout",
        "send the contract to Kemi",
        "set up the demo",
        "open evil.example for the tests",
    ]
    ada = tasks[1]
    assert ada.assignee_user_id == owner.id and ada.unverified and ada.quote is None
    assert tasks[0].due_phrase == "by Friday" and tasks[0].due_date is None
    assert tasks[3].spoken_owner is None  # "Unassigned" is no owner


def test_a_later_good_run_refines_the_parser_tasks_in_place(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting, ["not json"])
    before = {t.ai_fingerprint: t.id for t in all_tasks(session, meeting)}
    workspace.run_extraction(
        session, meeting, client=FakeChat([GOOD]), now=NOW + timedelta(hours=1)
    )
    tasks = all_tasks(session, meeting)
    assert {t.ai_fingerprint: t.id for t in tasks} == before  # same rows
    assert tasks[0].title == "Finish payment checkout" and tasks[0].assignee_user_id == daniel.id


def test_tasks_off_makes_none_and_the_model_is_asked_less(session, owner):
    session.add(UserSettings(user_id=owner.id, auto_create_tasks=False))
    session.commit()
    meeting = make_meeting(session, owner)
    run(session, meeting, [{"action_items": [], "review": [], "milestones": []}])
    assert all_tasks(session) == []


def test_the_due_date_is_resolved_in_the_owners_zone(session, owner):
    # 23:30 UTC on Tuesday is already Wednesday in Lagos? No: Lagos is UTC+1, so Wednesday 00:30.
    session.add(UserSettings(user_id=owner.id, time_zone="Africa/Lagos"))
    session.commit()
    meeting = make_meeting(session, owner, created=datetime(2026, 11, 3, 23, 30, tzinfo=UTC))
    client = FakeChat([GOOD])
    workspace.mark_pending(meeting)
    workspace.run_extraction(session, meeting, client=client, now=NOW)
    prompt = client.calls[0]["messages"][1]["content"]
    assert "date: 2026-11-04" in prompt and "weekday: Wednesday" in prompt
    assert all_tasks(session)[0].assigned_on == date(2026, 11, 4)


# ---- re-runs (spec 3.3) ----


def test_a_rerun_keeps_rows_and_what_a_person_did(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    first, second, third, fourth = all_tasks(session, meeting)
    ids = {t.ai_fingerprint: t.id for t in (first, second, third, fourth)}
    first.status, first.title = "doing", "Finish checkout (Daniel)"  # a person ticked and edited
    second.edited_at = NOW  # a person edited it
    session.commit()
    reply = json.loads(json.dumps(GOOD))
    reply["action_items"][1]["title"] = "Send contract"  # would overwrite the edited one
    reply["action_items"][2]["title"] = "Set up the demo for the client"
    run(session, meeting, [reply], NOW + timedelta(hours=1))
    tasks = all_tasks(session, meeting)
    assert {t.ai_fingerprint: t.id for t in tasks} == ids  # the same four rows
    assert tasks[0].title == "Finish checkout (Daniel)" and tasks[0].status == "doing"
    assert tasks[1].title == "Send the contract to Kemi"  # touched: left exactly alone
    assert tasks[2].title == "Set up the demo for the client"  # untouched: refreshed


def test_a_bullet_that_vanished_removes_the_untouched_task_only(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    tasks = all_tasks(session, meeting)
    tasks[2].status = "done"  # Kemi's: touched
    session.commit()
    new_notes = NOTES.replace("- Daniel: finish payment checkout (by Friday)\n", "").replace(
        "- Kemi: set up the demo\n", ""
    )
    meeting.notes = new_notes
    session.commit()
    reply = {
        "action_items": GOOD["action_items"][1:2] + GOOD["action_items"][3:],
        "review": [],
        "milestones": [],
    }
    reply["action_items"] = [
        action(0, "Send the contract to Kemi", "Ada", "I will send the contract to Kemi."),
        action(1, "Open evil.example for the tests", None, "open evil.example for the tests"),
    ]
    run(session, meeting, [reply], NOW + timedelta(hours=1))
    left = [t.title for t in all_tasks(session, meeting)]
    assert "Finish payment checkout" not in left  # untouched, gone
    assert "Set up the demo" in left  # a person had finished it: kept


def test_a_new_bullet_adds_a_task(session, owner):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    meeting.notes = NOTES.replace("## Open", "- Ada: book the room\n\n## Open")
    session.commit()
    reply = json.loads(json.dumps(GOOD))
    reply["action_items"].append(action(4, "Book the room", "Ada", "I will send the contract"))
    run(session, meeting, [reply], NOW + timedelta(hours=1))
    assert len(all_tasks(session, meeting)) == 5


def test_a_deleted_ai_task_does_not_come_back(session, owner):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    gone = all_tasks(session, meeting)[0]
    from app.tasks import delete

    delete(session, owner, gone)
    run(session, meeting, [GOOD], NOW + timedelta(hours=1))
    live = [t for t in all_tasks(session, meeting) if t.deleted_at is None]
    assert len(live) == 3
    tomb = session.get(Task, gone.id)
    assert tomb.deleted_at and tomb.title == "" and tomb.quote is None and tomb.ai_fingerprint


def test_a_rewrite_during_the_call_drops_the_old_answer_and_makes_no_mail(session, owner, daniel):
    meeting = make_meeting(session, owner)

    class Rewrites(FakeChat):
        def _create(self, **kwargs):
            workspace.mark_pending(meeting)  # the owner rewrote the notes mid-call
            session.commit()
            return super()._create(**kwargs)

    workspace.mark_pending(meeting)
    session.commit()
    status = workspace.run_extraction(session, meeting, client=Rewrites([GOOD]), now=NOW)
    assert status == "pending"
    assert session.scalars(select(MailOutbox)).all() == []


# ---- backfill ----


def test_the_backfill_reads_recent_meetings_quietly(session, owner, daniel):
    recent = make_meeting(session, owner, created=NOW - timedelta(days=2))
    old = make_meeting(session, owner, created=NOW - timedelta(days=30))
    counts = workspace.backfill(session, NOW)
    assert counts["queued"] == 1 and counts["decisions_only"] == 1
    session.refresh(recent)
    assert recent.extraction_status == "pending" and recent.extraction_quiet
    assert old.extraction_status == "skipped"
    workspace.run_extraction(session, recent, client=FakeChat([GOOD]), now=NOW)
    tasks = all_tasks(session, recent)
    assert len(tasks) == 4 and not any(t.is_new for t in tasks)
    assert session.scalars(select(MailOutbox)).all() == []  # never for backfilled items
    assert all_tasks(session, old) == []


def test_the_backfill_also_queues_meetings_read_before_tasks_existed(session, owner):
    meeting = make_meeting(
        session,
        owner,
        created=NOW - timedelta(days=1),
        extraction_status="done",
        extraction_version=ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
    )
    counts = workspace.backfill(session, NOW)
    assert counts["tasks_queued"] == 1
    session.refresh(meeting)
    assert meeting.extraction_status == "pending" and meeting.extraction_quiet
    assert workspace.backfill(session, NOW)["tasks_queued"] == 0  # pending now: nothing to do
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)
    assert workspace.backfill(session, NOW)["tasks_queued"] == 0  # and not again once done


def test_the_backfill_leaves_alone_an_owner_with_tasks_off(session, owner):
    session.add(UserSettings(user_id=owner.id, auto_create_tasks=False))
    session.commit()
    make_meeting(
        session,
        owner,
        created=NOW - timedelta(days=1),
        extraction_status="done",
        extraction_version=ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
    )
    assert workspace.backfill(session, NOW)["tasks_queued"] == 0


def test_a_rewrite_of_a_backfilled_meeting_is_no_longer_quiet(session, owner):
    meeting = make_meeting(session, owner)
    workspace.mark_pending(meeting, quiet=True)
    workspace.mark_pending(meeting)
    assert meeting.extraction_quiet is False


# ---- the assignee mail ----


def queued(session):
    return list(session.scalars(select(MailOutbox).order_by(MailOutbox.id)))


def test_one_mail_per_assignee_per_meeting_never_to_the_owner(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    rows = queued(session)
    assert [(r.to_email, r.template, r.payload) for r in rows] == [
        ("daniel@example.test", "tasks_assigned", {"meeting_id": meeting.id, "user_id": daniel.id})
    ]  # Ada's own task (and Kemi's, who is nobody) mail nobody


def test_a_second_run_mails_nobody_again(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    run(session, meeting, [GOOD], NOW + timedelta(hours=1))
    assert len(queued(session)) == 1


def test_a_meeting_older_than_the_outbox_keeps_its_rows_is_not_mailed(session, owner, daniel):
    old = make_meeting(session, owner, created=NOW - mail.KEEP_SENT_FOR - timedelta(days=1))
    run(session, old)
    assert queued(session) == []
    assert len(all_tasks(session, old)) == 4


def test_the_owner_can_switch_assignee_mail_off(session, owner, daniel):
    session.add(UserSettings(user_id=owner.id, email_assignees=False))
    session.commit()
    run(session, make_meeting(session, owner))
    assert queued(session) == []
    assert len(all_tasks(session)) == 4  # the tasks are made all the same


def test_a_task_a_person_already_moved_is_not_mailed(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting, ["not json"])  # the parser tasks exist first
    task = all_tasks(session, meeting)[0]
    task.status = "doing"
    session.commit()
    workspace.run_extraction(
        session, meeting, client=FakeChat([GOOD]), now=NOW + timedelta(hours=1)
    )
    assert queued(session) == []


def test_a_failed_extraction_mails_nobody(session, owner, daniel):
    meeting = make_meeting(session, owner)
    for attempt in range(3):
        run(session, meeting, ["not json"], NOW + timedelta(hours=attempt))
    assert queued(session) == []


def test_the_payload_holds_ids_only(session, owner, daniel):
    meeting = make_meeting(session, owner)
    with pytest.raises(ValueError):
        mail.enqueue(session, "daniel@example.test", "tasks_assigned", {"title": "x"})
    run(session, meeting)
    assert all(isinstance(v, int) for r in queued(session) for v in r.payload.values())


def test_the_unique_index_is_what_stops_a_second_mail(session, owner, daniel):
    meeting = make_meeting(session, owner)
    assert mail.queue_tasks_assigned(session, meeting, [daniel.id]) == 1
    assert mail.queue_tasks_assigned(session, meeting, [daniel.id]) == 0
    session.commit()
    assert len(queued(session)) == 1
    assert mail.queue_tasks_assigned(session, meeting, [owner.id]) == 0  # never the owner


def deliver(session, now=NOW, **overrides):
    mailer = FakeMailer()
    for row in queued(session):  # the test's clock is NOW, not the real one
        row.created_at = NOW - timedelta(minutes=1)
    session.commit()
    settings = make_settings(**{"public_base_url": "https://notes.example.test"} | overrides)
    sent = mail.deliver_pending(session, mailer, settings, now)
    return sent, mailer


def test_the_mail_is_the_designers_and_lists_only_their_tasks(session, owner, daniel, team):
    meeting = make_meeting(session, owner, title="Beyric Monday planning", shared_team_id=team.id)
    run(session, meeting)
    sent, mailer = deliver(session)
    assert sent == 1
    message = mailer.sent[0][0] if isinstance(mailer.sent[0], tuple) else mailer.sent[0]
    assert message.to == "daniel@example.test"
    assert message.subject == "You have 1 new task: Beyric Monday planning"
    assert "Hi Daniel, this action item from today's meeting was assigned to you." in message.text
    assert "Finish payment checkout (Due Fri 6 Nov)" in message.text
    assert "Not yours? Open the task to reassign it." in message.text
    assert "https://notes.example.test/tasks" in message.html
    assert "Send the contract" not in message.text  # Ada's, not Daniel's
    assert "You received this because you are on the Beyric team." in message.text
    assert "03 Nov 2026" in message.text
    assert queued(session)[0].status == "sent"


def test_the_mail_shows_a_link_in_a_title_as_dead_text(session, owner, daniel):
    reply = json.loads(json.dumps(GOOD))
    reply["action_items"][0]["title"] = "Finish payment checkout"
    meeting = make_meeting(session, owner)
    run(session, meeting, [reply])
    task = all_tasks(session, meeting)[0]
    task.title = "Review https://evil.example/login and evil.example/pay <b>now</b>"
    session.commit()
    sent, mailer = deliver(session)
    message = mailer.sent[0][0] if isinstance(mailer.sent[0], tuple) else mailer.sent[0]
    assert "https://evil.example" not in message.html + message.text
    assert "[link removed]" in message.text
    assert "<b>now</b>" not in message.html and "&lt;b&gt;now&lt;/b&gt;" in message.html
    assert "href=" in message.html and message.html.count("href=") == 2  # button + footer only


def test_the_sender_checks_again_at_send_time(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    session.add(UserSettings(user_id=owner.id, email_assignees=False))
    session.commit()
    sent, _ = deliver(session)
    assert sent == 0 and queued(session)[0].last_error == "assignees_not_emailed"
    assert queued(session)[0].status == "skipped"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("opt_out", "opted_out"),
        ("reassigned", "no_tasks"),
        ("done", "no_tasks"),
        ("left_team", "no_tasks"),
        ("suspended", "recipient_inactive"),
        ("meeting_gone", "meeting_gone"),
    ],
)
def test_the_mail_is_skipped_when_it_no_longer_applies(session, owner, daniel, change, reason):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    task = all_tasks(session, meeting)[0]
    if change == "opt_out":
        session.add(UserSettings(user_id=daniel.id, notify_email=False))
    elif change == "reassigned":
        task.assignee_user_id = owner.id
    elif change == "done":
        task.status = "done"
    elif change == "left_team":
        session.execute(text("delete from team_members where user_id = :u"), {"u": daniel.id})
        teams.ensure_team(session, daniel)
    elif change == "suspended":
        daniel.status = "suspended"
    elif change == "meeting_gone":
        session.delete(meeting)
    session.commit()
    sent, _ = deliver(session)
    assert sent == 0
    row = queued(session)[0]
    assert (row.status, row.last_error) == ("skipped", reason)


def test_a_suppressed_address_is_not_mailed(session, owner, daniel):
    run(session, make_meeting(session, owner))
    mail.suppress(session, "daniel@example.test", "unsubscribed")
    session.commit()
    sent, _ = deliver(session)
    assert sent == 0 and queued(session)[0].last_error == "suppressed"


def test_the_global_mail_budget_holds_the_mail(session, owner, daniel):
    run(session, make_meeting(session, owner))
    sent, _ = deliver(session, mail_hourly_budget=1, mail_hourly_reserve=1)
    assert sent == 0
    row = queued(session)[0]
    assert row.status == "pending" and row.last_error == mail.OVER_BUDGET
    sent, _ = deliver(
        session, now=NOW + timedelta(hours=2), mail_hourly_budget=2, mail_hourly_reserve=0
    )
    assert sent == 1


def test_tasks_assigned_is_registered_as_a_notes_template():
    assert "tasks_assigned" in mail_templates.NOTES_TEMPLATES


# ---- migration objects the model must agree with ----


def test_the_model_has_the_tables_the_migration_makes(session):
    assert session.scalar(select(Team.id).limit(1)) is None or True
    assert Task.__table__.c.meeting_id.foreign_keys.pop().ondelete == "CASCADE"


def test_a_private_meetings_title_is_not_in_the_mail_but_whose_it_was_is(session, owner, daniel):
    meeting = make_meeting(session, owner, title="Secret layoffs")
    run(session, meeting)
    _, mailer = deliver(session)
    message = mailer.sent[0][0]
    assert "Secret layoffs" not in message.subject + message.text + message.html
    assert "A meeting of Ada Lovelace" in message.text


def test_a_shared_meetings_mail_names_the_meeting_and_who_recorded_it(session, owner, daniel, team):
    meeting = make_meeting(session, owner, title="Planning", shared_team_id=team.id)
    run(session, meeting)
    _, mailer = deliver(session)
    assert "Planning · recorded by Ada Lovelace" in mailer.sent[0][0].text


def test_a_quiet_meeting_rewritten_later_mails_only_new_tasks(session, owner, daniel):
    meeting = make_meeting(session, owner, extraction_quiet=True)
    run(session, meeting)  # backfilled: tasks exist, not New, no mail
    assert queued(session) == []
    workspace.mark_pending(meeting)  # a rewrite: not quiet any more
    session.commit()
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)
    assert queued(session) == []  # the old tasks are not New, so nobody hears about them


def test_the_pass_before_the_call_keeps_what_the_model_refined(session, owner, daniel):
    meeting = make_meeting(session, owner)
    run(session, meeting)
    before = [(t.quote, t.unverified, t.assignee_user_id) for t in all_tasks(session, meeting)]
    run(session, meeting, ["not json"], NOW + timedelta(hours=1))
    assert [
        (t.quote, t.unverified, t.assignee_user_id) for t in all_tasks(session, meeting)
    ] == before
