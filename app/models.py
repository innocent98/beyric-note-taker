from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATUSES = ("bot_sent", "processing", "ready", "failed")
STEPS = ("waiting", "audio", "transcribing", "speakers", "notes", "done")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    instructions: Mapped[str] = mapped_column(Text)


class VocabularyTerm(Base):
    __tablename__ = "vocabulary_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(200), unique=True)


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_meetings_status"),
        CheckConstraint(_in("step", STEPS), name="ck_meetings_step"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(32))
    native_id: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id"))
    vexa_meeting_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="bot_sent", server_default="bot_sent")
    step: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    speaker_transcript: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    started_polling_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audio_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Vexa's id for the recording, kept when first seen so cleanup can delete Vexa's copy.
    vexa_recording_id: Mapped[int | None] = mapped_column(Integer)
    # True when some part of the transcript could not be given speaker names.
    speakers_incomplete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    completion_reason: Mapped[str | None] = mapped_column(String(64))
    # When a poll first saw Vexa report the meeting as over.
    terminal_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppState(Base):
    """One row (id 1) of app-wide state. `session_epoch` is bumped to sign everyone out."""

    __tablename__ = "app_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_app_state_single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set by the worker on every pass; the home page warns when it goes stale.
    worker_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
