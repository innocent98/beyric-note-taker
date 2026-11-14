"""Drive `GET /api/meetings?from=&to=` over real HTTP and save every exchange for the FE guide.

    python scripts/capture_meetings_date_filter.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same set-up as `scripts/capture_meeting_delete.py`: real migrations, the real app under uvicorn,
real HTTP, only Vexa and OpenAI replaced by fakes. Six finished meetings are inserted by SQL with
chosen `created_at` times (the worker is not running), around midnight in Lagos (UTC+1) so the
time-zone rule shows. Files go to `docs/fe-captures/meetings-date-filter/NN-name.json`.

What is NOT real: Vexa and OpenAI (fakes), the worker (it never runs), the meetings (inserted).
"""

import shutil
import sys
from datetime import UTC, datetime

import capture_owner_api as base

base.OUT = base.ROOT / "docs" / "fe-captures" / "meetings-date-filter"
OUT = base.OUT

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting  # noqa: E402
from app.services import get_openai_client, get_vexa  # noqa: E402

Recorder = base.Recorder

# name: (created_at in UTC, what it is in Lagos)
MEETINGS = {
    "a": (datetime(2026, 11, 6, 22, 30, tzinfo=UTC), "Fri 6 Nov 23:30 in Lagos"),
    "b": (datetime(2026, 11, 6, 23, 30, tzinfo=UTC), "Sat 7 Nov 00:30 in Lagos"),
    "c": (datetime(2026, 11, 7, 12, 0, tzinfo=UTC), "Sat 7 Nov 13:00 in Lagos"),
    "d": (datetime(2026, 11, 7, 22, 30, tzinfo=UTC), "Sat 7 Nov 23:30 in Lagos"),
    "e": (datetime(2026, 11, 7, 23, 30, tzinfo=UTC), "Sun 8 Nov 00:30 in Lagos"),
    "f": (datetime(2026, 11, 10, 9, 0, tzinfo=UTC), "Tue 10 Nov 10:00 in Lagos"),
}


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.scratch_url,
    )
    vexa, openai_client = base.FakeVexa(), base.FakeOpenAI()
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: vexa
    app.dependency_overrides[get_openai_client] = lambda: openai_client
    server, server_base = base.start_server(app)
    engine = create_engine(base.scratch_url)
    try:
        run(server_base, engine)
    finally:
        server.should_exit = True
        engine.dispose()


def run(server_base, engine) -> None:
    owner_id = base.owner_id_of(engine)
    owner = Recorder(server_base)
    owner.call("session-anonymous", "GET", "/api/session", note="Read the CSRF token.")
    owner.call("login", "POST", "/api/session", body=base.LOGIN)
    template_id = owner.call("templates", "GET", "/api/templates").json()["templates"][0]["id"]

    ids = {}
    with Session(engine) as session:
        for key, (created, _) in MEETINGS.items():
            row = Meeting(
                owner_user_id=owner_id,
                url=base.MEET,
                platform="google_meet",
                native_id=f"mtg-{key}-{key * 3}"[:12],
                template_id=template_id,
                status="ready",
                step="done",
                title=f"Meeting {key}",
                transcript=base.PLAIN_TRANSCRIPT,
                speaker_transcript=base.SPEAKER_TRANSCRIPT,
                notes=base.NOTES,
                created_at=created,
            )
            session.add(row)
            session.commit()
            ids[key] = row.id
    key_note = "; ".join(f"id {ids[k]} = Meeting {k}, {when}" for k, (_, when) in MEETINGS.items())

    def get(name, query, note):
        return owner.call(name, "GET", f"/api/meetings{query}", note=note)

    get("list-no-filter", "", f"No `from` and `to`: all six, newest first (as before). {key_note}.")
    get(
        "range-utc-no-zone-stored",
        "?from=2026-11-07&to=2026-11-07",
        "No time zone stored yet, so the days are UTC days: 7 Nov UTC holds Meeting c, d and e.",
    )
    get(
        "range-tz-lagos-no-zone-stored",
        "?from=2026-11-07&to=2026-11-07&tz=Africa/Lagos",
        "Still no stored zone, but the page sends its browser's zone as `tz` (the zone its day "
        "headings use): Lagos days, so Meeting b (00:30), c and d (23:30), not e.",
    )
    get(
        "range-tz-new-york-no-zone-stored",
        "?from=2026-11-06&to=2026-11-06&tz=America/New_York",
        "A New York browser (UTC-5): the 6th there runs from 05:00 UTC on the 6th to 05:00 UTC on "
        "the 7th, so Meeting a (17:30) and b (18:30), whose UTC times are 22:30 and 23:30.",
    )
    get(
        "error-unknown-tz",
        "?from=2026-11-07&tz=Mars/Base",
        "A zone the server does not know: 422 `invalid_time_zone`, used or not.",
    )
    owner.call(
        "set-time-zone",
        "PUT",
        "/api/settings/time-zone",
        body={"time_zone": "Africa/Lagos"},
        note="The owner picks Lagos (UTC+1) on Settings.",
    )
    get(
        "range-one-day-lagos",
        "?from=2026-11-07&to=2026-11-07",
        "The same request now means the 7th in Lagos: Meeting b (00:30), c and d (23:30), not e.",
    )
    get(
        "range-stored-zone-wins-over-tz",
        "?from=2026-11-07&to=2026-11-07&tz=America/New_York",
        "Now Lagos is stored, so `tz` is ignored: the same Lagos days as without it.",
    )
    get("range-two-days", "?from=2026-11-07&to=2026-11-08", "Both days are included whole.")
    get("range-only-from", "?from=2026-11-08", "Only `from`: that day and everything after.")
    get("range-only-to", "?to=2026-11-06", "Only `to`: that day and everything before.")
    first = get(
        "range-paging-page-1",
        "?from=2026-11-06&to=2026-11-08&limit=2",
        "Paging inside a range: two of the five in range, `has_more` true, `next_before_id` set.",
    ).json()
    get(
        "range-paging-page-2",
        f"?from=2026-11-06&to=2026-11-08&limit=2&before_id={first['next_before_id']}",
        "The next page of the same range, with the first page's `next_before_id`: two more, and "
        "`has_more` still true (Meeting a is left).",
    )
    get(
        "range-empty",
        "?from=2026-01-01&to=2026-01-31",
        "A range with no meetings: 200 and an empty list, not an error.",
    )
    get("range-blank-values", "?from=&to=", "Empty values are the same as leaving them out.")
    get(
        "error-from-after-to",
        "?from=2026-11-09&to=2026-11-08",
        "`from` after `to`: 422 `invalid_date_range`.",
    )
    get("error-not-a-date", "?from=2026-02-30", "A day that does not exist: 422 `invalid_date`.")
    get("error-wrong-layout", "?to=07/11/2026", "Another layout: 422 `invalid_date`, naming To.")


if __name__ == "__main__":
    main()
