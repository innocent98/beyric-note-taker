"""What a person chooses when they pick the meetings of a cross-meeting draft, and the sentence that
says how many were left out. A leaf module (pydantic only) so `app/api_schemas.py` can use it
without importing the services; the resolving is `app/recipes/picker.py`."""

from datetime import date

from pydantic import BaseModel, Field, model_validator

MAX_MEETINGS = 12
MAX_HAND_PICKED = 100
_MAX_ID = 2**31 - 1  # ids are Postgres integers (`services.MAX_ID`)


class PickerSpec(BaseModel):
    """Which meetings to use. Exactly one of: `series_id` (a recurring meeting), `project_id`,
    `sprint_id`, a date range (`from_day` and/or `to_day`, `YYYY-MM-DD`, both days included, in the
    caller's time zone) or `meeting_ids` (hand-picked). At most 12 are used, newest first; a meeting
    the caller cannot read is left out and counted."""

    series_id: int | None = Field(default=None, ge=1, le=_MAX_ID)
    project_id: int | None = Field(default=None, ge=1, le=_MAX_ID)
    sprint_id: int | None = Field(default=None, ge=1, le=_MAX_ID)
    from_day: date | None = None
    to_day: date | None = None
    meeting_ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_HAND_PICKED)
    # The zone for the days when the caller has none stored (the browser's), as `GET /api/meetings`.
    tz: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _one_way(self) -> "PickerSpec":
        ways = [
            self.series_id is not None,
            self.project_id is not None,
            self.sprint_id is not None,
            self.from_day is not None or self.to_day is not None,
            self.meeting_ids is not None,
        ]
        if sum(ways) != 1:
            raise ValueError(
                "Choose exactly one of series_id, project_id, sprint_id, a date range or "
                "meeting_ids."
            )
        if self.meeting_ids is not None and not all(1 <= i <= _MAX_ID for i in self.meeting_ids):
            raise ValueError("meeting_ids must be valid meeting ids.")
        return self


def left_out_sentence(count: int) -> str | None:
    """The plain sentence for `count` meetings left out (null for none). It never names one."""
    if count <= 0:
        return None
    if count == 1:
        return "1 meeting was left out because you cannot read it or it is not ready."
    return f"{count} meetings were left out because you cannot read them or they are not ready."
