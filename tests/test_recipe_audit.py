"""The number audit and the blanks (`app/recipes/audit.py`): code, no model. Advisory: it flags
figures the meeting, notes, tasks and brief do not support, and finds `[need: ...]` blanks."""

import pytest

from app.recipes import audit as a
from app.recipes.blocks import (
    Bullets,
    Card,
    Cell,
    Column,
    Heading,
    Item,
    Paragraph,
    Table,
)

CORPUS = [
    "Daniel: We can do the onboarding for 50k, so 50,000 naira in total.",
    "Ada: Kick-off is 2026-12-01 and the review is on the 15th of December.",
    "Daniel: Support is $1,200 a month, about 20% off the list.",
    "Ada: We need 14 days for the pilot with 12 users.",
    "Notes: team of 8 people, 3 workshops.",
]


def para(text):
    return Paragraph(text=text)


def flagged(text, corpus=CORPUS):
    return [(f.text, f.kind) for f in a.audit([para(text)], corpus)]


def test_invented_price_is_flagged():
    flags = a.audit([para("The total investment is ₦450,000 for the pilot.")], CORPUS)
    assert [(f.block_index, f.text, f.kind) for f in flags] == [(0, "₦450,000", "figure")]


def test_figure_in_transcript_is_not_flagged():
    assert flagged("The setup costs 50k.") == []
    assert flagged("The setup costs 50,000.") == []
    assert flagged("The setup costs ₦50,000 in total.") == []
    assert a.normalise_figures("50k") == a.normalise_figures("50,000")
    assert "v:50000" in a.normalise_figures("50k")
    assert "v:50000" in a.normalise_figures("50,000")


@pytest.mark.parametrize(
    "corpus_text, draft",
    [
        ("It is N450,000.", "NGN 450,000"),
        ("It is N450,000.", "450,000 naira"),
        ("It is N450,000.", "₦450k"),
        ("About ₦1.5m.", "1,500,000 naira"),
        ("It is $1,200.", "1200 USD"),
        ("It is $1,200.", "1.2k dollars"),
        ("It is 80 euros.", "€80"),
        ("It is £5m.", "5 million pounds"),
        ("It is 5 thousand dollars.", "$5,000"),
    ],
)
def test_naira_dollar_euro_pound_forms(corpus_text, draft):
    assert flagged(f"Fee: {draft}.", [corpus_text]) == []


def test_a_figure_in_another_currency_is_flagged():
    assert flagged("Fee: ₦450,000.", ["It is $450,000."]) == [("₦450,000", "figure")]
    # a bare number in the meeting supports a currency amount in the draft
    assert flagged("Fee: ₦450,000.", ["It is 450,000."]) == []


def test_written_out_number():
    assert flagged("The setup costs fifty thousand naira.") == []
    assert flagged("The setup costs seventy thousand naira.") == [
        ("seventy thousand naira", "figure")
    ]
    assert flagged("We expect twelve users.") == []
    assert flagged("We expect nineteen users.") == [("nineteen", "figure")]
    # small number words are ordinary prose ("one of the options"), not figures
    assert flagged("One of the two options is simple.") == []
    assert "v:50000" in a.normalise_figures("fifty thousand")
    assert "v:1250" in a.normalise_figures("one thousand two hundred and fifty")
    assert "v:45" in a.normalise_figures("forty-five")


def test_date_forms():
    corpus = [
        "Kick-off is 2026-12-01, the review is on the 15th of December, the demo 03/04/2026.",
        "Training starts 22 Jan.",
    ]
    for ok in (
        "2026-12-01",
        "December 1, 2026",
        "1st Dec 2026",
        "12/01/2026",
        "01/12/2026",  # UK form: ambiguous, one reading matches
        "15th Dec",
        "Dec 15",
        "15/12/2026",
        "22nd January",
        "Jan 22",
        "3 April 2026",
        "4 March 2026",
    ):
        assert flagged(f"Due {ok}.", corpus) == [], ok
    for bad in ("2026-12-02", "16th Dec", "Feb 3", "13/12/2026", "1 Dec 2027"):
        assert [f[1] for f in flagged(f"Due {bad}.", corpus)] == ["figure"], bad


