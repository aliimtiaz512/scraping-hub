"""SEPTA persistence: store scraped Open Quotes in the DB and build the per-run
Excel sheet from the DB (openpyxl).

The portal has no native export, so the data path is
scrape -> septa_bids -> generated Excel. Persistence uses the same batched
single-transaction mechanism as North Dakota: one session upserts the run row
and every quote, with the complete scraped record kept in raw_data, and one
commit. If the DB is unavailable the Excel is written straight from the
in-memory records instead.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.septa.models import EXCEL_COLUMNS, SeptaBid, SeptaRun

logger = logging.getLogger(__name__)

# Columns actually present on SeptaBid (used to filter a scraped record dict).
_BID_FIELDS = {"requisition_number", "summary", "open_date", "close_date"}


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
        "search": run.get("search"),
        "bids_found": run.get("bids_found", 0),
        "documents_downloaded": run.get("documents_downloaded", 0),
        "folder": run.get("folder"),
        "excel_path": run.get("excel_path"),
    }


def _upsert_run(session, run: dict[str, Any]) -> None:
    """Upsert the run-level row within the given session (no commit)."""
    values = _run_values(run)
    stmt = pg_insert(SeptaRun).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SeptaRun.run_id],
        set_={k: v for k, v in values.items() if k != "run_id"},
    )
    session.execute(stmt)


def save_run(run: dict[str, Any]) -> None:
    """Upsert the run-level row in septa_runs (its own transaction)."""
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
    """Upsert a run's quotes into septa_bids in one transaction.

    Mirrors North Dakota's ingest: a single session upserts the run row and every
    quote, de-duplicated by requisition_number within the run, with the complete
    scraped record kept in `raw_data`, and one commit at the end. Rolls back and
    re-raises on any error so the caller can fall back to an Excel-from-records.
    Returns the number of rows stored.
    """
    session = SessionLocal()
    try:
        _upsert_run(session, run)

        stored = 0
        seen: set[str] = set()
        for record in records:
            values: dict[str, Any] = {k: (record.get(k) or None) for k in _BID_FIELDS}
            values["run_id"] = run["run_id"]
            values["raw_data"] = _jsonable(record)

            req = values.get("requisition_number")
            if req and req in seen:
                continue
            if req:
                seen.add(req)
                stmt = pg_insert(SeptaBid).values(**values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_septa_run_requisition",
                    set_={k: v for k, v in values.items() if k not in ("run_id", "requisition_number")},
                )
                session.execute(stmt)
            else:
                session.add(SeptaBid(**values))
            stored += 1

        session.commit()
        logger.info("[run %s] stored %d quote rows in DB", run.get("run_id"), stored)
        return stored
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _rows_for_run(run_id: str) -> list[SeptaBid]:
    session = SessionLocal()
    try:
        return session.execute(
            select(SeptaBid).where(SeptaBid.run_id == run_id).order_by(SeptaBid.id)
        ).scalars().all()
    finally:
        session.close()


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build this run's Excel sheet from septa_bids. Returns the row count."""
    rows = _rows_for_run(run_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SEPTA Quotes"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    for bid in rows:
        sheet.append([getattr(bid, attr, None) for attr, _ in EXCEL_COLUMNS])
    workbook.save(str(out_path))
    logger.info("[run %s] wrote %d rows to %s", run_id, len(rows), out_path)
    return len(rows)


def generate_excel_from_records(records: list[dict[str, Any]], out_path: str | Path) -> int:
    """Build the Excel straight from in-memory records (DB-unavailable fallback).

    Mirrors generate_excel: only records that carry a requisition_number are written.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SEPTA Quotes"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    count = 0
    for record in records:
        if not record.get("requisition_number"):
            continue
        sheet.append([record.get(attr) for attr, _ in EXCEL_COLUMNS])
        count += 1
    workbook.save(str(out_path))
    return count
