"""Ask Beyric's two routes (spec 1.7). Included into `app.api.router`, so they share its `/api`
prefix, login check, CSRF rule and error shape (`{"error": {"code", "message"}}`).

`GET /api/ask/scope` is a few counts and no model. `POST /api/ask` answers from the meetings the
caller may read (`app/ask.py`); the question and the answer are never stored or logged.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import ask, auth, services
from app.api_schemas import ErrorResponse
from app.db import get_session
from app.models import User
from app.services import Refused

router = APIRouter()


class AskIn(BaseModel):
    # Trimmed; empty, or more than 300 characters, is 422 `question_invalid`.
    question: str


class CiteOut(BaseModel):
    meeting_id: int
    # Plain text. Never render it as a link or as HTML.
    title: str
    # The day in the asker's time zone, "Mon 2 Nov": no time of day.
    date_label: str
    # The meeting owner's display name when the meeting is not the asker's; null for their own.
    owner_name: str | None
    # The meeting is shared with a team.
    shared: bool
    # `transcript`: open the Transcript tab at `line`; `notes`: open the meeting.
    target: Literal["transcript", "notes"]
    # 1-based row of the transcript the meeting page draws; null unless `target` is `transcript`.
    line: int | None


class PointOut(BaseModel):
    # Plain text, at most 400 characters.
    text: str
    # One to three source chips. Empty only for a task whose meeting the asker may not read.
    cites: list[CiteOut]


class AskOut(BaseModel):
    kind: Literal["answer", "tasks", "none"]
    # Plain text, at most 300 characters.
    lead: str
    points: list[PointOut]


class ScopeOut(BaseModel):
    meetings: int
    decisions: int
    tasks: int
    # The chips under the input, in the designer's order; the name chip is left out when the
    # asker's meetings name no one else.
    suggestions: list[str]


@router.get("/ask/scope", tags=["ask"])
def ask_scope(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> ScopeOut:
    """What a question searches: the ready meetings the caller may read, their decisions and
    tasks, and the question chips. No model call."""
    found = ask.scope(session, user)
    return ScopeOut(
        meetings=found.meetings,
        decisions=found.decisions,
        tasks=found.tasks,
        suggestions=found.suggestions,
    )


@router.post(
    "/ask",
    tags=["ask"],
    responses={
        422: {"model": ErrorResponse, "description": "`question_invalid`: empty or too long"},
        429: {
            "model": ErrorResponse,
            "description": "`ask_limit`: the daily cap is spent; `ask_rate`: more than 20 "
            "questions in a minute",
        },
        502: {
            "model": ErrorResponse,
            "description": "`ask_failed`: the model gave no usable answer",
        },
    },
)
def ask_question(
    body: AskIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    client=Depends(services.get_openai_client),
) -> AskOut:
    """Answer a question from the meetings the caller may read. `kind` `tasks`: the caller's own
    tasks (no model, free). `answer`: points, each with source chips. `none`: nothing found."""
    if not request.app.state.public_limits.ask_rate.hit(f"user:{user.id}"):
        raise Refused(429, "ask_rate", "Slow down a little and try again in a minute.")
    result = ask.answer(session, user, body.question, client=client)
    return AskOut(
        kind=result.kind,
        lead=result.lead,
        points=[
            PointOut(text=p.text, cites=[CiteOut(**vars(c)) for c in p.cites])
            for p in result.points
        ],
    )
