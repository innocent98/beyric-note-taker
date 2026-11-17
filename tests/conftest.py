import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/notetaker_test",
)


@pytest.fixture
def engine():
    from app.models import Base

    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Recording hosts resolve to a public address in tests: the download refuses a host that does
    not, and the fake hosts have no DNS."""
    monkeypatch.setattr("app.skribby._resolve_host", lambda host: ["93.184.216.34"])


@pytest.fixture
def vault_key(monkeypatch):
    """The saved-card vault has a key (the subscription tests keep card tokens encrypted)."""
    import base64

    from app import vault
    from tests.test_web import make_settings

    key = base64.b64encode(b"K" * 32).decode()
    monkeypatch.setattr(vault, "get_settings", lambda: make_settings(token_enc_key=key))


@pytest.fixture
def providers(monkeypatch):
    """`providers.usd` answers for `flutterwave` (dollars) and `providers.ngn` for `paystack`:
    fake providers named after the real ones the route table picks, nobody else registered."""
    from app.billing.fake import FakeProvider
    from app.billing.provider import PROVIDER_FACTORIES

    class Both:
        usd = FakeProvider(name="flutterwave")
        ngn = FakeProvider(name="paystack")

    monkeypatch.setitem(PROVIDER_FACTORIES, "flutterwave", lambda s: Both.usd)
    monkeypatch.setitem(PROVIDER_FACTORIES, "paystack", lambda s: Both.ngn)
    return Both
