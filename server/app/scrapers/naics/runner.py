"""Background-task runner for the vendored NAICS engine.

The vendored ``NaicsCodeScraper`` fetches the NAICS index page and yields every
6-digit code via its ``_on_code_scraped`` callback. This runner collects them and
upserts the whole set into the naics_codes reference table in one transaction.
"""

import logging
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import run_manager
from app.db import SessionLocal
from app.scrapers.naics.models import NaicsCode
from app.scrapers.naics.engine.naics_scraper import NaicsCodeScraper

logger = logging.getLogger(__name__)


def _upsert_codes(codes: list[dict]) -> int:
    """Upsert scraped {code, title} rows into naics_codes (dedup by code)."""
    if not codes:
        return 0
    session = SessionLocal()
    try:
        seen: set[str] = set()
        stored = 0
        for item in codes:
            code = (item.get("code") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            stmt = pg_insert(NaicsCode).values(code=code, title=item.get("title") or "")
            stmt = stmt.on_conflict_do_update(index_elements=[NaicsCode.code], set_={"title": stmt.excluded.title})
            session.execute(stmt)
            stored += 1
        session.commit()
        return stored
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def execute_run(run_id: str) -> None:
    run_manager.update_run(run_id, status="running", step="fetching_naics")
    collected: list[dict] = []
    try:
        scraper = NaicsCodeScraper()
        scraper._on_code_scraped = lambda item: collected.append(item)
        scraper.run()

        run_manager.update_run(run_id, bids_found=len(collected), bids_processed=len(collected))
        if not collected:
            run_manager.update_run(run_id, no_results=True)

        run_manager.update_run(run_id, step="saving")
        try:
            stored = _upsert_codes(collected)
            run_manager.update_run(run_id, bids_stored_in_db=stored)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] NAICS DB save failed", run_id)
            run_manager.add_error(run_id, "db save failed (see logs)")

        run_manager.update_run(run_id, status="completed", step="done")
    except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
        logger.exception("[run %s] NAICS run failed", run_id)
        run_manager.add_error(run_id, str(exc)[:500])
        run_manager.update_run(run_id, status="failed", step="failed")
    finally:
        run_manager.update_run(run_id, finished_at=datetime.now().isoformat())
