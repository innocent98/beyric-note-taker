"""The block schema of a draft (`app/recipes/blocks.py`): what the model's reply may contain, and
the plain renderings every export starts from."""

import pytest

from app.recipes import blocks as b

ALLOWED = {
    (1, 12),
    (1, 13),
    (1, None),
    (2, None),
    ("decision", 1, 7),
    ("task", 2, 9),
}


def parse(raw, **kw):
    return b.parse_blocks(raw, allowed_sources=kw.pop("allowed", ALLOWED), **kw)


def line(meeting, n, quote=None):
    return {"kind": "line", "meeting_id": meeting, "line": n, "quote": quote, "ref": None}


def para(text, sources=()):
    return {"type": "paragraph", "text": text, "sources": list(sources)}


def test_unknown_source_is_dropped():
    out = parse(
        [
            para(
                "Agreed on a start date.",
                [
                    line(1, 12, "we agreed on the"),
                    line(1, 99),  # a line that was never sent
                    line(7, 12),  # a meeting that was never sent
                    {"kind": "meeting", "meeting_id": 2, "line": None, "quote": None, "ref": None},
                    {"kind": "decision", "meeting_id": 1, "line": None, "quote": None, "ref": 7},
                    {"kind": "decision", "meeting_id": 1, "line": None, "quote": None, "ref": 8},
                    {"kind": "bogus", "meeting_id": 1, "line": 12},
                    "not even a dict",
                ],
            )
        ]
    )
    kept = [(s.kind, s.meeting_id, s.line, s.ref) for s in out[0].sources]
    assert kept == [("line", 1, 12, None), ("meeting", 2, None, None), ("decision", 1, None, 7)]
    assert out[0].sources[0].quote == "we agreed on the"


def test_a_line_number_is_not_a_decision_id():
    # Meeting 1 has a line 12 but no decision 12: the two are different keys.
    out = parse(
        [para("x", [{"kind": "decision", "meeting_id": 1, "line": None, "quote": None, "ref": 12}])]
    )
    assert out[0].sources == []


def test_html_and_links_become_plain_text():
    out = parse(
        [
            {"type": "heading", "level": 2, "text": "<b>Plan</b> & <script>x()</script>"},
            para("See [our site](https://evil.example.test/x) and **bold** at https://a.test/p"),
            {
                "type": "bullets",
                "items": [{"text": "- <img src=x onerror=alert(1)> `code`", "sources": []}],
            },
        ]
    )
    assert out[0].text == "Plan & x()"
    assert out[1].text == "See our site and bold at [link removed]"
    assert out[2].items[0].text == "code"
    for block in out:
        assert "<" not in str(block.model_dump()) and "http" not in str(block.model_dump())


def test_table_rows_must_match_columns():
    good = {
        "type": "table",
        "columns": ["Item", "Price"],
        "rows": [[{"text": "Setup", "sources": []}, {"text": "[need: price]", "sources": []}]],
    }
    out = parse([good])
    assert out[0].columns == ["Item", "Price"]
    assert out[0].rows[0][1].text == "[need: price]"
    short = {**good, "rows": [[{"text": "Setup", "sources": []}]]}
    with pytest.raises(b.BlocksInvalid) as error:
        parse([short])
    assert error.value.code == "table_row"
    with pytest.raises(b.BlocksInvalid):
        parse([{**good, "columns": []}])


def test_oversize_block_is_cut():
    out = parse(
        [
            para("word " * 2000),
            {
                "type": "bullets",
                "items": [{"text": f"item {i}", "sources": []} for i in range(500)],
            },
            {
                "type": "table",
                "columns": [f"c{i}" for i in range(40)],
                "rows": [],
            },
        ]
    )
    assert len(out[0].text) <= b.PARAGRAPH_CHARS and out[0].text.endswith("…")
    assert len(out[1].items) == b.MAX_BULLETS
    assert len(out[2].columns) == b.MAX_COLUMNS


def test_too_many_blocks_keeps_the_first_ones():
    raw = [para(f"p{i}") for i in range(10)]
    assert [x.text for x in parse(raw, max_blocks=3)] == ["p0", "p1", "p2"]


def test_a_table_keeps_at_most_the_row_limit_and_each_cell_is_cut():
    columns = ["A", "B"]
    rows = [
        [{"text": "x" * 900, "sources": []}, {"text": str(i), "sources": []}] for i in range(300)
    ]
    out = parse([{"type": "table", "columns": columns, "rows": rows}])
    assert len(out[0].rows) == b.MAX_ROWS
    assert len(out[0].rows[0][0].text) <= b.CELL_CHARS


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "a string",
        {"type": "paragraph"},
        [],  # nothing to show is a failed reply, not an empty draft
        [para("")],  # everything cleaned away
        ["not a dict"],
        [{"type": "chart", "text": "x"}],
        [{"type": "table", "columns": "A", "rows": []}],
        [{"type": "bullets", "items": "x"}],
    ],
)
def test_malformed_reply_raises(raw):
    with pytest.raises(b.BlocksInvalid):
        parse(raw)


