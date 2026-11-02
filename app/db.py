from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

__all__ = ["SessionLocal", "get_session", "make_session_factory", "text"]


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


SessionLocal = make_session_factory(create_engine(get_settings().database_url, pool_pre_ping=True))


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
