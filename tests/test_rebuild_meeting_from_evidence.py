"""`scripts/rebuild_meeting_from_evidence.py`: rebuild a finished meeting's transcript and notes
from the Skribby segments kept in `skribby_segment_evidence`, as the live `skribby` path does.

Every fixture is made up (invented names, invented words). Nothing here is, or is copied from, a
real meeting.
"""

import json
import os
import stat
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import skribby, transcript_source
from app.config import Settings
from app.models import (
    AiRun,
    Decision,
    MailOutbox,
    Meeting,
    OpenQuestion,
    ReviewItem,
    SkribbySegmentEvidence,
    Task,
    Team,
    Template,
    UsageEntry,
    VocabularyTerm,
)
from app.notes import HEADINGS, NotesFailed
from scripts import rebuild_meeting_from_evidence as rebuild
from tests.test_pipeline import FakeOpenAI
from tests.test_web import owner  # noqa: F401  (fixture used by name)
from tests.test_workspace import GOOD, FakeChat

NOW = datetime.now(UTC)
SECONDS = 60
BOT = "Beyric Notes"


def notes(*, decisions=(), actions=(), questions=(), check=()):
    def section(name, bullets):
        body = "\n".join(f"- {b}" for b in bullets) or "None noted"
        return f"## {name}\n{body}"

    given = {"Decisions": decisions, "Action items": actions}
    given |= {"Open questions": questions, "Please check": check}
    return "\n\n".join(section(h, given.get(h, ())) for h in HEADINGS) + "\n"


OLD_NOTES = notes(
    decisions=["Old wording of the first decision."],
    actions=["Old wording of the action."],
    questions=["Old wording of the question?"],
    check=["Old wording of the check."],
)
NEW_NOTES = notes(
    decisions=["New wording of the first decision."],
    actions=["New wording of the action."],
    questions=["New wording of the question?"],
    check=["New wording of the check."],
)
OLD_TRANSCRIPT = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"
OLD_LABELLED = (
    "[Ada Example]: alpha bravo charlie delta\n"
    "[Unknown speaker]: echo foxtrot golf hotel\n"
    "[Bo Example]: india juliet kilo lima"
)


def item(tag, text, start, *, name=None, candidates=()):
    return {
        "speaker": tag,
        "speaker_name": name,
        "potential_speaker_names": list(candidates),
        "start": float(start),
        "end": float(start) + 3.0,
        "text": text,
    }


def three_voices():
    """The shape of the real case, made up: two tags Skribby named, one it did not but listed the
    same single person on every segment."""
    rows, at = [], 0
    for round_ in range(4):
        rows.append(item("0", f"red{round_} orchid one two three four", at, name="Ada Example"))
        rows.append(
            item("1", f"blue{round_} violet five six seven eight", at + 3, name="Bo Example")
        )
        rows.append(
            item(
                "2",
                f"green{round_} lilac nine ten eleven twelve",
                at + 6,
                candidates=["Cy Example"],
            )
        )
        at += 12
    return rows


def tags_of(items, *, by_elimination=True):
    body = {
        "transcript": [
            {
                "speaker": i["speaker"],
                "speaker_name": i["speaker_name"],
                "potential_speaker_names": [{"name": n} for n in i["potential_speaker_names"]],
                "transcript": i["text"],
            }
            for i in items
        ]
    }
    return skribby._resolve_tags(body["transcript"], (), by_elimination)


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    fixed = Settings(audio_dir=str(tmp_path / "audio"), skribby_name_by_elimination=True)
    monkeypatch.setattr(rebuild, "get_settings", lambda: fixed)
    return fixed


@pytest.fixture
def backups(tmp_path):
    return tmp_path / "backups"


@pytest.fixture
def template(session, owner):
    row = Template(owner_user_id=owner.id, name="General", instructions="Be brief.")
    session.add(row)
    session.commit()
    return row


def make_meeting(session, owner, template, **fields):
    values = dict(
        owner_user_id=owner.id,
        url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_id="abc-defg-hij",
        template_id=template.id,
        bot_provider="skribby",
        bot_name=BOT,
        status="ready",
        step="done",
        transcript=OLD_TRANSCRIPT,
        speaker_transcript=OLD_LABELLED,
        speakers_incomplete=True,
        transcript_source="own",
        notes=OLD_NOTES,
        recorded_seconds=SECONDS,
        extraction_status="done",
        extraction_version="extract-v3",
    )
    meeting = Meeting(**(values | fields))
    session.add(meeting)
    session.commit()
    return meeting


