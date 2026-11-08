from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

__all__ = [
    "SessionLocal",
    "get_rate_session",
    "get_session",
    "make_engine",
    "make_session_factory",
    "text",
]


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def make_engine(url: str, **options) -> Engine:
    """An engine that never puts statement parameters in an error message: a failed query is logged
    with its SQL text only. Parameters can hold what a person typed (Ask Beyric's search words)."""
    return create_engine(url, pool_pre_ping=True, hide_parameters=True, **options)


SessionLocal = make_session_factory(make_engine(get_settings().database_url))


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


# The rate limiter's sessions (`app/ratelimit.py`) come from a pool of their own. A limiter call is
# made while a request still holds its own connection (sign-up counts a mail inside its
# transaction): from the same pool, enough requests at once would all hold one connection and wait
# for a second. A separate small pool cannot be starved by requests, and its calls never nest.
RateSessionLocal = make_session_factory(
    make_engine(get_settings().database_url, pool_size=5, max_overflow=5)
)


def get_rate_session() -> Iterator[Session]:
    with RateSessionLocal() as session:
        yield session
