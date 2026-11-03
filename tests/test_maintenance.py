"""The hourly sweeps: stale sign-in flows, spent tokens, old notifications, unverified accounts and
the mail outbox."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import mail, maintenance
from app.models import AuthToken, MailOutbox, Notification, OAuthFlow, User
from tests.test_web import make_user, owner  # noqa: F401  (fixture used by name)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def token(session, **fields) -> AuthToken:
    row = AuthToken(
        email="a@example.test",
        purpose="verify_email",
        token_hash=f"{len(session.scalars(select(AuthToken.id)).all()):064d}",
        expires_at=NOW + timedelta(hours=1),
        **fields,
    )
    session.add(row)
    session.commit()
    return row


def test_expired_sign_in_flows_are_deleted_and_live_ones_stay(session):
    for name, expires in (
        ("old", NOW - timedelta(minutes=1)),
        ("live", NOW + timedelta(minutes=5)),
    ):
        session.add(
            OAuthFlow(
                state_hash=name[0] * 64,
                purpose="signin",
                code_verifier="v",
                nonce="n",
                expires_at=expires,
            )
        )
    session.commit()
    maintenance.run_hourly(session, NOW)
    assert [f.state_hash for f in session.scalars(select(OAuthFlow))] == ["l" * 64]


def test_spent_and_long_expired_tokens_are_deleted_and_live_ones_stay(session):
    live = token(session)
    token(session, used_at=NOW - timedelta(days=8))
    used_recently = token(session, used_at=NOW - timedelta(days=1))
    expired_long_ago = token(session)
    expired_long_ago.expires_at = NOW - timedelta(days=8)
    just_expired = token(session)
    just_expired.expires_at = NOW - timedelta(hours=1)
    session.commit()
    kept = {live.id, used_recently.id, just_expired.id}
    maintenance.run_hourly(session, NOW)
    assert {t.id for t in session.scalars(select(AuthToken))} == kept


def test_notifications_are_kept_90_days(session, owner):
    for n, age in enumerate((91, 89)):
        session.add(
            Notification(
                user_id=owner.id,
                kind="k",
                title=f"t{n}",
                dedupe_key=f"k{n}",
                created_at=NOW - timedelta(days=age),
            )
        )
    session.commit()
    maintenance.run_hourly(session, NOW)
    assert [n.title for n in session.scalars(select(Notification))] == ["t1"]


def test_unverified_accounts_and_the_outbox_are_swept_too(session):
    old = make_user(session, "old@example.test", email_verified_at=None)
    old.created_at = NOW - timedelta(hours=49)
    row = mail.enqueue(session, "x@example.test", "verify_email", {"link": "l"})
    row.created_at = NOW - timedelta(hours=25)
    session.commit()
    maintenance.run_hourly(session, NOW)
    session.expire_all()
    assert session.scalars(select(User).where(User.email == "old@example.test")).first() is None
    assert session.scalars(select(MailOutbox)).one().status == "failed"


def test_one_failing_hourly_part_does_not_stop_the_others(session, monkeypatch, caplog):
    from app import accounts

    row = mail.enqueue(session, "x@example.test", "verify_email", {"link": "https://x/?token=T"})
    row.created_at = NOW - timedelta(hours=3)
    session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("secret detail ada@example.test")

    monkeypatch.setattr(accounts, "sweep_unverified", boom)
    caplog.set_level("WARNING")
    maintenance.run_hourly(session, NOW)  # must not raise
    session.expire_all()
    assert session.scalars(select(MailOutbox)).one().status == "failed"  # the outbox sweep ran
    ours = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in ours and "secret detail" not in ours and "ada@example.test" not in ours


def test_the_worker_runs_the_maintenance_even_when_the_audio_cleanup_always_fails(
    session, monkeypatch, caplog
):
    from app import worker

    ran = []
    monkeypatch.setattr(worker, "delete_expired_audio", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(maintenance, "run_hourly", lambda s, now: ran.append(now))
    caplog.set_level("WARNING")
    worker.hourly_jobs(session, NOW, vexa=object())  # must not raise
    assert ran == [NOW]
    assert "ZeroDivisionError" in " ".join(r.getMessage() for r in caplog.records)
