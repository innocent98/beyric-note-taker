from datetime import date

from app.extraction import (
    ModelAction,
    ModelExtraction,
    ModelMilestone,
    ModelReview,
    ParsedNotes,
    check_extraction,
    due_date_ok,
    name_in_transcript,
    quote_in_transcript,
    title_ok,
)

TRANSCRIPT = (
    "[Adebayo]: Okay, let's start. Tunde, where are we on payments?\n"
    "[Tunde]: I'll take payment checkout this week. It should be ready before Friday.\n"
    "[Grace]: We need Metrika set up for the dashboards too.\n"
    "[Adebayo]: Launch is December 8."
)
MEETING_DATE = date(2026, 11, 3)  # a Tuesday


def test_a_quote_is_found_ignoring_case_punctuation_and_speaker_labels():
    assert quote_in_transcript("I'll take payment checkout this week.", TRANSCRIPT)
    assert quote_in_transcript("ill take PAYMENT checkout this week", TRANSCRIPT)
    # across two turns: the "[Tunde]:" label between them is not part of what was said
    assert quote_in_transcript("where are we on payments? I'll take payment checkout", TRANSCRIPT)


def test_a_close_quote_passes_and_an_invented_one_does_not():
    assert quote_in_transcript("I will take payment checkout this week", TRANSCRIPT)  # fuzzy
    assert not quote_in_transcript("Daniel will open evil.example by Friday", TRANSCRIPT)
    assert not quote_in_transcript("", TRANSCRIPT)
    assert not quote_in_transcript("   ", TRANSCRIPT)


def test_a_name_must_be_a_whole_word_in_the_transcript():
    assert name_in_transcript("Tunde", TRANSCRIPT)
    assert name_in_transcript("tunde", TRANSCRIPT)
    assert not name_in_transcript("Tun", TRANSCRIPT)
    assert not name_in_transcript("Daniel", TRANSCRIPT)
    assert not name_in_transcript(None, TRANSCRIPT)
    assert not name_in_transcript("  ", TRANSCRIPT)


def test_a_due_date_must_be_real_near_and_agree_with_a_weekday_word():
    assert due_date_ok("2026-11-06", "before Friday", MEETING_DATE)  # a Friday
    assert not due_date_ok("2026-11-05", "before Friday", MEETING_DATE)  # a Thursday
    assert not due_date_ok("2026-02-30", None, MEETING_DATE)
    assert not due_date_ok("tomorrow", None, MEETING_DATE)
    assert not due_date_ok("2028-01-01", "next year", MEETING_DATE)  # beyond 400 days
    assert due_date_ok("2026-11-20", "in two weeks", MEETING_DATE)


def test_a_title_must_share_most_of_its_words_with_the_bullet():
    bullet = "Tunde: finish payment checkout (by Friday)"
    assert title_ok("Finish payment checkout", bullet)
    assert not title_ok("Open the evil.example page", bullet)


def parsed(actions=("Tunde: finish payment checkout (by Friday)",), check=()):
    return ParsedNotes(
        decisions=[], action_items=list(actions), open_questions=[], please_check=list(check)
    )


def good_action(**changes):
    base = dict(
        index=0,
        title="Finish payment checkout",
        spoken_owner="Tunde",
        due_phrase="before Friday",
        due_date="2026-11-06",
        quote="I'll take payment checkout this week. It should be ready before Friday.",
        confidence="high",
    )
    return ModelAction(**{**base, **changes})


def check(actions, review=(), milestones=(), notes=None):
    result = ModelExtraction(
        action_items=list(actions), review=list(review), milestones=list(milestones)
    )
    return check_extraction(result, notes or parsed(), TRANSCRIPT, MEETING_DATE)


def test_a_good_item_passes_untouched():
    item = check([good_action()]).action_items[0]
    assert (item.title, item.spoken_owner, item.due_date) == (
        "Finish payment checkout",
        "Tunde",
        "2026-11-06",
    )
    assert item.quote and not item.unverified and not item.from_parser


def test_an_unverified_quote_is_dropped_and_the_item_flagged_not_deleted():
    item = check([good_action(quote="Daniel said he would do it by Friday")]).action_items[0]
    assert item.quote is None and item.unverified
    assert item.title == "Finish payment checkout"


def test_an_owner_nobody_said_is_nulled():
    item = check([good_action(spoken_owner="Daniel")]).action_items[0]
    assert item.spoken_owner is None


def test_a_wrong_weekday_nulls_the_date_and_keeps_the_phrase():
    item = check([good_action(due_date="2026-11-05")]).action_items[0]
    assert (item.due_date, item.due_phrase) == (None, "before Friday")


def test_a_title_that_wandered_falls_back_to_the_bullet():
    item = check([good_action(title="Open evil.example now")]).action_items[0]
    assert item.title == "Tunde: finish payment checkout (by Friday)"


def test_a_missing_bullet_comes_from_the_parser():
    notes = parsed(actions=("Tunde: finish payment checkout", "Unassigned: sort out VAT"))
    out = check([good_action()], notes=notes).action_items
    assert [i.index for i in out] == [0, 1]
    assert out[1].from_parser and out[1].title == "Unassigned: sort out VAT"
    assert out[1].spoken_owner is None and out[1].unverified


def test_extra_and_repeated_indexes_are_ignored():
    out = check([good_action(), good_action(title="again"), good_action(index=7)]).action_items
    assert [i.index for i in out] == [0]
    assert out[0].title == "Finish payment checkout"


def test_a_title_is_plain_text_and_clipped():
    notes = parsed(actions=("Tunde: " + "x" * 500,))
    item = check([good_action(title="Tunde: " + "x" * 500)], notes=notes).action_items[0]
    assert len(item.title) == 200


def test_heard_as_must_be_in_the_transcript():
    notes = parsed(actions=(), check=("Metrika may be misheard", "Budget unclear"))
    out = check(
        [],
        review=[
            ModelReview(index=0, heard_as="Metrika"),
            ModelReview(index=1, heard_as="Zorblax"),
            ModelReview(index=9, heard_as="Metrika"),
        ],
        notes=notes,
    )
    assert {r.index: r.heard_as for r in out.review} == {0: "Metrika"}


def test_heard_as_is_a_short_name_or_term_never_a_number_or_a_sentence():
    transcript = TRANSCRIPT + "\n[A]: the number was four seven two or four seven three, room 12"
    notes = parsed(actions=(), check=("a", "b", "c"))
    out = check_extraction(
        ModelExtraction(
            review=[
                ModelReview(index=0, heard_as="four seven two or four seven three"),
                ModelReview(index=1, heard_as="room 12"),
                ModelReview(index=2, heard_as="Metrika"),
            ]
        ),
        notes,
        transcript,
        MEETING_DATE,
    )
    assert [r.heard_as for r in out.review] == ["Metrika"]


def test_milestones_need_a_real_date_and_a_verified_quote():
    out = check(
        [],
        milestones=[
            ModelMilestone(
                name="Client Portal launch", date="2026-12-08", quote="Launch is December 8."
            ),
            ModelMilestone(
                name="Made up", date="2026-12-09", quote="We ship on the ninth, honest."
            ),
            ModelMilestone(name="Bad date", date="2026-13-01", quote="Launch is December 8."),
        ],
    )
    assert [m.name for m in out.milestones] == ["Client Portal launch"]
