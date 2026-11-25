# ruff: noqa: F811
"""The meeting picker's route (`app/api_recipe_picker.py`) and what a cross-meeting draft says about
the meetings left out (plan T15). Fixtures are those of `tests/test_api.py` and
`tests/test_recipe_picker.py`."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import teams
from app.models import Series, TeamMember
from app.recipes import service as svc
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    CSRF,
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
from tests.test_recipe_generate import FakeModel, para, reply
from tests.test_team_visibility import outsider, sign_in  # noqa: F401

DAY0 = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
STATUS = SimpleNamespace(
    key="status_report",
    name="Status report",
    instruction="Summarise progress, decisions, blockers and next steps.",
    output_type="document",
    scope="many",
    uses_brief=False,
)
ONE = SimpleNamespace(
    key="follow_up_email",
    name="Follow-up email",
    instruction="Write a friendly follow-up.",
    output_type="email",
    scope="one",
    uses_brief=True,
)
RESOLVE = "/api/recipes/picker/resolve"


@pytest.fixture(autouse=True)
def starters(monkeypatch):
    monkeypatch.setattr(svc, "starter_for", {"status_report": STATUS, "follow_up_email": ONE}.get)


@pytest.fixture
def team(session, owner):
    return teams.ensure_team(session, owner)


@pytest.fixture
def daniel_api(session, owner, team, app):
    user = make_user(session, "daniel@example.test", name="Daniel Okoro")
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return sign_in(app, "daniel@example.test")


@pytest.fixture
def weekly(session, owner, team):
    found = Series(team_id=team.id, owner_user_id=owner.id, title="Monday planning")
    session.add(found)
    session.commit()
    return found


def held(session, template, n, *, series, shared=True, **fields):
    values = {"title": f"Meeting {n}", "created_at": DAY0 + timedelta(days=n)}
    found = ready(session, template, series_id=series.id, **{**values, **fields})
    if shared:
        found.shared_team_id = teams.team_of(session, template.owner_user_id).id
        session.commit()
    return found


def test_a_series_previews_its_meetings_with_title_and_day(
    session, owner, daniel_api, template, weekly
):
    first = held(session, template, 0, series=weekly, title="Kick-off")
    last = held(session, template, 7, series=weekly, title="Review")
    response = daniel_api.post(RESOLVE, {"series_id": weekly.id})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "meetings": [
            {"id": last.id, "title": "Review", "day": "2026-11-09"},
            {"id": first.id, "title": "Kick-off", "day": "2026-11-02"},
        ],
        "left_out_count": 0,
        "left_out_message": None,
        "capped": False,
        "max_meetings": 12,
    }


def test_the_preview_says_how_many_were_left_out_and_never_names_them(
    session, owner, daniel_api, template, weekly
):
    held(session, template, 0, series=weekly, title="Planning")
    hidden = [
        held(session, template, 1, series=weekly, shared=False, title="Salary review SECRET"),
        held(session, template, 2, series=weekly, shared=False, title="Layoffs SECRET"),
    ]
    response = daniel_api.post(RESOLVE, {"series_id": weekly.id})
    body = response.json()
    assert body["left_out_count"] == 2 and len(body["meetings"]) == 1
    assert body["left_out_message"] == (
        "2 meetings were left out because you cannot read them or they are not ready."
    )
    assert "SECRET" not in response.text
    for meeting in hidden:
        assert meeting.id not in [m["id"] for m in body["meetings"]]


def test_one_left_out_meeting_is_worded_in_the_singular(
    session, owner, daniel_api, template, weekly
):
    held(session, template, 0, series=weekly)
    held(session, template, 1, series=weekly, shared=False)
    body = daniel_api.post(RESOLVE, {"series_id": weekly.id}).json()
    assert body["left_out_message"] == (
        "1 meeting was left out because you cannot read it or it is not ready."
    )


def test_the_preview_says_when_the_cap_cut_some(session, owner, daniel_api, template, weekly):
    for n in range(14):
        held(session, template, n, series=weekly)
    body = daniel_api.post(RESOLVE, {"series_id": weekly.id}).json()
    assert len(body["meetings"]) == 12 and body["capped"] is True
    assert body["meetings"][0]["title"] == "Meeting 13"


def test_a_date_range_and_the_hand_picked_way_work_through_the_route(
    session, owner, daniel_api, template, weekly
):
    a = held(session, template, 1, series=weekly)
    b = held(session, template, 20, series=weekly)
    dated = daniel_api.post(RESOLVE, {"from_day": "2026-11-01", "to_day": "2026-11-10"}).json()
    assert [m["id"] for m in dated["meetings"]] == [a.id]
    picked = daniel_api.post(RESOLVE, {"meeting_ids": [a.id, b.id]}).json()
    assert [m["id"] for m in picked["meetings"]] == [b.id, a.id]


def test_the_team_header_narrows_a_date_range_as_the_meetings_list_does(
    session, owner, daniel_api, template, weekly, team
):
    held(session, template, 1, series=weekly)
    other = daniel_api.client.post(
        RESOLVE,
        json={"from_day": "2026-11-01"},
        headers={CSRF: daniel_api.csrf, "X-Team-Id": "999999"},
    )
    error_of(other, 404, "not_found")
    mine = daniel_api.client.post(
        RESOLVE,
        json={"from_day": "2026-11-01"},
        headers={CSRF: daniel_api.csrf, "X-Team-Id": str(team.id)},
    )
    assert mine.status_code == 200 and len(mine.json()["meetings"]) == 1


def test_naming_no_way_or_two_is_refused_in_the_project_shape(api, session):
    error_of(api.post(RESOLVE, {}), 422, "validation_error")
    error_of(api.post(RESOLVE, {"series_id": 1, "project_id": 1}), 422, "validation_error")
    error_of(
        api.post(RESOLVE, {"from_day": "2026-11-09", "to_day": "2026-11-01"}),
        422,
        "invalid_date_range",
    )


def test_a_series_or_project_or_sprint_of_another_team_is_404(
    session, owner, outsider, template, weekly, app
):
    theirs = sign_in(app, outsider.email)
    error_of(theirs.post(RESOLVE, {"series_id": weekly.id}), 404, "not_found")
    error_of(theirs.post(RESOLVE, {"project_id": 1}), 404, "not_found")
    error_of(theirs.post(RESOLVE, {"sprint_id": 1}), 404, "not_found")


def test_the_route_needs_login(anon, session, weekly):
    assert anon.post(RESOLVE, {"series_id": weekly.id}).status_code in (401, 403)


def test_the_route_needs_a_csrf_token(api, session, weekly):
    error_of(api.post(RESOLVE, {"series_id": weekly.id}, csrf=False), 403, "csrf_rejected")


# ---- the draft carries the left-out count ----


def work(session):
    model = FakeModel(reply([para("Progress was steady.")]))
    return svc.process_next(session, datetime.now(UTC), client=model)


def test_a_draft_made_from_a_picker_keeps_the_count_of_meetings_left_out(
    session, owner, daniel_api, template, weekly
):
    shown = held(session, template, 0, series=weekly)
    held(session, template, 1, series=weekly, shared=False)
    held(session, template, 2, series=weekly, shared=False)
    preview = daniel_api.post(RESOLVE, {"series_id": weekly.id}).json()
    response = daniel_api.post(
        "/api/documents",
        {
            "recipe_key": "status_report",
            "meeting_ids": [m["id"] for m in preview["meetings"]],
            "picker": {"series_id": weekly.id},
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["meeting_ids"] == [shown.id]
    assert body["left_out_count"] == 2
    assert body["left_out_message"] == (
        "2 meetings were left out because you cannot read them or they are not ready."
    )
    work(session)
    assert daniel_api.get(f"/api/documents/{body['id']}").json()["left_out_count"] == 2


def test_a_draft_without_a_picker_says_nothing_was_left_out(
    session, owner, daniel_api, template, weekly
):
    shown = held(session, template, 0, series=weekly)
    body = daniel_api.post(
        "/api/documents", {"recipe_key": "status_report", "meeting_ids": [shown.id]}
    ).json()
    assert (body["left_out_count"], body["left_out_message"]) == (0, None)


def test_a_picker_cannot_widen_what_the_draft_reads(session, owner, daniel_api, template, weekly):
    shown = held(session, template, 0, series=weekly)
    hidden = held(session, template, 1, series=weekly, shared=False)
    # naming a meeting the caller cannot read in meeting_ids is still left out, picker or not
    body = daniel_api.post(
        "/api/documents",
        {
            "recipe_key": "status_report",
            "meeting_ids": [shown.id, hidden.id],
            "picker": {"series_id": weekly.id},
        },
    ).json()
    assert body["meeting_ids"] == [shown.id] and body["left_out_count"] == 1


def test_a_picker_for_a_series_of_another_team_is_404(
    session, owner, outsider, template, weekly, app
):
    theirs = sign_in(app, outsider.email)
    mine = ready(session, template)
    error_of(
        theirs.post(
            "/api/documents",
            {"recipe_key": "status_report", "meeting_ids": [mine.id], "picker": {"series_id": 1}},
        ),
        404,
        "not_found",
    )


def test_a_picker_is_for_recipes_that_use_several_meetings(session, owner, api, template, weekly):
    shown = held(session, template, 0, series=weekly)
    error_of(
        api.post(
            "/api/documents",
            {
                "recipe_key": "follow_up_email",
                "meeting_ids": [shown.id],
                "picker": {"series_id": weekly.id},
            },
        ),
        422,
        "meetings_invalid",
    )
    assert date(2026, 11, 2)  # keeps the import used