def test_headings_levels_are_bounded_and_empty_parts_dropped():
    out = parse(
        [
            {"type": "heading", "level": 9, "text": "Top"},
            {"type": "heading", "level": "x", "text": "Odd"},
            {"type": "heading", "level": 1, "text": "   "},
            {"type": "bullets", "items": [{"text": "", "sources": []}]},
            para("kept"),
        ]
    )
    assert [(x.type, getattr(x, "level", None)) for x in out] == [
        ("heading", 3),
        ("heading", 2),
        ("paragraph", None),
    ]


def test_sources_are_deduplicated_and_limited_and_quotes_are_cut():
    many = [line(1, 12, "q " * 100)] * 3 + [line(1, 13)]
    out = parse([para("x", many)])
    assert [s.line for s in out[0].sources] == [12, 13]
    assert len(out[0].sources[0].quote) <= b.QUOTE_CHARS


def test_board_columns_and_cards():
    out = parse(
        [
            {
                "type": "column",
                "title": "Decisions",
                "cards": [
                    {
                        "text": "Ship in March",
                        "kind": "decision",
                        "owner": "Ada",
                        "sources": [line(1, 12)],
                    },
                    {"text": "Who pays?", "kind": "weird", "owner": None, "sources": []},
                    {"text": "", "kind": "note", "owner": None, "sources": []},
                ],
            }
        ]
    )
    column = out[0]
    assert column.title == "Decisions"
    assert [(c.text, c.kind, c.owner) for c in column.cards] == [
        ("Ship in March", "decision", "Ada"),
        ("Who pays?", "note", None),
    ]


def test_dump_and_load_round_trip():
    raw = [
        {"type": "heading", "level": 1, "text": "Follow-up"},
        para("Thanks", [line(1, 12, "thanks for")]),
        {"type": "bullets", "items": [{"text": "One", "sources": []}]},
        {
            "type": "table",
            "columns": ["A"],
            "rows": [[{"text": "1", "sources": []}]],
        },
    ]
    parsed = parse(raw)
    stored = b.dump_blocks(parsed)
    assert b.load_blocks(stored) == parsed
    assert stored[1]["type"] == "paragraph" and stored[1]["sources"][0]["line"] == 12


def table_blocks():
    return parse(
        [
            {"type": "heading", "level": 2, "text": "Investment"},
            para("Total: [need: price] | later"),
            {
                "type": "table",
                "columns": ["Item", "Cost"],
                "rows": [
                    [{"text": "Setup", "sources": []}, {"text": "50,000", "sources": []}],
                    [{"text": "A | B", "sources": []}, {"text": "=SUM(1)", "sources": []}],
                ],
            },
            {"type": "bullets", "items": [{"text": "one_two", "sources": []}]},
        ]
    )


def test_to_markdown_table():
    md = b.to_markdown(table_blocks())
    assert md.splitlines() == [
        "## Investment",
        "",
        "Total: \\[need: price\\] \\| later",
        "",
        "| Item | Cost |",
        "| --- | --- |",
        "| Setup | 50,000 |",
        "| A \\| B | =SUM(1) |",
        "",
        "- one\\_two",
    ]


def test_to_text():
    assert b.to_text(table_blocks()).splitlines() == [
        "Investment",
        "",
        "Total: [need: price] | later",
        "",
        "Item | Cost",
        "Setup | 50,000",
        "A | B | =SUM(1)",
        "",
        "- one_two",
    ]


def test_to_text_and_markdown_of_a_board():
    out = parse(
        [
            {
                "type": "column",
                "title": "Open",
                "cards": [
                    {"text": "Who pays?", "kind": "question", "owner": "Ada", "sources": []},
                ],
            }
        ]
    )
    assert b.to_text(out) == "Open\n- Who pays? (Ada)"
    assert b.to_markdown(out) == "## Open\n\n- Who pays? (Ada)"


def test_texts_lists_every_piece_of_text_with_its_block_index():
    pieces = list(b.texts(table_blocks()))
    assert (0, "Investment") in pieces and (1, "Total: [need: price] | later") in pieces
    assert (2, "50,000") in pieces and (2, "=SUM(1)") in pieces and (3, "one_two") in pieces
    assert (2, "Item") in pieces  # column headings are text too