def keep_evidence(session, meeting, items=None, *, age=timedelta(hours=1), **fields):
    items = three_voices() if items is None else items
    saved = tags_of(items) if items and isinstance(items[0], dict) else {}
    if "2" in saved:  # as saved before the sole-candidate naming: that tag stayed unnamed
        saved["2"] = {"name": "", "how": "unknown", "note": "unresolved"}
    row = SkribbySegmentEvidence(
        meeting_id=meeting.id,
        items=items,
        tags=saved,
        by_elimination=True,
        created_at=NOW - age,
    )
    for name, value in fields.items():
        setattr(row, name, value)
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def meeting(session, owner, template):
    row = make_meeting(session, owner, template)
    keep_evidence(session, row)
    return row


@pytest.fixture(autouse=True)
def fake_database(session, engine, monkeypatch):
    monkeypatch.setattr(rebuild, "SessionLocal", lambda: type(session)(engine))


def go(argv, backups, client=None, capsys=None):
    client = client or FakeOpenAI(notes=NEW_NOTES)
    code = rebuild.run([*argv, "--backup-dir", str(backups)], client=client)
    return code, client, (capsys.readouterr().out if capsys else "")


def stored(session, meeting):
    session.expire_all()
    return session.get(Meeting, meeting.id)


def snapshot(row):
    names = (
        "transcript speaker_transcript speakers_incomplete transcript_source notes status step "
        "recorded_seconds template_id shared_team_id owner_team_id series_id extraction_status "
        "extraction_quiet failure_reason"
    ).split()
    return {name: getattr(row, name) for name in names}


# ---- what it refuses, and what it leaves alone when it does ----


@pytest.mark.parametrize("status", ["bot_sent", "processing", "failed"])
def test_a_meeting_that_is_not_finished_is_refused_and_left_alone(
    session, owner, template, backups, capsys, status
):
    row = make_meeting(session, owner, template, status=status)
    keep_evidence(session, row)
    before = snapshot(row)
    code, client, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code != 0
    assert "not finished" in out or "being recorded or processed" in out
    assert snapshot(stored(session, row)) == before
    assert client.chats == 0
    assert not backups.exists()


def test_an_unknown_meeting_is_refused(session, backups, capsys):
    code, _, out = go(["999999", "--apply"], backups, capsys=capsys)
    assert code != 0 and "no meeting" in out.lower()


def test_a_meeting_whose_lists_are_being_read_right_now_is_refused(
    session, meeting, backups, capsys
):
    meeting.extraction_status = "running"
    session.commit()
    code, client, out = go([str(meeting.id), "--apply"], backups, capsys=capsys)
    assert code != 0 and "being read" in out
    assert client.chats == 0 and stored(session, meeting).transcript == OLD_TRANSCRIPT


def test_a_meeting_a_different_provider_ran_has_no_evidence_to_rebuild_from(
    session, owner, template, backups, capsys
):
    row = make_meeting(session, owner, template, bot_provider="vexa")
    code, _, out = go([str(row.id)], backups, capsys=capsys)
    assert code != 0 and "skribby" in out.lower()


def test_a_meeting_with_no_saved_evidence_is_refused_plainly(
    session, owner, template, backups, capsys
):
    row = make_meeting(session, owner, template)
    code, client, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code != 0
    assert "no saved evidence" in out
    assert client.chats == 0 and stored(session, row).transcript == OLD_TRANSCRIPT


def test_evidence_older_than_its_seven_days_is_refused(session, owner, template, backups, capsys):
    row = make_meeting(session, owner, template)
    keep_evidence(session, row, age=timedelta(days=8))
    code, client, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code != 0 and "expired" in out
    assert client.chats == 0 and stored(session, row).transcript == OLD_TRANSCRIPT


def test_evidence_with_too_little_text_for_the_recording_is_refused(
    session, owner, template, backups, capsys
):
    row = make_meeting(session, owner, template)
    keep_evidence(session, row, [item("0", "hello there", 1, name="Ada Example")])
    code, client, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code != 0 and "too short" in out
    assert client.chats == 0 and stored(session, row).transcript == OLD_TRANSCRIPT


