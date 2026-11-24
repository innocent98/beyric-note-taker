# ruff: noqa: F811
"""Exports of a draft (recipes plan T8): Markdown, text, CSV, Excel, Word and PDF, all from the
draft's CURRENT blocks. `app/recipes/exporters.py` builds them; `app/api_recipe_exports.py` serves
`GET /api/documents/{id}/export`."""

import csv
import io
from datetime import UTC, datetime

import pytest
from openpyxl import load_workbook

from app import export, services
from app.models import Document
from app.recipes import blocks as b
from app.recipes import exporters
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
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

WHEN = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
FORMULAS = ['=HYPERLINK("http://evil","x")', "+1+1", "-2+3", "@SUM(A1)", "  =1+1", "\t=1", "\r=1"]


def cell(text: str) -> b.Cell:
    return b.Cell(text=text, sources=[])


def table(columns: list[str], rows: list[list[str]]) -> b.Table:
    return b.Table(columns=columns, rows=[[cell(t) for t in row] for row in rows])


def draft(blocks: list, *, title="Proposal for Acme", status="ready", **fields) -> Document:
    return Document(
        title=title,
        output_type="document",
        status=status,
        blocks=b.dump_blocks(blocks),
        created_at=WHEN,
        **fields,
    )


PRICES = table(["Item", "Price"], [["Setup", "₦450,000"], ["Support", "₦50,000"]])


def with_table() -> Document:
    return draft(
        [
            b.Heading(level=1, text="Investment"),
            b.Paragraph(text="The costs we discussed."),
            PRICES,
        ]
    )


def read_csv(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


# ---- neutralising formulas ----


def test_formula_cells_are_neutralised_csv_and_xlsx():
    hostile = draft([table(["=cmd", "ok"], [[text, "fine"] for text in FORMULAS])])
    data, _, _ = exporters.export(hostile, "csv")
    grid = read_csv(data)
    assert grid[0] == ["'=cmd", "ok"]
    for row, text in zip(grid[1:], FORMULAS, strict=True):
        assert row[0] == "'" + text and row[1] == "fine"

    content, _, _ = exporters.export(hostile, "xlsx")
    sheet = load_workbook(io.BytesIO(content)).active
    assert sheet.cell(1, 1).value == "'=cmd" and sheet.cell(1, 1).data_type == "s"
    for index, text in enumerate(FORMULAS, start=2):
        found = sheet.cell(index, 1)
        assert found.data_type == "s" and found.value == "'" + text


def test_ordinary_text_is_not_touched_by_neutralising():
    plain = draft([table(["A"], [["Setup = 5"], ["₦450,000"], ["a-b"], ["x@y.test"]])])
    data, _, _ = exporters.export(plain, "csv")
    assert [row[0] for row in read_csv(data)[1:]] == ["Setup = 5", "₦450,000", "a-b", "x@y.test"]


# ---- CSV ----


def test_csv_has_bom_and_commas_in_cells_survive():
    tricky = draft([table(["Name", "Note"], [["Ada, Lovelace", 'said "hi"'], ["Ben", "a\nb"]])])
    data, mime, name = exporters.export(tricky, "csv")
    assert data.startswith(b"\xef\xbb\xbf")
    assert mime == "text/csv; charset=utf-8" and name == "Proposal for Acme.csv"
    assert read_csv(data) == [
        ["Name", "Note"],
        ["Ada, Lovelace", 'said "hi"'],
        ["Ben", "a\nb"],
    ]


def test_csv_of_the_second_table_and_an_unknown_one():
    second = table(["When"], [["Week 1"]])
    two = draft([PRICES, b.Paragraph(text="x"), second])
    assert read_csv(exporters.export(two, "csv", table=1)[0]) == [["When"], ["Week 1"]]
    with pytest.raises(services.Refused) as refused:
        exporters.export(two, "csv", table=2)
    assert (refused.value.status, refused.value.code) == (409, "no_table")


def test_csv_and_xlsx_need_a_table():
    no_table = draft([b.Paragraph(text="Just words")])
    for fmt in ("csv", "xlsx"):
        with pytest.raises(services.Refused) as refused:
            exporters.export(no_table, fmt)
        assert (refused.value.status, refused.value.code) == (409, "no_table")


# ---- Excel ----


def test_xlsx_opens_with_openpyxl_and_has_header_row():
    data, mime, name = exporters.export(with_table(), "xlsx")
    assert mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert name == "Proposal for Acme.xlsx"
    sheet = load_workbook(io.BytesIO(data)).active
    assert [c.value for c in sheet[1]] == ["Item", "Price"]
    assert [c.value for c in sheet[2]] == ["Setup", "₦450,000"]
    assert sheet.max_row == 3
    assert sheet["A1"].font.bold and sheet.freeze_panes == "A2"


def test_xlsx_has_one_sheet_per_table_named_from_its_heading():
    two = draft(
        [
            b.Heading(level=2, text="Timeline: phase/one?"),
            table(["When"], [["Week 1"]]),
            table(["Cost"], [["1"]]),
        ]
    )
    workbook = load_workbook(io.BytesIO(exporters.export(two, "xlsx")[0]))
    assert workbook.sheetnames == ["Timeline phaseone", "Table 2"]


# ---- Markdown and text ----


def test_markdown_table():
    data, mime, name = exporters.export(with_table(), "md")
    text = data.decode()
    assert mime == "text/markdown; charset=utf-8" and name == "Proposal for Acme.md"
    assert "# Investment" in text
    assert "| Item | Price |\n| --- | --- |\n| Setup | ₦450,000 |" in text


def test_text_export_is_plain():
    data, mime, name = exporters.export(with_table(), "txt")
    assert mime == "text/plain; charset=utf-8" and name == "Proposal for Acme.txt"
    assert "Item | Price\nSetup | ₦450,000" in data.decode()


# ---- the current blocks ----


def test_export_uses_edited_blocks():
    doc = with_table()
    before = exporters.export(doc, "md")[0].decode()
    assert "₦450,000" in before
    doc.blocks = b.dump_blocks(
        [b.Paragraph(text="Rewritten by the person"), table(["Item"], [["Changed"]])]
    )
    doc.edited = True
    after = exporters.export(doc, "md")[0].decode()
    assert "Rewritten by the person" in after and "Changed" in after
    assert "₦450,000" not in after and "Investment" not in after
    assert read_csv(exporters.export(doc, "csv")[0]) == [["Item"], ["Changed"]]


def test_blanks_stay_in_the_export_as_text():
    doc = draft([b.Paragraph(text="Start date: [need: start date]")])
    assert "[need: start date]" in exporters.export(doc, "txt")[0].decode()


# ---- Word and PDF ----


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_docx_pdf_go_through_export_build_file(fmt, monkeypatch):
    seen = {}

    def fake_build(notes_document, requested):
        seen["doc"], seen["fmt"] = notes_document, requested
        return b"FILE"

    monkeypatch.setattr(export, "build_file", fake_build)
    data, mime, name = exporters.export(with_table(), fmt)
    assert data == b"FILE" and seen["fmt"] == fmt
    assert mime == export.FORMATS[fmt][0] and name == f"Proposal for Acme.{fmt}"
    assert seen["doc"].title == "Proposal for Acme"
    assert seen["doc"].sections[0].blocks[1].kind == "table"


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError):
        exporters.export(with_table(), "rtf")


