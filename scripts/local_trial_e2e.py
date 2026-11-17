# ruff: noqa: E501
"""Prove the trial and the pilot end locally, end to end, with the real processes (stage S10).

    python scripts/local_trial_e2e.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

It makes a scratch database with the real migrations, then starts the **real web server
(`uvicorn app.main:create_app`) and the real scheduler (`python -m app.scheduler`) as separate
processes**, with the console mailer (every mail the scheduler sends is printed to its log: that
log is the local mail sink). It drives the app over HTTP only, in the order Adebayo switches the
features on, and checks each step, exiting non-zero on the first failure:

 1. trial off: a person signs up and confirms, and gets no hours (the 0049 gap, as it is today);
 2. `TRIAL_SECONDS=10800`, `HOURS_MODE=shadow`: the scheduler's sweep gives that person their
    trial; a fresh sign-up gets theirs at once, from the mailed link; the dry-run scripts run;
 3. recordings are counted while in shadow, then `HOURS_MODE=enforce`: the trial holds only what is
    left, the 80% and 100% notices go out (bell and mail), a new meeting is refused 403;
 4. `PAID_PLANS_OPEN_ON` set so the notice window is open now: the scheduler gives the pilot grant
    its end and sends the one mail, once; then the end itself, with an injected clock.

Every HTTP exchange is saved to `docs/fe-captures/trial/NN-name.json` for the billing guide, and
the switch-on proof commands' output to `switch-on-proof.txt`.

What is real: both processes, the migrations, the routes, the sign-up and the mailed link, the
scheduler's jobs and its mail loop, the grants and the entitlement rule, the database rows.
What is NOT, said in each file's note: Google sign-in (same function, covered by tests); counting
a recording (done by calling `usage.record_usage` in this process, as the pipeline does: no bot, no
audio); the clock (the server cannot be moved in time: the 14th day, the budget window and the
moment the pilot hours end are proved by `tests/test_trial.py` and `tests/test_pilot_expiry.py`
with a fake clock, and the last capture runs the pilot job in this process with an injected clock);
the pilot account's grant is inserted by SQL in the shape migration 0049 makes.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# This process reads the same settings as the servers it starts (the notices it raises by counting
# a recording go through the same mailer and database).
os.environ.update(
    APP_ENV="development",
    SESSION_SECRET="local-e2e-secret-" + "x" * 40,
    COOKIE_SECURE="false",
    PUBLIC_BASE_URL="http://127.0.0.1:8000",
    SCHEDULER_DISPATCH_SECONDS="2",
    SCHEDULER_SYNC_SECONDS="2",
    HOURS_MODE="off",
    TRIAL_SECONDS="",
    PAID_PLANS_OPEN_ON="",
)

import capture_pilot_api as P  # reads sys.argv at import; sets DATABASE_URL, OWNER_* for the run  # noqa: E402

ROOT = P.ROOT
OUT = ROOT / "docs" / "fe-captures" / "trial"
P.OUT = OUT
HOUR = 3600
PASSWORD = "-".join(["chosen", "local", "password", "1"])
PEOPLE = {n: f"{n}@example.test" for n in ("gap", "ada", "paula", "quinn", "rita")}

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import alerts, pilot_expiry, usage  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import HourGrant, Meeting, Notification, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.usage import email_hash  # noqa: E402

Recorder = P.Recorder
LOG_DIR = Path(tempfile.mkdtemp(prefix="trial-e2e-"))
procs: list[subprocess.Popen] = []
checks: list[str] = []


def check(ok: bool, what: str) -> None:
    checks.append(("PASS  " if ok else "FAIL  ") + what)
    print(checks[-1])
    if not ok:
        raise SystemExit(f"FAILED: {what}")


def wait_for(what: str, condition, timeout: float = 120) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return
        time.sleep(1)
    raise SystemExit(f"FAILED: timed out waiting for {what}")


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start(name: str, env: dict, args: list[str]) -> subprocess.Popen:
    log = open(LOG_DIR / f"{name}.log", "a")  # kept across restarts: one running log
    proc = subprocess.Popen(
        [sys.executable, *args],
        env={
            **os.environ,
            **env,
            "PYTHONUNBUFFERED": "1",
        },  # the log is the mail sink: no buffering
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    procs.append(proc)
    return proc


def stop_all() -> None:
    for proc in procs:
        proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    procs.clear()


def launch(env: dict) -> str:
    """Start web and scheduler with `env`, wait until the web answers; return its base URL."""
    stop_all()
    port = free_port()
    start("scheduler", env, ["-m", "app.scheduler"])
    start(
        "web",
        env,
        [
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
    )
    import httpx

    def up() -> bool:
        try:
            return httpx.get(f"http://127.0.0.1:{port}/api/session", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    wait_for("the web server", up, 60)
    return f"http://127.0.0.1:{port}"


def scheduler_log() -> str:
    return (LOG_DIR / "scheduler.log").read_text()


def grants(engine, email: str) -> list[dict]:
    with Session(engine) as s:
        rows = s.scalars(
            select(HourGrant)
            .where(HourGrant.email_hash == email_hash(email))
            .order_by(HourGrant.id)
        ).all()
        return [
            {
                "kind": g.kind,
                "seconds": g.seconds,
                "starts_at": g.starts_at.isoformat(),
                "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            }
            for g in rows
        ]


def sign_up_and_confirm(base: str, engine, who: str) -> None:
    """A real password sign-up: the link comes from the mail the scheduler sends (its log)."""
    email = PEOPLE[who]
    visitor = Recorder(base, f"visitor {who}")
    visitor.call(f"{who}-visitor-session", "GET", "/api/session")
    visitor.call(
        f"{who}-signup",
        "POST",
        "/api/signup",
        body={
            "name": f"{who.title()} Example",
            "email": email,
            "password": PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
        },
        note="202: the confirmation link is mailed (the scheduler's console mailer prints it).",
    )
    wait_for(
        f"the verification mail to {email} in the scheduler's log",
        lambda: f"mail to {email}" in scheduler_log(),
    )
    # the mail row is scrubbed once it is sent: the link comes from the printed mail, as a person's
    # would come from their inbox
    block = scheduler_log().split(f"mail to {email}")[-1]
    token = re.search(r"verify-email\?token=([\w-]+)", block)[1]
    visitor.call(
        f"{who}-verify",
        "POST",
        "/api/verify-email",
        body={"token": token, "password": PASSWORD},
        note="Confirming the email. With `TRIAL_SECONDS` set this is what gives the trial; the "
        'response is the same `{"status": "verified"}` as before the trial existed.',
    )


def signed_in(base: str, who: str) -> Recorder:
    client = Recorder(base, who)
    client.call(f"{who}-session-anonymous", "GET", "/api/session")
    P.login(client, f"{who}-login", PEOPLE[who], PASSWORD)
    return client


def count(engine, settings, email: str, seconds: int, code: str) -> None:
    """Count a recording the way the pipeline does when it is prepared (no bot, no audio)."""
    with Session(engine) as s:
        user = s.query(User).filter_by(email=email).one()
        template = s.query(Template).filter_by(owner_user_id=user.id).first()
        meeting = Meeting(
            owner_user_id=user.id,
            url=f"https://meet.google.com/{code}",
            platform="google_meet",
            native_id=code,
            template_id=template.id,
            status="ready",
        )
        s.add(meeting)
        s.flush()
        usage.record_usage(s, meeting, seconds, settings=settings)
        s.commit()


def template_of(engine, email: str) -> int:
    with Session(engine) as s:
        user = s.query(User).filter_by(email=email).one()
        return s.query(Template).filter_by(owner_user_id=user.id).first().id


def proof(label: str, command: list[str], env: dict, sink: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, *command],
        env={**os.environ, **env},
        cwd=ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    block = f"$ {' '.join(command)}   # {label}\n(exit {result.returncode})\n{result.stdout}{result.stderr}"
    sink.append(block)
    print(block)
    return result.stdout


def add_pilot(engine, who: str) -> None:
    """A pilot account in the shape migration 0049 leaves: confirmed, 2 hours held."""
    with Session(engine) as s:
        user = User(
            email=PEOPLE[who],
            password_hash=hash_password(PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        s.add(user)
        s.flush()
        s.add(Template(owner_user_id=user.id, name="General", instructions="Write notes."))
        s.add(
            HourGrant(
                user_id=user.id,
                email_hash=email_hash(PEOPLE[who]),
                kind="pilot",
                seconds=2 * HOUR,
                starts_at=datetime.now(UTC) - timedelta(days=80),
            )
        )
        s.commit()


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    P.prepare_database()
    for old in OUT.glob("*.json"):
        old.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    engine = create_engine(P.scratch_url)
    today = datetime.now(UTC).date()
    proofs: list[str] = []
    try:
        # ---- 1. trial off: the gap as it is today ----
        env = {}
        base = launch(env)
        admin = Recorder(base, "platform admin")
        admin.call("admin-session-anonymous", "GET", "/api/session")
        P.login(admin, "admin-login", P.ADMIN_EMAIL, P.ADMIN_PASSWORD)
        admin.call(
            "admin-pilot-open",
            "PUT",
            "/api/admin/pilot",
            body={
                "signups_open": True,
                "max_accounts": 10,
                "default_hours_cap_seconds": 18000,
                "password_signup_enabled": True,
            },
            note="Sign-up open for ten accounts (`max_accounts` is still the brake).",
        )
        sign_up_and_confirm(base, engine, "gap")
        check(
            grants(engine, PEOPLE["gap"]) == [], "TRIAL_SECONDS unset: a new account holds no grant"
        )
        proofs.append("")  # spacing
        # ---- 2. trial on, hours rule in shadow ----
        env = {"TRIAL_SECONDS": "10800", "HOURS_MODE": "shadow"}
        proof("what the sweep would do", ["scripts/trial_report.py"], env, proofs)
        base = launch(env)
        wait_for(
            "the sweep to give the earlier account its trial",
            lambda: len(grants(engine, PEOPLE["gap"])) == 1,
        )
        check(
            grants(engine, PEOPLE["gap"])[0]["seconds"] == 3 * HOUR,
            "the scheduler's sweep gave the account made before the switch-on its 3 hours",
        )
        check("trial sweep gave 1 trial" in scheduler_log(), "the scheduler logged the sweep")
        sign_up_and_confirm(base, engine, "ada")
        g = grants(engine, PEOPLE["ada"])
        check(
            len(g) == 1 and g[0]["kind"] == "trial" and g[0]["seconds"] == 3 * HOUR,
            "a fresh sign-up got the trial when it confirmed",
        )
        P.note_file(
            "ada-grants-after-confirming",
            "The `hour_grants` row the confirmation made (read from the database): one `trial` grant "
            "of 3 hours (10800 s) ending 14 days after it was given.",
            {"grants": g},
        )
        ada = signed_in(base, "ada")
        ada.call(
            "ada-session-shadow",
            "GET",
            "/api/session",
            note="`HOURS_MODE=shadow`: the old cap rule decides; the two old fields come from the "
            "old cap and every new field is null, exactly as before.",
        )
        # recordings counted in shadow (nothing is split across grants yet)
        shadow = Settings(
            _env_file=None, app_env="development", hours_mode="shadow", trial_seconds=3 * HOUR
        )
        count(engine, shadow, PEOPLE["ada"], 2 * HOUR, "aaa-aaaa-aaa")
        proof("reconcile, dry run", ["scripts/hours_reconcile_backlog.py"], env, proofs)
        proof("reconcile, keep it", ["scripts/hours_reconcile_backlog.py", "--apply"], env, proofs)
        proof("shadow report", ["scripts/hours_shadow_report.py"], env, proofs)
        # ---- 3. enforce ----
        env = {"TRIAL_SECONDS": "10800", "HOURS_MODE": "enforce"}
        enforce = Settings(
            _env_file=None, app_env="development", hours_mode="enforce", trial_seconds=3 * HOUR
        )
        base = launch(env)
        ada = signed_in(base, "ada")
        ada.call(
            "ada-session-enforce",
            "GET",
            "/api/session",
            note="`HOURS_MODE=enforce` after 2 hours were recorded in shadow: the trial holds only "
            "the 1 hour left (the shadow recordings were settled, not forgotten); 2 of 3 hours is "
            "67%, so `warning` is still null.",
        )
        body = ada.http.get("/api/session").json()["usage"]
        check(
            body["state"] == "trial" and body["seconds_left"] == HOUR,
            "enforce: the trial holds 1 hour (3 h minus the 2 h recorded in shadow)",
        )
        count(engine, enforce, PEOPLE["ada"], 1440, "bbb-bbbb-bbb")  # 2.4 of 3 hours: exactly 80%
        ada.call(
            "ada-session-80-percent",
            "GET",
            "/api/session",
            note="2.4 of the 3 hours used: exactly 80%, `warning` `low`, 0.6 hour left.",
        )
        count(engine, enforce, PEOPLE["ada"], 2160, "ccc-cccc-ccc")  # the last 0.6 hour
        ada.call(
            "ada-session-three-hours-used",
            "GET",
            "/api/session",
            note="Exactly 3 hours used: `state` `none` (a used-up trial shows `none`), `seconds_left` "
            "0, `warning` `out`, `can_record` false; `trial_ends_at` is still set.",
        )
        r = ada.call(
            "ada-start-meeting-trial-used",
            "POST",
            "/api/meetings",
            body={
                "url": "https://meet.google.com/abc-defg-hij",
                "template_id": template_of(engine, PEOPLE["ada"]),
            },
            note="403 `hours_cap_reached` with `reason` `no_hours`: the 3 hours are used.",
        )
        check(
            r.status_code == 403 and r.json()["error"]["reason"] == "no_hours",
            "the 3 hours used: a new meeting is refused 403 no_hours",
        )
        notes = ada.call(
            "ada-notifications", "GET", "/api/notifications", note="The 80% and 100% bell entries."
        )
        kinds = {n["kind"] for n in notes.json()["notifications"]}
        check(
            {"usage.hours_low", "usage.hours_out"} <= kinds,
            "the 80% and 100% notices are in the bell",
        )
        wait_for(
            "the notice mails in the scheduler's log",
            lambda: "You are out of recorded hours" in scheduler_log(),
        )
        check(
            "Most of your recorded hours are used" in scheduler_log(),
            "the 80% mail was sent (printed by the scheduler's mailer)",
        )
        # the person who got a trial from the sweep can use it too
        gap = signed_in(base, "gap")
        gap.call(
            "gap-session-enforce",
            "GET",
            "/api/session",
            note="The account the sweep gave a trial to: 3 hours, `trial`.",
        )
        check(
            gap.http.get("/api/session").json()["usage"]["seconds_left"] == 3 * HOUR,
            "the sweep's trial holds the full 3 hours under enforce",
        )
        # ---- 4. the pilot end ----
        opened = today - timedelta(
            days=50
        )  # the last usable day is today+10: the notice window is open
        env = {
            "TRIAL_SECONDS": "10800",
            "HOURS_MODE": "enforce",
            "PAID_PLANS_OPEN_ON": opened.isoformat(),
        }
        for who in ("paula", "quinn"):
            add_pilot(engine, who)
        proof("what the pilot job would do", ["scripts/trial_report.py"], env, proofs)
        base = launch(env)
        wait_for(
            "the pilot job's mail in the scheduler's log",
            lambda: "Your pilot hours will expire" in scheduler_log(),
        )
        check(
            scheduler_log().count("Your pilot hours will expire") >= 1,
            "the scheduler sent the pilot mail",
        )
        paula_grants = grants(engine, PEOPLE["paula"])
        check(paula_grants[0]["expires_at"] is not None, "the pilot grant was given its end")
        P.note_file(
            "paula-pilot-grants",
            "The pilot grant after the scheduler's job ran: it has the end (the start of the day after the last usable day, UTC).",
            {"grants": paula_grants},
        )
        paula = signed_in(base, "paula")
        n = paula.call(
            "paula-notifications",
            "GET",
            "/api/notifications",
            note="The one bell entry, kind `pilot.expiry`.",
        )
        check(
            sum(1 for x in n.json()["notifications"] if x["kind"] == "pilot.expiry") == 1,
            "exactly one pilot notice in the bell",
        )
        paula.call(
            "paula-session-before-the-end",
            "GET",
            "/api/session",
            note="Before the end nothing about the pilot account changes: `state` `pilot`, 2 hours.",
        )
        time.sleep(8)  # several more scheduler runs; the one mail must stay one
        with Session(engine) as s:
            sent = s.scalars(select(Notification).where(Notification.kind == "pilot.expiry")).all()
            check(
                len(sent) == 2,
                "one notice per pilot person (paula and quinn), none repeated by later runs",
            )
        stop_all()
        # the end itself: the job with an injected clock, the app answering on its real clock
        end = pilot_expiry.end_of(
            Settings(
                _env_file=None, app_env="development", paid_plans_open_on=today - timedelta(days=62)
            )
        )
        ended = Settings(
            _env_file=None,
            app_env="development",
            hours_mode="enforce",
            paid_plans_open_on=today - timedelta(days=62),
        )
        alerts.get_settings = lambda: ended
        add_pilot(engine, "rita")  # nobody has mailed her yet: the job below is her first notice
        with Session(engine) as s:
            result = pilot_expiry.run(s, end - timedelta(days=10), ended)
        P.note_file(
            "rita-pilot-job-injected-clock",
            "The pilot job run in this process with the clock ten days before an end that is already past on the app's own clock (the server cannot be moved in time): `(grants changed, people told)`.",
            {"result": list(result), "end": end.isoformat()},
        )
        base = launch({"TRIAL_SECONDS": "10800", "HOURS_MODE": "enforce"})
        rita = signed_in(base, "rita")
        rita.call(
            "rita-session-after-the-end",
            "GET",
            "/api/session",
            note="The app's clock is now after the pilot end: the pilot hours are not in force. `state` `none`, nothing left, `can_record` false, `cap_seconds` 0.",
        )
        r = rita.call(
            "rita-start-meeting-after-the-end",
            "POST",
            "/api/meetings",
            body={
                "url": "https://meet.google.com/abc-defg-hij",
                "template_id": template_of(engine, PEOPLE["rita"]),
            },
            note="403 `hours_cap_reached` with `reason` `no_plan`: no grant is in force.",
        )
        check(
            r.status_code == 403 and r.json()["error"]["reason"] == "no_plan",
            "after the pilot end a new meeting is refused 403 no_plan",
        )
    finally:
        stop_all()
        engine.dispose()
        (OUT / "switch-on-proof.txt").write_text("\n".join(proofs))
        (OUT / "checks.txt").write_text("\n".join(checks) + "\n")
        sink = re.sub(r"token=[\w-]{20,}", "token=<token>", scheduler_log())
        mails = re.findall(r"--- mail to .*?\n---\n", sink, flags=re.S)
        (OUT / "mail-sink.txt").write_text(
            "Every mail the real scheduler's console mailer printed during the run "
            "(links' tokens masked):\n\n" + "\n".join(mails)
        )
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            text_ = path.read_text()
            for secret in (PASSWORD, P.ADMIN_PASSWORD):
                text_ = text_.replace(secret, "<password placeholder>")
            path.write_text(text_)
        print(f"\nlogs of the two processes: {LOG_DIR}")
    print(f"\nall {len(checks)} checks passed")


if __name__ == "__main__":
    main()