def test_evidence_with_far_too_little_text_for_a_long_recording_is_refused(
    session, owner, template, backups, capsys
):
    row = make_meeting(session, owner, template, recorded_seconds=3600)
    keep_evidence(session, row)
    code, client, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code != 0 and "too short" in out  # 72 words is far too few for an hour
    assert client.chats == 0


def test_evidence_that_stops_well_before_the_end_of_the_recording_is_refused(
    session, owner, template, backups, capsys
):
    row = make_meeting(session, owner, template, recorded_seconds=70)
    keep_evidence(session, row)  # its last segment ends at 45 s: under 70% of 70 s
    code, client, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code != 0 and "stops well before the end of the recording" in out
    assert client.chats == 0 and stored(session, row).transcript == OLD_TRANSCRIPT


def test_unreadable_evidence_is_refused(session, owner, template, backups, capsys):
    row = make_meeting(session, owner, template)
    keep_evidence(session, row, ["not an item"])
    code, _, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code != 0 and "unreadable" in out


# ---- the dry run ----


def test_the_dry_run_changes_nothing_and_asks_the_model_nothing(session, meeting, backups, capsys):
    before = snapshot(meeting)
    runs = session.scalars(select(AiRun)).all()
    code, client, out = go([str(meeting.id)], backups, capsys=capsys)
    assert code == 0
    assert snapshot(stored(session, meeting)) == before
    assert session.scalars(select(AiRun)).all() == runs
    assert client.chats == 0
    assert not backups.exists()
    assert "dry run" in out.lower() and "--apply" in out


def test_the_dry_run_prints_counts_and_no_words_or_names(session, meeting, backups, capsys):
    _, _, out = go([str(meeting.id)], backups, capsys=capsys)
    assert "12 segments" in out  # the evidence's segments
    assert "3 tags" in out
    assert "given" in out and "sole_candidate" in out
    # what the saved record said at the time (the tag was unnamed), against what is read now
    assert "unknown" in out
    assert "speakers named: before 2, after 3" in out
    assert "words: before 12, after" in out
    for private in ("orchid", "violet", "lilac", "Ada", "Bo Example", "Cy Example", "alpha"):
        assert private not in out


def test_the_dry_run_counts_what_a_person_has_touched(session, meeting, owner, backups, capsys):
    session.add(
        Decision(meeting_id=meeting.id, text="d", position=0, fingerprint="a" * 64, hidden_at=NOW)
    )
    session.add(
        ReviewItem(
            meeting_id=meeting.id,
            owner_user_id=owner.id,
            text="r",
            position=0,
            fingerprint="b" * 64,
            checked_at=NOW,
        )
    )
    session.commit()
    _, _, out = go([str(meeting.id)], backups, capsys=capsys)
    assert "touched by a person: 1 decisions, 0 open questions, 1 review items, 0 tasks" in out


# ---- what --apply does ----


def apply(session, meeting, backups, capsys, client=None, extra=()):
    return go([str(meeting.id), "--apply", *extra], backups, client=client, capsys=capsys)


def test_apply_builds_the_transcript_exactly_as_the_live_path_does(
    session, meeting, backups, capsys
):
    body = {
        "transcript": [
            {
                "speaker": i["speaker"],
                "speaker_name": i["speaker_name"],
                "potential_speaker_names": [{"name": n} for n in i["potential_speaker_names"]],
                "start": i["start"],
                "end": i["end"],
                "transcript": i["text"],
            }
            for i in three_voices()
        ]
    }
    segments = skribby._segments(body, (BOT,), True)  # what map_bot hands the pipeline
    expected = transcript_source.candidate(segments, skribby._last_segment_end(body))

    code, _, _ = apply(session, meeting, backups, capsys)

    assert code == 0
    row = stored(session, meeting)
    assert row.transcript == expected.text == row.speaker_transcript
    assert row.speakers_incomplete is False
    assert row.transcript_source == "skribby-rebuilt"
    # the tag Skribby never named is now the person it listed on every one of its segments
    assert "[Cy Example]: green0 lilac" in row.speaker_transcript
    assert "Unknown speaker" not in row.speaker_transcript


