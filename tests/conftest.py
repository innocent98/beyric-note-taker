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
