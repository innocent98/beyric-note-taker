"""The recipe library and team brief routes of meeting recipes (plan T10 and T11). Included into
`app.api.router`, so they share its `/api` prefix, login check, CSRF rule and error shape.

The list is the eighteen starters (code, `app/recipes/library.py`), then the active team's recipes,
then the caller's own. A person's own recipe is private: another person's is a 404, never a 403. A
team recipe is written (created, edited, archived) by the team's owners and admins only
(`team_admin.require_manager`, the Team screen's rule): a member reads and uses it, and a write by
a member is 403 `team_forbidden`. The team is the `X-Team-Id` header, else the caller's default
team. Deleting a recipe archives it: the drafts made from it keep working.

The brief is the active team's one reference text: everyone on the team reads it, owners and admins
write it. `POST /api/briefs/import` turns a file into text for the person to review: it saves
nothing and never stores the file (the upload is read in memory; see `read_upload`).
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StringConstraints, ValidationInfo, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, services, team_admin, teams, titles
from app.api_schemas import ErrorResponse
from app.config import get_settings
from app.db import get_session
from app.models import RECIPE_OUTPUT_TYPES, RECIPE_SCOPES, Recipe, Team, TeamMember, User
from app.recipes import briefs, importer, library

router = APIRouter()

NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such recipe (or not yours)"}}
FORBIDDEN = {
    403: {
        "model": ErrorResponse,
        "description": "`team_forbidden`: a team recipe is written by owners and admins only",
    }
}

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=library.MAX_NAME_CHARS)
]
Instruction = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=library.MAX_INSTRUCTION_CHARS
    ),
]
OutputType = Literal["document", "email", "table", "board"]
Scope = Literal["one", "many"]

assert OutputType.__args__ == RECIPE_OUTPUT_TYPES and Scope.__args__ == RECIPE_SCOPES


class OptionDef(BaseModel):
    """One choice the Make dialog draws for a recipe. `choices` is empty and `default` null for
    kind `text`."""

    label: str
    kind: Literal["text", "choice"]
    choices: list[str] = []
    default: str | None = None


def _clean_options(options: dict[str, OptionDef]) -> dict:
    try:
        return library.clean_options(
            {key: item.model_dump(exclude_none=True) for key, item in options.items()}
        )
    except ValueError as exc:
        raise services.Refused(422, "options_invalid", str(exc)) from exc


class RecipeIn(BaseModel):
    """A new recipe. With `key` (a starter's key) the fields left out are taken from that starter:
    this is how a starter is copied. Without it, `name` and `instruction` are needed."""

    key: str | None = Field(None, description="The starter this is a copy of")
    name: Name | None = None
    instruction: Instruction | None = None
    output_type: OutputType | None = None
    options: dict[str, OptionDef] | None = None
    scope: Scope | None = None
    uses_brief: bool | None = None
    visibility: Literal["personal", "team"] = Field(
        "personal", description="`team`: the active team's recipe (owners and admins only)"
    )

    @field_validator("name", "instruction", "output_type", mode="before")
    @classmethod
    def _not_null(cls, value, info: ValidationInfo):
        if value is None:
            raise ValueError(f"{info.field_name} may be left out but not null")
        return value


class RecipePatch(BaseModel):
    """Change a recipe: only the fields sent change. Who owns it (personal or team) never does."""

    name: Name | None = None
    instruction: Instruction | None = None
    output_type: OutputType | None = None
    options: dict[str, OptionDef] | None = None
    scope: Scope | None = None
    uses_brief: bool | None = None

    @field_validator("name", "instruction", "output_type", "scope", "uses_brief", mode="before")
    @classmethod
    def _not_null(cls, value, info: ValidationInfo):
        if value is None:
            raise ValueError(f"{info.field_name} may be left out but not null")
        return value


class RecipeOut(BaseModel):
    id: int | None = Field(description="Null for a starter: name it by `key`")
    key: str | None = Field(description="The starter's key, or the starter a copy came from")
    pack: Literal["general", "school"] | None = Field(
        description="A starter's pack; null for a recipe someone wrote"
    )
    source: Literal["starter", "personal", "team"]
    name: str
    instruction: str
    output_type: OutputType
    options: dict[str, OptionDef]
    scope: Scope
    uses_brief: bool
    can_edit: bool = Field(description="False for a starter, and for a team recipe to a member")
    updated_at: datetime | None


class RecipeTeam(BaseModel):
    id: int
    name: str
    is_personal: bool
    can_manage: bool = Field(description="Owner or admin of the team: may write its recipes")


class RecipeList(BaseModel):
    recipes: list[RecipeOut]
    team: RecipeTeam = Field(description="The active team: whose recipes and brief these are")


def _now() -> datetime:
    return datetime.now(UTC)


def _starter_out(s: library.Starter) -> RecipeOut:
    return RecipeOut(
        id=None,
        key=s.key,
        pack=s.pack,
        source="starter",
        name=s.name,
        instruction=s.instruction,
        output_type=s.output_type,
        options={k: OptionDef(**v) for k, v in s.options.items()},
        scope=s.scope,
        uses_brief=s.uses_brief,
        can_edit=False,
        updated_at=None,
    )


def _row_out(row: Recipe, *, can_edit: bool) -> RecipeOut:
    return RecipeOut(
        id=row.id,
        key=row.key,
        pack=None,
        source="team" if row.team_id is not None else "personal",
        name=row.name,
        instruction=row.instruction,
        output_type=row.output_type,
        options={k: OptionDef(**v) for k, v in (row.options or {}).items()},
        scope=row.scope,
        uses_brief=row.uses_brief,
        can_edit=can_edit,
        updated_at=row.updated_at,
    )


def _can_write(row: Recipe, me: TeamMember | None) -> bool:
    if row.team_id is None:
        return True
    return me is not None and me.role in team_admin.MANAGER_ROLES


def team_out(session: Session, user: User, me: TeamMember) -> RecipeTeam:
    team = session.get(Team, me.team_id)
    return RecipeTeam(
        id=team.id,
        name=team.name,
        is_personal=teams.is_personal_of(team, user.id),
        can_manage=me.role in team_admin.MANAGER_ROLES,
    )


def _live_count(session: Session, user: User, team_id: int | None) -> int:
    where = (
        [Recipe.team_id == team_id]
        if team_id is not None
        else [Recipe.team_id.is_(None), Recipe.owner_user_id == user.id]
    )
    return session.scalar(
        select(func.count()).select_from(Recipe).where(Recipe.archived_at.is_(None), *where)
    )


def _row_of_mine(session: Session, user: User, recipe_id: int) -> tuple[Recipe, TeamMember | None]:
    """A live recipe the caller may see (their own, or one of a team they are on) and their
    membership of its team. Anything else is a 404, as for any recipe that is not theirs."""
    row = None
    if 1 <= recipe_id <= services.MAX_ID:
        row = session.scalars(
            select(Recipe).where(Recipe.id == recipe_id, Recipe.archived_at.is_(None))
        ).first()
    if row is None:
        raise services.Refused(404, "not_found", "No such recipe.")
    if row.team_id is None:
        if row.owner_user_id != user.id:
            raise services.Refused(404, "not_found", "No such recipe.")
        return row, None
    me = teams.membership(session, user.id, row.team_id)
    if me is None:
        raise services.Refused(404, "not_found", "No such recipe.")
    return row, me


@router.get("/recipes", tags=["recipes"])
def list_recipes(
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> RecipeList:
    """The starters (General and School), then the active team's recipes, then the caller's own.
    `X-Team-Id` names the team (404 for one the caller is not on), else their default team.
    Archived recipes are not listed."""
    me = team_admin.my_membership(session, user, team_id)
    rows = session.scalars(
        select(Recipe)
        .where(
            Recipe.archived_at.is_(None),
            (Recipe.team_id == me.team_id)
            | (Recipe.team_id.is_(None) & (Recipe.owner_user_id == user.id)),
        )
        .order_by(Recipe.id)
    ).all()
    team_rows = [r for r in rows if r.team_id is not None]
    own_rows = [r for r in rows if r.team_id is None]
    return RecipeList(
        recipes=[
            *(_starter_out(s) for s in library.STARTERS),
            *(_row_out(r, can_edit=_can_write(r, me)) for r in team_rows),
            *(_row_out(r, can_edit=True) for r in own_rows),
        ],
        team=team_out(session, user, me),
    )


@router.post("/recipes", tags=["recipes"], status_code=201, responses={**FORBIDDEN, **NOT_FOUND})
def create_recipe(
    body: RecipeIn,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> RecipeOut:
    """Save a recipe: the caller's own, or (`visibility: team`, owners and admins only, 403
    `team_forbidden`) the active team's. Give `key` to copy a starter: whatever you leave out comes
    from it. 422 `too_many_recipes` past 100 live ones."""
    me = team_admin.my_membership(session, user, team_id)
    if body.visibility == "team":
        team_admin.require_manager(me)
    starter = None
    if body.key is not None:
        starter = library.starter_by_key(body.key)
        if starter is None:
            raise services.Refused(404, "not_found", "No such starter recipe.")
    name = body.name if body.name is not None else starter.name if starter else None
    instruction = (
        body.instruction
        if body.instruction is not None
        else starter.instruction
        if starter
        else None
    )
    if name is None or instruction is None:
        raise services.Refused(422, "recipe_invalid", "A recipe needs a name and an instruction.")
    owner_team = me.team_id if body.visibility == "team" else None
    if _live_count(session, user, owner_team) >= library.MAX_RECIPES:
        raise services.Refused(
            422, "too_many_recipes", f"Keep at most {library.MAX_RECIPES} recipes; archive one."
        )
    options = (
        _clean_options(body.options)
        if body.options is not None
        else dict(starter.options)
        if starter
        else {}
    )
    row = Recipe(
        owner_user_id=user.id,
        team_id=owner_team,
        key=starter.key if starter else None,
        name=name,
        instruction=instruction,
        output_type=body.output_type or (starter.output_type if starter else "document"),
        options=options,
        scope=body.scope or (starter.scope if starter else "one"),
        uses_brief=(
            body.uses_brief
            if body.uses_brief is not None
            else starter.uses_brief
            if starter
            else True
        ),
    )
    session.add(row)
    session.commit()
    return _row_out(row, can_edit=True)


@router.put("/recipes/{recipe_id}", tags=["recipes"], responses={**FORBIDDEN, **NOT_FOUND})
def update_recipe(
    recipe_id: int,
    body: RecipePatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> RecipeOut:
    """Change a recipe of the caller's own, or of a team they own or administer (403
    `team_forbidden` for a member, who may still copy its text into a recipe of their own). Only
    the fields sent change; a copy of a starter stays marked as one."""
    row, me = _row_of_mine(session, user, recipe_id)
    if row.team_id is not None:
        team_admin.require_manager(me)
    sent = body.model_fields_set
    for field in ("name", "instruction", "output_type", "scope", "uses_brief"):
        if field in sent:
            setattr(row, field, getattr(body, field))
    if "options" in sent and body.options is not None:
        row.options = _clean_options(body.options)
    row.updated_at = _now()
    session.commit()
    return _row_out(row, can_edit=True)


@router.delete(
    "/recipes/{recipe_id}", tags=["recipes"], status_code=204, responses={**FORBIDDEN, **NOT_FOUND}
)
def archive_recipe(
    recipe_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Archive a recipe (same people as `PUT`): it leaves the list and cannot be used again, and
    the drafts made from it are untouched."""
    row, me = _row_of_mine(session, user, recipe_id)
    if row.team_id is not None:
        team_admin.require_manager(me)
    row.archived_at = _now()
    session.commit()
    return Response(status_code=204)