def test_apply_rewrites_the_notes_from_the_new_transcript(session, meeting, backups, capsys):
    code, client, _ = apply(session, meeting, backups, capsys)
    assert code == 0 and client.chats == 1
    row = stored(session, meeting)
    assert row.notes == NEW_NOTES
    assert row.template_id == meeting.template_id
    notes_run = session.scalars(select(AiRun).where(AiRun.kind == "notes")).one()
    assert notes_run.meeting_id == meeting.id and notes_run.status == "ok"
    assert notes_run.prompt_version.endswith("+rebuild")  # still a "notes" call for the cost report


def test_apply_asks_for_the_owners_vocabulary_like_the_live_path_does(
    session, meeting, backups, capsys, monkeypatch
):
    session.add(VocabularyTerm(owner_user_id=meeting.owner_user_id, term="Orchid"))
    session.commit()
    seen = []
    monkeypatch.setattr(
        rebuild.pipeline,
        "_apply_vocabulary",
        lambda session, meeting, text, client: seen.append(text) or text,
    )
    apply(session, meeting, backups, capsys)
    assert len(seen) == 1 and "[Cy Example]:" in seen[0]


def test_apply_leaves_hours_usage_sharing_and_status_alone(
    session, meeting, backups, capsys, owner
):
    session.add(
        UsageEntry(user_id=owner.id, email_hash="h" * 64, meeting_id=meeting.id, seconds=SECONDS)
    )
    session.commit()
    before = snapshot(meeting)
    usage = [(u.meeting_id, u.seconds) for u in session.scalars(select(UsageEntry))]
    apply(session, meeting, backups, capsys)
    after = snapshot(stored(session, meeting))
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {
        "transcript",
        "speaker_transcript",
        "speakers_incomplete",
        "transcript_source",
        "notes",
        "extraction_status",
        "extraction_quiet",
    }
    assert after["recorded_seconds"] == SECONDS
    assert after["status"] == "ready" and after["step"] == "done"
    assert [(u.meeting_id, u.seconds) for u in session.scalars(select(UsageEntry))] == usage


def test_apply_sends_no_mail(session, meeting, backups, capsys):
    apply(session, meeting, backups, capsys)
    assert session.scalars(select(MailOutbox)).all() == []


def test_apply_queues_the_lists_to_be_read_again_quietly(session, meeting, backups, capsys):
    apply(session, meeting, backups, capsys)
    row = stored(session, meeting)
    assert (row.extraction_status, row.extraction_quiet) == ("pending", True)


def test_the_quiet_re_read_makes_no_new_badge_no_mail_and_no_second_task(
    session, meeting, backups, capsys, owner
):
    from app import workspace

    # the person already has the task from the old notes, and has not touched it
    workspace.run_extraction(session, stored(session, meeting), client=FakeChat([GOOD]), now=NOW)
    old_tasks = session.scalars(select(Task)).all()
    assert [t.title for t in old_tasks] == ["Old wording of the action."]
    session.execute(Task.__table__.update().values(is_new=False))
    session.commit()

    apply(session, meeting, backups, capsys)
    workspace.run_extraction(session, stored(session, meeting), client=FakeChat([GOOD]), now=NOW)

    tasks = session.scalars(select(Task)).all()
    assert [t.title for t in tasks] == ["New wording of the action."]  # replaced, not added
    assert [t.is_new for t in tasks] == [False]  # not a "New" badge
    assert session.scalars(select(MailOutbox)).all() == []


def test_apply_touches_no_other_meeting(session, owner, template, meeting, backups, capsys):
    other = make_meeting(session, owner, template, native_id="zzz")
    keep_evidence(session, other)
    before = snapshot(other)
    apply(session, meeting, backups, capsys)
    assert snapshot(stored(session, other)) == before


def test_the_evidence_row_itself_is_not_changed(session, meeting, backups, capsys):
    row = session.scalars(select(SkribbySegmentEvidence)).one()
    saved = (row.items, row.tags, row.created_at)
    apply(session, meeting, backups, capsys)
    session.expire_all()
    row = session.scalars(select(SkribbySegmentEvidence)).one()
    assert (row.items, row.tags, row.created_at) == saved