def test_a_draft_that_is_not_ready_is_refused():
    for status in ("writing", "failed"):
        with pytest.raises(services.Refused) as refused:
            exporters.export(draft([b.Paragraph(text="x")], status=status), "md")
        assert (refused.value.status, refused.value.code) == (409, "not_ready")


# ---- the route ----


def saved(session, owner, doc: Document) -> Document:
    doc.owner_user_id = owner.id
    session.add(doc)
    session.commit()
    return doc


def url(doc, fmt, extra=""):
    return f"/api/documents/{doc.id}/export?format={fmt}{extra}"


def test_the_route_serves_each_format_with_a_clean_name(api, session, owner):
    doc = saved(session, owner, with_table())
    md = api.get(url(doc, "md"))
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert "Proposal for Acme.md" in md.headers["content-disposition"]
    assert md.headers["cache-control"] == "no-store"
    assert md.headers["x-content-type-options"] == "nosniff"
    assert "| Setup | ₦450,000 |" in md.text
    csv_response = api.get(url(doc, "csv"))
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    xlsx = api.get(url(doc, "xlsx"))
    assert load_workbook(io.BytesIO(xlsx.content)).active["A1"].value == "Item"
    assert api.get(url(doc, "txt")).status_code == 200


def test_the_route_builds_word_through_the_renderer_child(api, session, owner):
    doc = saved(session, owner, with_table())
    response = api.get(url(doc, "docx"))
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


def test_the_route_refuses_a_table_export_without_a_table(api, session, owner):
    doc = saved(session, owner, draft([b.Paragraph(text="x")]))
    error_of(api.get(url(doc, "csv")), 409, "no_table")
    error_of(api.get(url(doc, "xlsx")), 409, "no_table")
    assert api.get(url(doc, "md")).status_code == 200


def test_the_route_refuses_a_draft_still_being_written(api, session, owner):
    doc = saved(session, owner, draft([], status="writing"))
    error_of(api.get(url(doc, "md")), 409, "not_ready")


def test_the_route_is_a_404_for_another_persons_draft_and_a_missing_one(
    api, session, owner, stranger
):
    mine = saved(session, owner, with_table())
    theirs = saved(session, stranger, with_table())
    assert api.get(url(mine, "md")).status_code == 200
    error_of(api.get(url(theirs, "md")), 404, "not_found")
    error_of(api.get("/api/documents/999999/export?format=md"), 404, "not_found")
    error_of(api.get("/api/documents/99999999999999999999/export?format=md"), 404, "not_found")


def test_the_route_needs_a_known_format(api, session, owner):
    doc = saved(session, owner, with_table())
    assert api.get(url(doc, "rtf")).status_code == 422
    assert api.get(url(doc, "md", "&table=-1")).status_code == 422


def test_the_default_format_is_pdf(api, session, owner, monkeypatch):
    doc = saved(session, owner, with_table())
    monkeypatch.setattr(export, "build_file", lambda notes_document, fmt: b"%PDF-" + fmt.encode())
    response = api.get(f"/api/documents/{doc.id}/export")
    assert response.content == b"%PDF-pdf" and response.headers["content-type"] == "application/pdf"


def test_the_route_needs_a_login(anon, session, owner):
    doc = saved(session, owner, with_table())
    assert anon.get(url(doc, "md")).status_code == 401


def test_a_busy_or_failed_render_answers_with_the_project_error_shape(
    api, session, owner, monkeypatch
):
    doc = saved(session, owner, with_table())

    def busy(*_):
        raise export.ExportBusy()

    monkeypatch.setattr(export, "build_file", busy)
    busy_response = api.get(url(doc, "pdf"))
    error_of(busy_response, 503, "export_busy")
    assert busy_response.headers["retry-after"] == "5"

    def slow(*_):
        raise export.ExportFailed("slow", timed_out=True)

    monkeypatch.setattr(export, "build_file", slow)
    error_of(api.get(url(doc, "pdf")), 504, "export_timeout")

    def broken(*_):
        raise export.ExportFailed("broken")

    monkeypatch.setattr(export, "build_file", broken)
    error_of(api.get(url(doc, "docx")), 500, "export_failed")