# ---- the team brief ----


class BriefIn(BaseModel):
    text: str = Field(description="The whole brief; an empty text clears it")


class BriefOut(BaseModel):
    team: RecipeTeam = Field(description="Whose brief this is: the active team")
    text: str
    max_chars: int
    updated_at: datetime | None
    updated_by: str | None = Field(description="Who saved it last; null when none was saved")


def _brief_out(session: Session, user: User, me: TeamMember) -> BriefOut:
    found = briefs.read(session, me.team_id)
    author = (
        session.get(User, found.updated_by_user_id)
        if found is not None and found.updated_by_user_id
        else None
    )
    return BriefOut(
        team=team_out(session, user, me),
        text=found.text if found is not None else "",
        max_chars=briefs.max_chars(),
        updated_at=found.updated_at if found is not None else None,
        updated_by=(titles.clean_title(author.name, 100) or author.email.split("@")[0])
        if author is not None
        else None,
    )


@router.get("/briefs", tags=["recipes"])
def read_brief(
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> BriefOut:
    """The active team's brief (`text` is empty when none was saved). Members read it too: its
    prices and terms appear in the drafts they make. `X-Team-Id` names the team (404 for one the
    caller is not on)."""
    return _brief_out(session, user, team_admin.my_membership(session, user, team_id))


@router.put("/briefs", tags=["recipes"], responses=FORBIDDEN)
def write_brief(
    body: BriefIn,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> BriefOut:
    """Replace the active team's brief (owners and admins; 403 `team_forbidden` for a member).
    422 `brief_too_long` over `max_chars`. The text is data: stored as typed, minus control
    characters, and shown to the model only inside a fence."""
    me = team_admin.my_membership(session, user, team_id)
    team_admin.require_manager(me)
    briefs.save(session, me, user, body.text)
    session.commit()
    return _brief_out(session, user, me)


class BriefImported(BaseModel):
    text: str = Field(description="Review and edit it, then save it with `PUT /api/briefs`")
    truncated: bool = Field(description="The file held more than a brief may: the rest is cut")
    pages: int | None = Field(description="A PDF's page count; null for other files")


IMPORT_ERRORS = {
    "too_big": 413,
    "too_many_pages": 422,
    "unsupported": 422,
    "no_text_layer": 422,
    "unreadable": 422,
    "busy": 503,
}
ENVELOPE_BYTES = 64 * 1024  # the multipart boundaries and headers around the file


def _refused(exc: importer.ImportRefused) -> services.Refused:
    code = "import_busy" if exc.code == "busy" else exc.code
    return services.Refused(IMPORT_ERRORS[exc.code], code, str(exc))


async def _read_body(request: Request, limit: int) -> bytes:
    """The request body, in memory, stopping as soon as it is over `limit`."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _refused(importer.ImportRefused("too_big"))
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _refused(importer.ImportRefused("too_big"))
        chunks.append(chunk)
    return b"".join(chunks)


def read_upload(content_type: str, body: bytes) -> tuple[str, bytes] | None:
    """The `file` part of a multipart body as (filename, bytes), read in memory. Starlette's own
    form parsing would spill a file over 1 MB into a temporary file on disk; a brief file is
    never written anywhere, so this reads the body here. None when there is no such part."""
    from python_multipart.multipart import MultipartParser, parse_options_header

    kind, options = parse_options_header(content_type.encode())
    boundary = options.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        return None
    found: dict[str, object] = {}
    state: dict[str, object] = {"headers": {}, "field": b"", "value": b"", "data": []}

    def on_part_begin() -> None:
        state.update(headers={}, field=b"", value=b"", data=[])

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        state["headers"][state["field"].lower()] = state["value"]
        state["field"] = b""
        state["value"] = b""

    def on_part_data(data: bytes, start: int, end: int) -> None:
        state["data"].append(data[start:end])

    def on_part_end() -> None:
        disposition = state["headers"].get(b"content-disposition", b"")
        _, params = parse_options_header(disposition)
        if params.get(b"name") == b"file" and "file" not in found:
            name = params.get(b"filename", b"").decode("utf-8", errors="replace")
            found["file"] = (name, b"".join(state["data"]))

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    try:
        parser.write(body)
        parser.finalize()
    except Exception:  # a malformed body: nothing usable in it
        return None
    return found.get("file")  # type: ignore[return-value]


_UPLOAD_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }
        }
    },
}


@router.post(
    "/briefs/import",
    tags=["recipes"],
    responses={
        **FORBIDDEN,
        413: {"model": ErrorResponse, "description": "`too_big`"},
        422: {
            "model": ErrorResponse,
            "description": "`file_missing`, `unsupported`, `too_many_pages`, `no_text_layer` "
            "(a scanned PDF) or `unreadable`",
        },
        503: {"model": ErrorResponse, "description": "`import_busy`: try again in a moment"},
    },
    openapi_extra={"requestBody": _UPLOAD_BODY},
)
async def import_brief(
    request: Request,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> BriefImported:
    """Read a `.docx`, `.pdf`, `.txt` or `.md` file (multipart, field `file`) into text for the
    brief. The text comes back for the person to review and edit: **nothing is saved** and the file
    is never stored (not on disk, not in the database). Owners and admins only (the people who can
    save a brief). The text is data: if saved, the model sees it inside a fence."""

    def check() -> None:
        team_admin.require_manager(team_admin.my_membership(session, user, team_id))

    await run_in_threadpool(check)
    limit = get_settings().import_max_bytes + ENVELOPE_BYTES
    body = await _read_body(request, limit)
    upload = read_upload(request.headers.get("content-type", ""), body)
    if upload is None:
        raise services.Refused(
            422, "file_missing", "Send the file as a multipart form field named `file`."
        )
    name, data = upload
    try:
        found = await run_in_threadpool(importer.extract_text, data, name)
    except importer.ImportRefused as exc:
        raise _refused(exc) from exc
    return BriefImported(text=found.text, truncated=found.truncated, pages=found.pages)