def test_when_the_notes_cannot_be_written_nothing_is_changed(
    session, meeting, backups, capsys, monkeypatch
):
    before = snapshot(meeting)
    runs = session.scalars(select(AiRun)).all()

    def refuse(*args, **kwargs):
        raise NotesFailed("notes: the model returned an empty reply")

    monkeypatch.setattr(rebuild, "generate_notes", refuse)
    code, _, out = apply(session, meeting, backups, capsys)
    assert code != 0 and "notes could not be written" in out.lower()
    assert snapshot(stored(session, meeting)) == before
    assert session.scalars(select(AiRun)).all() == runs


# ---- the backup, and putting things back ----


def backup_files(backups):
    return sorted(backups.glob("meeting-*.json")) if backups.exists() else []


def test_the_old_text_is_saved_before_anything_is_written(
    session, meeting, backups, capsys, monkeypatch
):
    seen = {}
    real = rebuild.generate_notes

    def spy(*args, **kwargs):
        # by the time the model is asked, the backup is on disk and the row is still the old one
        seen["files"] = backup_files(backups)
        seen["transcript"] = stored(session, meeting).transcript
        return real(*args, **kwargs)

    monkeypatch.setattr(rebuild, "generate_notes", spy)
    apply(session, meeting, backups, capsys)
    assert len(seen["files"]) == 1 and seen["transcript"] == OLD_TRANSCRIPT


def test_the_backup_holds_the_old_text_and_is_private(session, meeting, backups, capsys):
    _, _, out = apply(session, meeting, backups, capsys)
    (path,) = backup_files(backups)
    data = json.loads(path.read_text())
    assert data["meeting_id"] == meeting.id
    assert data["transcript"] == OLD_TRANSCRIPT
    assert data["speaker_transcript"] == OLD_LABELLED
    assert data["notes"] == OLD_NOTES
    assert data["transcript_source"] == "own" and data["speakers_incomplete"] is True
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(backups).st_mode) == 0o700
    assert str(path) in out
    assert "private" in out.lower()
    assert "alpha" not in out  # the path is printed, never the words


def test_nothing_is_written_when_the_backup_cannot_be_saved(session, meeting, tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the folder should be")
    before = snapshot(meeting)
    code, client, out = go([str(meeting.id), "--apply"], blocker / "inside", capsys=capsys)
    assert code != 0 and "backup" in out.lower()
    assert client.chats == 0
    assert snapshot(stored(session, meeting)) == before


def test_restore_puts_the_old_text_and_notes_back(session, meeting, backups, capsys):
    before = snapshot(meeting)
    apply(session, meeting, backups, capsys)
    (path,) = backup_files(backups)
    code = rebuild.run(["--restore", str(path), "--apply"])
    assert code == 0
    session.expire_all()
    row = session.get(Meeting, meeting.id)
    for name in (
        "transcript",
        "speaker_transcript",
        "speakers_incomplete",
        "transcript_source",
        "notes",
    ):
        assert getattr(row, name) == before[name]
    assert (row.extraction_status, row.extraction_quiet) == ("pending", True)
    assert row.status == "ready" and row.recorded_seconds == SECONDS


def test_restore_without_apply_only_says_what_it_would_do(session, meeting, backups, capsys):
    apply(session, meeting, backups, capsys)
    (path,) = backup_files(backups)
    after_rebuild = snapshot(stored(session, meeting))
    capsys.readouterr()
    assert rebuild.run(["--restore", str(path)]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert snapshot(stored(session, meeting)) == after_rebuild


def test_restore_then_rebuild_again_works(session, meeting, backups, capsys):
    apply(session, meeting, backups, capsys)
    (path,) = backup_files(backups)
    rebuild.run(["--restore", str(path), "--apply"])
    code, _, out = apply(session, meeting, backups, capsys)
    assert code == 0, out
    assert stored(session, meeting).transcript_source == "skribby-rebuilt"


def test_restore_refuses_a_meeting_that_is_not_the_rebuilt_one_unless_forced(
    session, meeting, backups, capsys
):
    apply(session, meeting, backups, capsys)
    (path,) = backup_files(backups)
    row = stored(session, meeting)
    row.transcript_source = "own"  # someone changed it since
    row.notes = "written by a person since"
    session.commit()
    code = rebuild.run(["--restore", str(path), "--apply"])
    assert code != 0 and "changed since" in capsys.readouterr().out
    assert stored(session, meeting).notes == "written by a person since"
    assert rebuild.run(["--restore", str(path), "--force", "--apply"]) == 0
    assert stored(session, meeting).notes == OLD_NOTES


def test_restore_changes_only_the_meeting_in_the_file(
    session, owner, template, meeting, backups, capsys
):
    other = make_meeting(session, owner, template, native_id="zzz")
    keep_evidence(session, other)
    before = snapshot(other)
    apply(session, meeting, backups, capsys)
    (path,) = backup_files(backups)
    rebuild.run(["--restore", str(path), "--apply"])
    assert snapshot(stored(session, other)) == before


def test_restore_stops_when_the_old_notes_would_remove_something_a_person_touched(
    session, meeting, backups, capsys
):
    apply(session, meeting, backups, capsys)
    (path,) = backup_files(backups)
    session.add(touched_decision(meeting, "New wording of the first decision."))
    session.commit()  # touched since the rebuild; the old notes do not hold that wording
    capsys.readouterr()
    code = rebuild.run(["--restore", str(path), "--apply"])
    assert code != 0 and "1 decisions" in capsys.readouterr().out
    assert stored(session, meeting).notes == NEW_NOTES
    assert rebuild.run(["--restore", str(path), "--apply", "--force"]) == 0
    assert stored(session, meeting).notes == OLD_NOTES


def test_restore_refuses_a_file_that_is_not_one_of_ours(session, meeting, tmp_path, capsys):
    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"hello": "world"}))
    assert rebuild.run(["--restore", str(junk), "--apply"]) != 0
    assert rebuild.run(["--restore", str(tmp_path / "missing.json"), "--apply"]) != 0
    assert stored(session, meeting).transcript == OLD_TRANSCRIPT