def test_a_time_is_a_figure_too():
    corpus = ["We meet at 3pm on Tuesday."]
    assert flagged("See you at 15:00.", corpus) == []
    assert flagged("See you at 4pm.", corpus) == [("4pm", "figure")]


def test_list_ordinals_and_line_numbers_ignored():
    text = "1. First thing\nlines 12 to 15 and [L40] and line 7 and the 2nd option and v2.1 and A4"
    assert flagged(text, ["nothing numeric here"]) == []
    table = Table(
        columns=["#", "Task"],
        rows=[[Cell(text="1"), Cell(text="Send the plan")], [Cell(text="2"), Cell(text="Call")]],
    )
    assert a.audit([table], ["nothing numeric here"]) == []
    plain_table = Table(columns=["Owner", "Count"], rows=[[Cell(text="Ada"), Cell(text="2")]])
    assert [f.text for f in a.audit([plain_table], ["nothing numeric here"])] == ["2"]


def test_derived_figure_flagged_derived():
    # the sum of two figures in the meeting, a conversion of a length of time
    assert flagged("The total is 100,000 naira.", ["Setup 50,000 and support 50,000."]) == [
        ("100,000 naira", "derived")
    ]
    assert flagged("Phase three comes to 75,000.", ["Phase one 50,000.", "Phase two 25,000."]) == [
        ("75,000", "derived")
    ]
    assert flagged("The pilot takes two weeks.", ["We need 14 days for the pilot."]) == [
        ("two weeks", "derived")
    ]
    assert flagged("The pilot takes 3 weeks.", ["We need 14 days for the pilot."]) == [
        ("3 weeks", "figure")
    ]


def test_blank_tokens_found():
    blocks = [
        Heading(level=1, text="Proposal"),
        para("Price: [need: price] and start [NEED: start date]. Total [need:3 deliverables]"),
        Bullets(items=[Item(text="Owner [need: owner]")]),
        Table(columns=["A"], rows=[[Cell(text="[need: delivery date]")]]),
    ]
    assert [(b.block_index, b.what) for b in a.blanks(blocks)] == [
        (1, "price"),
        (1, "start date"),
        (1, "3 deliverables"),
        (2, "owner"),
        (3, "delivery date"),
    ]
    # a blank's own words are not figures
    assert a.audit([para("Total [need: 3 deliverables]")], ["nothing"]) == []


def test_table_cells_are_audited():
    table = Table(
        columns=["Item", "Cost"],
        rows=[
            [Cell(text="Setup"), Cell(text="50,000")],
            [Cell(text="Extras"), Cell(text="₦450,000")],
        ],
    )
    assert [
        (f.block_index, f.text) for f in a.audit([Heading(level=1, text="x"), table], CORPUS)
    ] == [(1, "₦450,000")]


def test_board_cards_are_audited_and_each_figure_is_flagged_once_per_block():
    column = Column(
        title="Money", cards=[Card(text="Budget ₦450,000"), Card(text="Again ₦450,000")]
    )
    assert [f.text for f in a.audit([column], CORPUS)] == ["₦450,000"]


def test_another_language_is_still_audited_on_its_digits():
    assert flagged("Le total est de 450 000 €.", ["Le total est de 80 euros."]) != []
    assert flagged("Le total est de €80.", ["Le total est de 80 euros."]) == []


def test_the_brief_and_notes_count_as_corpus():
    brief = "Standard onboarding package: ₦450,000, 10 working days."
    assert flagged("The package is ₦450,000.", [*CORPUS, brief]) == []


def test_recall_on_seeded_set():
    seeded = [
        "The price is ₦450,000.",
        "We will charge $7,500 for the pilot.",
        "A discount of 35% applies.",
        "Delivery is on 2027-03-09.",
        "The review is on 28 February.",
        "We will deliver in 9 weeks.",
        "That is a total of £30,000.",
        "The retainer is 2.5m naira.",
        "Support runs until 11pm.",
        "Seventy five thousand dollars is the cap.",
    ]
    missed = [text for text in seeded if not a.audit([para(text)], CORPUS)]
    assert missed == []
    # and nothing invented was explained away as supported
    assert all(f.kind in ("figure", "derived") for t in seeded for f in a.audit([para(t)], CORPUS))
