"""North Dakota (ND Buys / Ivalua) persistence: store scraped solicitations in
the DB and build the per-run Excel sheet from the DB (openpyxl).

The portal has no native export, so the data path is
scrape -> northdakota_bids -> generated Excel. Persistence uses the same batched
single-transaction mechanism as MyFlorida: one session upserts the run row and
every bid, with the complete scraped record kept in raw_data, and one commit.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.northdakota.models import EXCEL_COLUMNS, NorthDakotaBid, NorthDakotaRun

logger = logging.getLogger(__name__)

# Columns actually present on NorthDakotaBid (used to filter a scraped record dict).
_BID_FIELDS = {
    "rfp_id", "title", "pub_begin_date", "pub_end_date", "begin_date", "close_date",
    "commodity", "remaining_time", "status", "detail_url", "matched_keyword",
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
        "search": run.get("search"),
        "bids_found": run.get("bids_found", 0),
        "documents_downloaded": run.get("documents_downloaded", 0),
        "folder": run.get("folder"),
        "excel_path": run.get("excel_path"),
    }


def _upsert_run(session, run: dict[str, Any]) -> None:
    """Upsert the run-level row within the given session (no commit)."""
    values = _run_values(run)
    stmt = pg_insert(NorthDakotaRun).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[NorthDakotaRun.run_id],
        set_={k: v for k, v in values.items() if k != "run_id"},
    )
    session.execute(stmt)


def save_run(run: dict[str, Any]) -> None:
    """Upsert the run-level row in northdakota_runs (its own transaction)."""
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
    """Upsert a run's solicitations into northdakota_bids in one transaction.

    Mirrors MyFlorida's ingest: a single session upserts the run row and every
    bid, de-duplicated by rfp_id within the run, with the complete scraped record
    kept in `raw_data`, and one commit at the end. Rolls back and re-raises on any
    error so the caller can fall back to an Excel-from-records. Returns the number
    of rows stored.
    """
    session = SessionLocal()
    try:
        _upsert_run(session, run)

        stored = 0
        seen_ids: set[str] = set()
        for record in records:
            values: dict[str, Any] = {k: (record.get(k) or None) for k in _BID_FIELDS}
            values["run_id"] = run["run_id"]
            values["raw_data"] = _jsonable(record)

            rfp_id = values.get("rfp_id")
            if rfp_id and rfp_id in seen_ids:
                continue
            if rfp_id:
                seen_ids.add(rfp_id)
                stmt = pg_insert(NorthDakotaBid).values(**values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_northdakota_run_rfp",
                    set_={k: v for k, v in values.items() if k not in ("run_id", "rfp_id")},
                )
                session.execute(stmt)
            else:
                session.add(NorthDakotaBid(**values))
            stored += 1

        session.commit()
        logger.info("[run %s] stored %d bid rows in DB", run.get("run_id"), stored)
        return stored
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _rows_for_run(run_id: str) -> list[NorthDakotaBid]:
    session = SessionLocal()
    try:
        return session.execute(
            select(NorthDakotaBid).where(NorthDakotaBid.run_id == run_id).order_by(NorthDakotaBid.id)
        ).scalars().all()
    finally:
        session.close()


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build this run's Excel sheet from northdakota_bids. Returns the row count."""
    rows = _rows_for_run(run_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "North Dakota Bids"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    for bid in rows:
        sheet.append([getattr(bid, attr, None) for attr, _ in EXCEL_COLUMNS])
    workbook.save(str(out_path))
    logger.info("[run %s] wrote %d rows to %s", run_id, len(rows), out_path)
    return len(rows)


def generate_excel_from_records(records: list[dict[str, Any]], out_path: str | Path) -> int:
    """Build the Excel straight from in-memory records (DB-unavailable fallback).

    Mirrors generate_excel: only records that carry an rfp_id are written.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "North Dakota Bids"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    count = 0
    for record in records:
        if not record.get("rfp_id"):
            continue
        sheet.append([record.get(attr) for attr, _ in EXCEL_COLUMNS])
        count += 1
    workbook.save(str(out_path))
    return count