# ---- running it twice, and the other paths ----


def test_running_it_twice_changes_nothing_the_second_time(session, meeting, backups, capsys):
    apply(session, meeting, backups, capsys)
    first = snapshot(stored(session, meeting))
    runs = session.scalars(select(AiRun)).all()
    code, client, out = apply(session, meeting, backups, capsys)
    assert code == 0 and "already rebuilt" in out
    assert snapshot(stored(session, meeting)) == first
    assert client.chats == 0
    assert session.scalars(select(AiRun)).all() == runs
    assert len(backup_files(backups)) == 1


def test_a_meeting_already_built_from_skribbys_text_with_the_same_speakers_is_left_alone(
    session, owner, template, backups, capsys
):
    row = make_meeting(session, owner, template, transcript_source="skribby")
    keep_evidence(session, row)
    code, _, _ = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code == 0  # the first run builds it (the stored labels differ from the evidence's)
    live = stored(session, row)
    live.transcript_source = "skribby"  # as the live path writes it
    session.commit()
    before = snapshot(stored(session, row))
    code, client, out = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code == 0 and "same speakers" in out
    assert client.chats == 0 and snapshot(stored(session, row)) == before


def test_a_meeting_on_the_skribby_path_with_an_unnamed_tag_is_rebuilt(
    session, owner, template, backups, capsys
):
    # made by the live path before the sole-candidate naming: one tag stayed "Unknown speaker"
    labelled = "[Ada Example]: red0\n[Bo Example]: blue0\n[Unknown speaker]: green0"
    row = make_meeting(
        session,
        owner,
        template,
        transcript_source="skribby",
        transcript=labelled,
        speaker_transcript=labelled,
    )
    keep_evidence(session, row)
    code, _, _ = go([str(row.id), "--apply"], backups, capsys=capsys)
    assert code == 0
    row = stored(session, row)
    assert row.transcript_source == "skribby-rebuilt"
    assert "[Cy Example]:" in row.speaker_transcript


def test_a_meeting_made_by_our_own_transcription_gets_skribbys_text(
    session, meeting, backups, capsys
):
    assert meeting.transcript_source == "own"
    apply(session, meeting, backups, capsys)
    row = stored(session, meeting)
    assert row.transcript != OLD_TRANSCRIPT and "alpha" not in row.transcript


# ---- what a person has touched ----


def touched_decision(meeting, text):
    from app.extraction import fingerprint

    return Decision(
        meeting_id=meeting.id,
        text=text,
        position=0,
        fingerprint=fingerprint(text),
        hidden_at=NOW,
    )


