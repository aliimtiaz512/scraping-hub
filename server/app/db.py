"""SQLAlchemy engine, session factory, and declarative base."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables if they do not exist."""
    # Import every scraper's models so they register on Base.metadata before create_all.
    from app.scrapers.myflorida import models as _myflorida_models  # noqa: F401
    from app.scrapers.ridemetro import models as _ridemetro_models  # noqa: F401
    from app.scrapers.bidnet import models as _bidnet_models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
