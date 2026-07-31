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
    # Import every model module so it registers on Base.metadata before create_all.
    from app.core import models as _core_models  # noqa: F401 — shared run_state table
    from app.scrapers.myflorida import models as _myflorida_models  # noqa: F401
    from app.scrapers.myflorida.sweep import models as _myflorida_sweep_models  # noqa: F401
    from app.scrapers.ridemetro import models as _ridemetro_models  # noqa: F401
    from app.scrapers.bidnet import models as _bidnet_models  # noqa: F401
    from app.scrapers.bidnet import niche_models as _bidnet_niche_models  # noqa: F401
    from app.scrapers.wisconsin import models as _wisconsin_models  # noqa: F401
    from app.scrapers.northdakota import models as _northdakota_models  # noqa: F401
    from app.scrapers.septa import models as _septa_models  # noqa: F401
    from app.scrapers.septa import niche_models as _septa_niche_models  # noqa: F401
    from app.scrapers.evalconfig import models as _evalconfig_models  # noqa: F401
    from app.scrapers.sam import models as _sam_models  # noqa: F401
    from app.scrapers.unison import models as _unison_models  # noqa: F401
    from app.scrapers.naics import models as _naics_models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # Materialise the SEPTA niche catalog (keywords + commodity codes) from its
    # source file into the tables the API and scraper read. Best-effort: a bad
    # catalog must not stop the API from starting.
    from app.scrapers.septa.niches import seed_niches

    seed_niches()

    # Same for the BidNet niche catalog: a run selects one niche and the
    # scraper resolves its keywords from these tables.
    from app.scrapers.bidnet.niches import seed_niches as seed_bidnet_niches

    seed_bidnet_niches()


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