def test_a_hidden_decision_whose_wording_would_vanish_stops_the_rebuild(
    session, meeting, backups, capsys
):
    session.add(touched_decision(meeting, "Old wording of the first decision."))
    session.commit()
    before = snapshot(meeting)
    code, _, out = apply(session, meeting, backups, capsys)
    assert code != 0
    assert "1 decisions" in out and "--force" in out
    assert snapshot(stored(session, meeting)) == before
    assert session.scalars(select(Decision)).one().hidden_at is not None


def test_a_checked_review_item_an_answered_question_and_a_touched_task_each_stop_it(
    session, meeting, backups, capsys, owner
):
    from app.extraction import fingerprint

    team = Team(name="T", owner_user_id=owner.id)
    session.add(team)
    session.flush()
    session.add(
        ReviewItem(
            meeting_id=meeting.id,
            owner_user_id=owner.id,
            text="x",
            position=0,
            fingerprint=fingerprint("Old wording of the check."),
            checked_at=NOW,
        )
    )
    session.add(
        OpenQuestion(
            meeting_id=meeting.id,
            text="x",
            position=0,
            fingerprint=fingerprint("Old wording of the question?"),
            resolved_at=NOW,
        )
    )
    session.add(
        Task(
            team_id=team.id,
            meeting_id=meeting.id,
            source="ai",
            ai_fingerprint=fingerprint("Old wording of the action."),
            title="x",
            status="done",
            position=0,
        )
    )
    session.commit()
    code, _, out = apply(session, meeting, backups, capsys)
    assert code != 0
    assert "1 open questions, 1 review items, 1 tasks" in out
    assert stored(session, meeting).transcript == OLD_TRANSCRIPT


def test_a_task_a_person_deleted_counts_as_touched(session, meeting, backups, capsys, owner):
    from app.extraction import fingerprint

    team = Team(name="T", owner_user_id=owner.id)
    session.add(team)
    session.flush()
    session.add(
        Task(
            team_id=team.id,
            meeting_id=meeting.id,
            source="ai",
            ai_fingerprint=fingerprint("Old wording of the action."),
            title="x",
            status="todo",
            position=0,
            deleted_at=NOW,
        )
    )
    session.commit()
    code, _, out = apply(session, meeting, backups, capsys)
    assert code != 0 and "1 tasks" in out


def test_touched_items_whose_wording_survives_do_not_stop_it(session, meeting, backups, capsys):
    session.add(touched_decision(meeting, "New wording of the first decision."))
    session.commit()
    code, _, _ = apply(session, meeting, backups, capsys)
    assert code == 0
    assert stored(session, meeting).notes == NEW_NOTES


def test_force_goes_ahead_and_says_what_it_gives_up(session, meeting, backups, capsys):
    session.add(touched_decision(meeting, "Old wording of the first decision."))
    session.commit()
    code, _, out = apply(session, meeting, backups, capsys, extra=["--force"])
    assert code == 0 and "1 decisions" in out
    assert stored(session, meeting).notes == NEW_NOTES
    assert len(backup_files(backups)) == 1  # the old notes are in the backup


def test_notes_the_owner_has_already_rewritten_once_stop_the_rebuild(
    session, meeting, backups, capsys
):
    # the app has no way to edit notes by hand; the one way they change is the owner's rewrite
    # with a template, which leaves a second "notes" row in the ledger
    for _ in range(2):
        session.add(AiRun(meeting_id=meeting.id, kind="notes", model="m", status="ok"))
    session.commit()
    code, client, out = apply(session, meeting, backups, capsys)
    assert code != 0 and "rewritten" in out and "--force" in out
    assert client.chats == 0 and stored(session, meeting).notes == OLD_NOTES
    code, _, _ = apply(session, meeting, backups, capsys, extra=["--force"])
    assert code == 0 and stored(session, meeting).notes == NEW_NOTES


def test_the_notes_call_that_failed_in_the_ledger_does_not_count_as_a_rewrite(
    session, meeting, backups, capsys
):
    session.add(AiRun(meeting_id=meeting.id, kind="notes", model="m", status="ok"))
    session.add(AiRun(meeting_id=meeting.id, kind="notes", model="m", status="failed"))
    session.commit()
    code, _, _ = apply(session, meeting, backups, capsys)
    assert code == 0
