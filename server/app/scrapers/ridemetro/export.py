"""RideMetro persistence: store scraped opportunities in the DB and build the
per-run Excel sheet *from* the DB (openpyxl).

The RideMetro portal has no Excel export, so the data path is
scrape -> ridemetro_bids -> generated Excel.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.ridemetro.models import EXCEL_COLUMNS, RideMetroBid, RideMetroRun

logger = logging.getLogger(__name__)

# Columns actually present on RideMetroBid (used to filter a scraped detail dict).
_BID_FIELDS = {
    "project", "ref_number", "department", "opportunity_type", "status",
    "open_date", "intent_to_bid_due_date", "question_due_date", "close_date",
    "days_left", "contact_information", "project_description",
}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def save_run(run: dict[str, Any]) -> None:
    """Upsert the run-level row in ridemetro_runs."""
    values = {
        "run_id": run["run_id"],
        "status": run.get("status"),
        "started_at": _parse_dt(run.get("started_at")),
        "finished_at": _parse_dt(run.get("finished_at")),
        "opportunities_found": run.get("bids_found", 0),
        "documents_downloaded": run.get("documents_downloaded", 0),
        "folder": run.get("folder"),
        "excel_path": run.get("excel_path"),
    }
    session = SessionLocal()
    try:
        stmt = pg_insert(RideMetroRun).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[RideMetroRun.run_id],
            set_={k: v for k, v in values.items() if k != "run_id"},
        )
        session.execute(stmt)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def save_bid(run_id: str, details: dict[str, Any], opportunity_url: str | None, zip_filename: str | None) -> None:
    """Upsert one opportunity into ridemetro_bids (keyed by run_id + ref_number)."""
    values: dict[str, Any] = {k: details.get(k) for k in _BID_FIELDS}
    values.update(
        run_id=run_id,
        opportunity_url=opportunity_url,
        zip_filename=zip_filename,
        raw_data=details.get("raw_data", {}),
    )
    session = SessionLocal()
    try:
        if values.get("ref_number"):
            stmt = pg_insert(RideMetroBid).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_ridemetro_run_ref",
                set_={k: v for k, v in values.items() if k not in ("run_id", "ref_number")},
            )
            session.execute(stmt)
        else:
            session.add(RideMetroBid(**values))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build the run's Excel sheet from ridemetro_bids. Returns the row count."""
    session = SessionLocal()
    try:
        rows = session.execute(
            select(RideMetroBid).where(RideMetroBid.run_id == run_id).order_by(RideMetroBid.id)
        ).scalars().all()
    finally:
        session.close()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "RideMetro Bids"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    for bid in rows:
        sheet.append([getattr(bid, attr, None) for attr, _ in EXCEL_COLUMNS])
    workbook.save(str(out_path))
    logger.info("[run %s] wrote %d rows to %s", run_id, len(rows), out_path)
    return len(rows)
