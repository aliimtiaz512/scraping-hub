"""Unison persistence: store scraped buyer requests in the DB and build the
per-run Excel from the DB (openpyxl). Same DB-first-with-fallback pattern as the
other hub portals."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.unison.models import EXCEL_COLUMNS, UnisonRequest, UnisonRun

logger = logging.getLogger(__name__)

_BID_FIELDS = {"buyer_number", "buyer_description", "buyer", "end_date"}


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
    values = _run_values(run)
    stmt = pg_insert(UnisonRun).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[UnisonRun.run_id],
        set_={k: v for k, v in values.items() if k != "run_id"},
    )
    session.execute(stmt)


def save_run(run: dict[str, Any]) -> None:
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
    """Upsert a run's requests into unison_requests, deduped by buyer_number."""
    session = SessionLocal()
    try:
        _upsert_run(session, run)

        stored = 0
        seen: set[str] = set()
        for record in records:
            values: dict[str, Any] = {k: (record.get(k) or None) for k in _BID_FIELDS}
            values["run_id"] = run["run_id"]
            values["raw_data"] = _jsonable(record)

            buyer_number = values.get("buyer_number")
            if buyer_number and buyer_number in seen:
                continue
            if buyer_number:
                seen.add(buyer_number)
                stmt = pg_insert(UnisonRequest).values(**values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_unison_run_buyer",
                    set_={k: v for k, v in values.items() if k not in ("run_id", "buyer_number")},
                )
                session.execute(stmt)
            else:
                session.add(UnisonRequest(**values))
            stored += 1

        session.commit()
        logger.info("[run %s] stored %d Unison rows in DB", run.get("run_id"), stored)
        return stored
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _rows_for_run(run_id: str) -> list[UnisonRequest]:
    session = SessionLocal()
    try:
        return session.execute(
            select(UnisonRequest).where(UnisonRequest.run_id == run_id).order_by(UnisonRequest.id)
        ).scalars().all()
    finally:
        session.close()


def generate_excel(run_id: str, out_path: str | Path) -> int:
    rows = _rows_for_run(run_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Unison Requests"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    for row in rows:
        sheet.append([getattr(row, attr, None) for attr, _ in EXCEL_COLUMNS])
    workbook.save(str(out_path))
    logger.info("[run %s] wrote %d Unison rows to %s", run_id, len(rows), out_path)
    return len(rows)


def generate_excel_from_records(records: list[dict[str, Any]], out_path: str | Path) -> int:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Unison Requests"
    sheet.append([header for _, header in EXCEL_COLUMNS])
    count = 0
    for record in records:
        if not record.get("buyer_number"):
            continue
        sheet.append([record.get(attr) for attr, _ in EXCEL_COLUMNS])
        count += 1
    workbook.save(str(out_path))
    return count
