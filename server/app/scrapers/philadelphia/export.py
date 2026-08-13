"""PHLContracts persistence: upsert bids, and build the summary sheet.

`generate_excel(run_id, path)` is the contract `app.core.exports` calls to
rebuild a run's sheet on demand (Download button, completion email), so a
spreadsheet a user asks for months later comes from the database rather than from
a workspace that was deleted the day it ran.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import excel_style
from app.db import SessionLocal
from app.scrapers.philadelphia import details, storage
from app.scrapers.philadelphia.models import (
    EXCEL_COLUMNS,
    PhiladelphiaBid,
    PhiladelphiaRun,
)

logger = logging.getLogger(__name__)

# Columns a scraped record can fill directly. `bid_number` is the key and is set
# separately; `first_seen_at` is only ever written by the insert.
_BID_FIELDS = (
    "organization", "alternate_id", "buyer", "description", "bid_opening_date",
    "detail_url", "documents_downloaded",
    # Promoted out of the header table so the sheet can be sorted on them.
    "fiscal_year", "solicitation_type", "pre_bid_conference",
)
_JSON_FIELDS = ("extra_header_data", "file_paths", "document_errors", "items")


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
        "bids_found": int(run.get("bids_found") or 0),
        "documents_downloaded": int(run.get("documents_downloaded") or 0),
        "folder": run.get("folder"),
        "excel_path": run.get("excel_path"),
    }


def _upsert_run(session, run: dict[str, Any]) -> None:
    values = _run_values(run)
    stmt = pg_insert(PhiladelphiaRun).values(**values)
    session.execute(stmt.on_conflict_do_update(
        index_elements=[PhiladelphiaRun.run_id],
        set_={k: v for k, v in values.items() if k != "run_id"},
    ))


def save_run(run: dict[str, Any]) -> None:
    """Upsert the run-level row on its own (called at the start of a run)."""
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
    """Upsert this run's bids into `city_of_philadelphia_bids`. Returns rows stored.

    Keyed on `bid_number`, so a bid seen by three consecutive runs is one row
    that keeps moving rather than three rows to reconcile — the Open Bids list
    is a live set, and the same bid is in it every day until it closes.

    Two fields are deliberately *not* overwritten on a repeat sighting:
    `first_seen_at`, which is the point of having it, and any earlier
    `file_paths`/`extra_header_data` when this run captured none — a detail page
    that failed to load today must not erase what it gave yesterday. Everything
    else is refreshed, because the newer reading is the true one.
    """
    session = SessionLocal()
    stored = 0
    try:
        _upsert_run(session, run)

        seen: set[str] = set()
        for record in records:
            bid_number = str(record.get("bid_number") or "").strip()
            if not bid_number:
                logger.warning(
                    "[run %s] a scraped row carried no bid number and was not stored: %.120s",
                    run.get("run_id"), record.get("description"),
                )
                continue
            if bid_number in seen:
                continue          # the same bid twice in one listing
            seen.add(bid_number)

            values: dict[str, Any] = {
                field: (record.get(field) if record.get(field) != "" else None)
                for field in _BID_FIELDS
            }
            values["documents_downloaded"] = int(record.get("documents_downloaded") or 0)
            for field in _JSON_FIELDS:
                values[field] = _jsonable(
                    record.get(field) or ({} if field == "extra_header_data" else [])
                )
            values["bid_number"] = bid_number
            values["run_id"] = run["run_id"]
            values["scraped_at"] = datetime.now()
            values["raw_data"] = _jsonable(record)

            update = {k: v for k, v in values.items() if k not in ("bid_number", "first_seen_at")}
            # Keep what an earlier run captured when this one came back empty:
            # a detail page that failed today should not erase yesterday's files.
            for field in ("extra_header_data", "file_paths"):
                if not values[field]:
                    update.pop(field, None)

            session.execute(
                pg_insert(PhiladelphiaBid).values(**values).on_conflict_do_update(
                    index_elements=[PhiladelphiaBid.bid_number], set_=update
                )
            )
            stored += 1

        session.commit()
        logger.info(
            "[run %s] stored %d of %d Philadelphia bid(s)",
            run.get("run_id"), stored, len(records),
        )
        return stored
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _rows_for_run(run_id: str) -> list[PhiladelphiaBid]:
    session = SessionLocal()
    try:
        return list(session.execute(
            select(PhiladelphiaBid)
            .where(PhiladelphiaBid.run_id == run_id)
            .order_by(PhiladelphiaBid.bid_opening_date, PhiladelphiaBid.bid_number)
        ).scalars())
    finally:
        session.close()


def _cell(source: Any, attr: str) -> Any:
    """One summary cell, from a model row or from a scraped record.

    Three columns are derived rather than stored twice — each is a function of
    something already on the row, and a second copy is a second thing to keep in
    step. `additional_header` is derived here in particular so that a sheet
    rebuilt from the database months later reads identically to the one that
    shipped in the ZIP.
    """
    getter = source.get if isinstance(source, dict) else lambda k: getattr(source, k, None)
    if attr == "folder":
        return storage.folder_reference(getter("bid_number") or "")
    if attr == "item_count":
        return len(getter("items") or [])
    if attr == "additional_header":
        # A scraped record has already had this rendered; a database row has
        # the header table it is rendered from.
        return getter("additional_header") or details.additional_header(
            getter("extra_header_data") or {}
        )
    return getter(attr)


def _write_workbook(rows: list[Any], out_path: str | Path) -> int:
    workbook, sheet = excel_style.new_workbook("Open Bids")
    count = excel_style.write_table(
        sheet,
        [header for _, header in EXCEL_COLUMNS],
        ([_cell(row, attr) for attr, _ in EXCEL_COLUMNS] for row in rows),
    )
    workbook.save(str(out_path))
    return count


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Rebuild this run's summary sheet from the database. Returns the row count."""
    count = _write_workbook(_rows_for_run(run_id), out_path)
    logger.info("[run %s] wrote %d rows to %s", run_id, count, Path(out_path).name)
    return count


def generate_excel_from_records(records: list[dict[str, Any]], out_path: str | Path) -> int:
    """Build the summary sheet straight from what was scraped.

    The copy that goes *inside the archive*, written before the database is
    touched — the deliverable is a ZIP whose root holds the sheet next to the
    documents it indexes, so the file has to exist at packaging time whatever
    the database is doing. Every record is written; nothing is filtered here.
    """
    return _write_workbook(records, out_path)
