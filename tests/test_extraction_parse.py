from app.extraction import fingerprint, normalise, parse_notes

NOTES = """\
## Summary
The team met about launch.

## Key points
- Payments slipped.

## Decisions
- Launch moves to 14 March.
- **Budget** is capped at £4,000.

## Action items
- Tunde: send the revised contract (by Friday)
- Unassigned: sort out VAT

## Open questions
- Which email provider do we pick?
  It was left for next week.

## Please check
- "Burek" on the login page may be "Beyric".
"""


def test_every_section_is_split_into_its_bullets():
    parsed = parse_notes(NOTES)
    assert parsed.decisions == ["Launch moves to 14 March.", "Budget is capped at £4,000."]
    assert parsed.action_items == [
        "Tunde: send the revised contract (by Friday)",
        "Unassigned: sort out VAT",
    ]
    assert parsed.open_questions == ["Which email provider do we pick? It was left for next week."]
    assert parsed.please_check == ['"Burek" on the login page may be "Beyric".']


def test_none_noted_means_no_items_however_it_is_written():
    for body in ("None noted", "None noted.", "- None noted", "* None noted.", "**None noted**"):
        notes = NOTES.replace(
            "- Launch moves to 14 March.\n- **Budget** is capped at £4,000.", body
        )
        assert parse_notes(notes).decisions == [], body


def test_numbered_and_plain_lines_count_as_items():
    notes = NOTES.replace(
        "- Launch moves to 14 March.\n- **Budget** is capped at £4,000.",
        "1. Launch moves to 14 March.\n2) Budget is capped.\n• Hiring is paused.\nPlain line.",
    )
    assert parse_notes(notes).decisions == [
        "Launch moves to 14 March.",
        "Budget is capped.",
        "Hiring is paused.",
        "Plain line.",
    ]


def test_a_repeated_bullet_is_one_item():
    notes = NOTES.replace("- Launch moves to 14 March.", "- Budget is capped at £4,000.")
    assert parse_notes(notes).decisions == ["Budget is capped at £4,000."]


def test_a_missing_section_is_empty_not_an_error():
    parsed = parse_notes("## Summary\nHello.\n")
    assert (parsed.decisions, parsed.action_items) == ([], [])


def test_an_item_is_clipped_to_a_sane_length():
    long = "x" * 5000
    notes = NOTES.replace("- Launch moves to 14 March.", f"- {long}")
    assert len(parse_notes(notes).decisions[0]) == 1000


def test_fingerprint_ignores_case_spacing_and_punctuation_only():
    assert fingerprint("Launch moves to 14 March.") == fingerprint("  launch  moves to 14 march ")
    assert fingerprint("Launch moves to 14 March.") != fingerprint("Launch moves to 15 March.")
    assert len(fingerprint("x")) == 64


def test_normalise_strips_accents_and_punctuation():
    assert normalise("Kemi  Adéyemi, said: “Hi!”") == "kemi adeyemi said hi"
