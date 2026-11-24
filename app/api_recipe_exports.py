"""Download a draft: `GET /api/documents/{id}/export` (recipes plan T8). Included into
`app.api.router`, so it shares its `/api` prefix, login check and error shape.

The file is built from the draft's current blocks when asked and never stored. Word and PDF are
built one at a time per server process by the notes export's child process, and a second request
meanwhile answers 503 `export_busy`, as the notes export does.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import auth, export
from app.api_recipes import document_or_404
from app.api_schemas import ErrorResponse
from app.db import get_session
from app.models import Document, User
from app.notes_document import content_disposition
from app.recipes import exporters

router = APIRouter()

_FILE_TYPES = {
    exporters._TYPES["md"]: {"schema": {"type": "string"}},
    exporters._TYPES["csv"]: {"schema": {"type": "string", "format": "binary"}},
    exporters._TYPES["xlsx"]: {"schema": {"type": "string", "format": "binary"}},
    export.FORMATS["pdf"][0]: {"schema": {"type": "string", "format": "binary"}},
    export.FORMATS["docx"][0]: {"schema": {"type": "string", "format": "binary"}},
}

EXPORT_RESPONSES = {
    200: {
        "description": "The file. Built for this request and not stored.",
        "content": _FILE_TYPES,
    },
    404: {"model": ErrorResponse, "description": "No such draft (another person's too)"},
    409: {
        "model": ErrorResponse,
        "description": "`not_ready`: the draft is still being written or failed; `no_table`: "
        "CSV and Excel need a draft with a table (and `table` must name one of them)",
    },
    500: {"model": ErrorResponse, "description": "`export_failed`: the file could not be built"},
    503: {"model": ErrorResponse, "description": "`export_busy`: another file is being built"},
    504: {"model": ErrorResponse, "description": "`export_timeout`: it took too long"},
}


def _error(status: int, code: str, message: str, headers=None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


@router.get(
    "/documents/{document_id:id}/export",
    tags=["documents"],
    response_class=Response,
    responses=EXPORT_RESPONSES,
)
def export_document(
    document_id: int,
    format: Annotated[exporters.Format, Query()] = "pdf",
    table: int = Query(0, ge=0, le=99),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """The draft as Markdown (`md`), plain text (`txt`), CSV, Excel (`xlsx`), Word (`docx`) or PDF
    (the default), from its current blocks, edits included.

    `csv` and `xlsx` need a draft with a table (409 `no_table` without one): the CSV holds the
    table numbered `table` (the first by default), the Excel file holds every table, one sheet
    each. A CSV is UTF-8 with a byte order mark so Excel reads it right, and a cell that could run
    as a formula is written with a leading apostrophe. Built when asked and never stored; the file
    name is the draft's title, cleaned."""
    doc = document_or_404(session, user, document_id)
    try:
        # Reads the blocks before the session is closed: the Word and PDF build can take seconds.
        content, media_type, filename = _build(session, doc, format, table)
    except export.ExportBusy:
        return _error(
            503,
            "export_busy",
            "Another file is being prepared. Try again in a few seconds.",
            headers={"Retry-After": "5"},
        )
    except export.ExportFailed as failure:
        if failure.timed_out:
            return _error(504, "export_timeout", "Preparing the file took too long. Try again.")
        return _error(500, "export_failed", "The file could not be prepared. Try again.")
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _build(session: Session, doc: Document, fmt: str, table: int):
    if fmt in ("docx", "pdf") and doc.status == "ready":
        session.expunge(doc)  # nothing else is read: hand the connection back before the render
        session.close()
    return exporters.export(doc, fmt, table=table)
