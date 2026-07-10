"""Wisconsin eSupplier persistence: store scraped solicitations in the DB and
build the per-run Excel sheet from the DB (openpyxl)."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.wisconsin.models import EXCEL_COLUMNS, WisconsinBid, WisconsinRun

logger = logging.getLogger(__name__)

# Columns actually present on WisconsinBid (used to filter a scraped record dict).
_BID_FIELDS = {
    "event_number", "solicitation_reference", "event_type", "event_title",
    "agency", "event_status", "due_datetime",
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
    """Upsert the run-level row in wisconsin_runs."""
    values = {
        "run_id": run["run_id"],
        "status": run.get("status"),
        "started_at": _parse_dt(run.get("started_at")),
        "finished_at": _parse_dt(run.get("finished_at")),
        "search": run.get("search"),
        "bids_found": run.get("bids_found", 0),
        "documents_downloaded": run.get("documents_downloaded", 0),
        "folder": run.get("folder"),
        "excel_path": run.get("excel_path"),
    }
    session = SessionLocal()
    try:
        stmt = pg_insert(WisconsinRun).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WisconsinRun.run_id],
            set_={k: v for k, v in values.items() if k != "run_id"},
        )
        session.execute(stmt)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def save_bid(run_id: str, record: dict[str, Any]) -> None:
    """Upsert one solicitation into wisconsin_bids (keyed by run_id + event_number)."""
    values: dict[str, Any] = {k: (record.get(k) or None) for k in _BID_FIELDS}
    values["run_id"] = run_id
    session = SessionLocal()
    try:
        if values.get("event_number"):
            stmt = pg_insert(WisconsinBid).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_wisconsin_run_event",
                set_={k: v for k, v in values.items() if k not in ("run_id", "event_number")},
            )
            session.execute(stmt)
        else:
            session.add(WisconsinBid(**values))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _rows_for_run(run_id: str) -> list[WisconsinBid]:
    session = SessionLocal()
    try:
        return session.execute(
            select(WisconsinBid).where(WisconsinBid.run_id == run_id).order_by(WisconsinBid.id)
        ).scalars().all()
    finally:
        session.close()


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build this run's Excel sheet from wisconsin_bids. Returns the row count."""
    rows = _rows_for_run(run_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Wisconsin Bids"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    for bid in rows:
        sheet.append([getattr(bid, attr, None) for attr, _ in EXCEL_COLUMNS])
    workbook.save(str(out_path))
    logger.info("[run %s] wrote %d rows to %s", run_id, len(rows), out_path)
    return len(rows)


def generate_excel_from_records(records: list[dict[str, Any]], out_path: str | Path) -> int:
    """Build the Excel straight from in-memory records (DB-unavailable fallback)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Wisconsin Bids"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    count = 0
    for record in records:
        if not record.get("event_number"):
            continue
        sheet.append([record.get(attr) for attr, _ in EXCEL_COLUMNS])
        count += 1
    workbook.save(str(out_path))
    return count
