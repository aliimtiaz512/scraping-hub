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
    "documents_count", "matched_keyword",
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


def _jsonable(value: Any) -> Any:
    """Return a JSON-serializable copy of `value` for a JSONB column."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _run_values(run: dict[str, Any]) -> dict[str, Any]:
    return {
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


def _upsert_run(session, run: dict[str, Any]) -> None:
    """Upsert the run-level row within the given session (no commit)."""
    values = _run_values(run)
    stmt = pg_insert(BidnetRun).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[BidnetRun.run_id],
        set_={k: v for k, v in values.items() if k != "run_id"},
    )
    session.execute(stmt)


def save_run(run: dict[str, Any]) -> None:
    """Upsert the run-level row in bidnet_runs (its own transaction)."""
    session = SessionLocal()
    try:
        _upsert_run(session, run)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def save_bids(run: dict[str, Any], records: list[dict[str, Any]]) -> int:
    """Upsert a run's solicitations into bidnet_bids in one transaction.

    Mirrors MyFlorida's ingest: a single session upserts the run row and every
    bid, de-duplicated by reference number within the run, with the complete
    scraped record kept in `raw_data`, and one commit at the end. Rolls back and
    re-raises on any error so the caller can fall back to an Excel-from-records.
    Returns the number of rows stored.
    """
    session = SessionLocal()
    try:
        _upsert_run(session, run)

        stored = 0
        seen_refs: set[str] = set()
        for record in records:
            values: dict[str, Any] = {k: (record.get(k) or None) for k in _BID_FIELDS}
            values["run_id"] = run["run_id"]
            values["raw_data"] = _jsonable({k: v for k, v in record.items() if k != "documents"})

            ref = values.get("reference_number")
            if ref and ref in seen_refs:
                continue
            if ref:
                seen_refs.add(ref)
                stmt = pg_insert(BidnetBid).values(**values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_bidnet_run_ref",
                    set_={k: v for k, v in values.items() if k not in ("run_id", "reference_number")},
                )
                session.execute(stmt)
            else:
                session.add(BidnetBid(**values))
            stored += 1

        session.commit()
        logger.info("[run %s] stored %d bid rows in DB", run.get("run_id"), stored)
        return stored
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
    records that carry a reference number are written (same as what save_bids stores).
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
