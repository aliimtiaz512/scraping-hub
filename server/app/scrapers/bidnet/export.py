"""BidNet persistence: store scraped solicitations in the DB and build the
per-run Excel sheet from the DB (openpyxl).

The on-demand export endpoint and the auto-generated run Excel both use the same
EXCEL_COLUMNS mapping, so they always agree.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.bidnet.models import EXCEL_COLUMNS, BidnetBid, BidnetRun

logger = logging.getLogger(__name__)

# Columns actually present on BidnetBid (used to filter a scraped record dict).
_BID_FIELDS = {
    "reference_number", "solicitation_number", "solicitation_type", "title",
    "publication_date", "question_acceptance_deadline", "closing_date",
    "documents_count",
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
    """Upsert the run-level row in bidnet_runs."""
    values = {
        "run_id": run["run_id"],
        "status": run.get("status"),
        "started_at": _parse_dt(run.get("started_at")),
        "finished_at": _parse_dt(run.get("finished_at")),
        "keyword": run.get("keyword"),
        "bids_found": run.get("bids_found", 0),
        "documents_downloaded": run.get("documents_downloaded", 0),
        "folder": run.get("folder"),
        "excel_path": run.get("excel_path"),
    }
    session = SessionLocal()
    try:
        stmt = pg_insert(BidnetRun).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[BidnetRun.run_id],
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
    """Upsert one solicitation into bidnet_bids (keyed by run_id + reference_number)."""
    values: dict[str, Any] = {k: (record.get(k) or None) for k in _BID_FIELDS}
    values["run_id"] = run_id
    session = SessionLocal()
    try:
        if values.get("reference_number"):
            stmt = pg_insert(BidnetBid).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_bidnet_run_ref",
                set_={k: v for k, v in values.items() if k not in ("run_id", "reference_number")},
            )
            session.execute(stmt)
        else:
            session.add(BidnetBid(**values))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _rows_for_run(run_id: str) -> list[BidnetBid]:
    session = SessionLocal()
    try:
        return session.execute(
            select(BidnetBid).where(BidnetBid.run_id == run_id).order_by(BidnetBid.id)
        ).scalars().all()
    finally:
        session.close()


def _all_rows() -> list[BidnetBid]:
    session = SessionLocal()
    try:
        return session.execute(
            select(BidnetBid).order_by(BidnetBid.scraped_at.desc(), BidnetBid.id.desc())
        ).scalars().all()
    finally:
        session.close()


def _write_workbook(rows: list[BidnetBid], out_path: str | Path) -> int:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "BidNet Bids"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    for bid in rows:
        sheet.append([getattr(bid, attr, None) for attr, _ in EXCEL_COLUMNS])
    workbook.save(str(out_path))
    return len(rows)


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build this run's Excel sheet from bidnet_bids. Returns the row count."""
    count = _write_workbook(_rows_for_run(run_id), out_path)
    logger.info("[run %s] wrote %d rows to %s", run_id, count, out_path)
    return count


def generate_excel_from_records(records: list[dict[str, Any]], out_path: str | Path) -> int:
    """Build this run's Excel sheet straight from the in-memory scraped records.

    Used as a fallback when the DB is unavailable, so a run always produces its
    Excel even though nothing could be persisted. Mirrors generate_excel: only
    records that carry a reference number are written (same as what save_bid stores).
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "BidNet Bids"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    count = 0
    for record in records:
        if not record.get("reference_number"):
            continue
        sheet.append([record.get(attr) for attr, _ in EXCEL_COLUMNS])
        count += 1
    workbook.save(str(out_path))
    return count


def export_all_excel(out_path: str | Path) -> int:
    """Build an Excel of every stored solicitation (backs the on-demand export)."""
    return _write_workbook(_all_rows(), out_path)
