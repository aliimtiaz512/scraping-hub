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

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import excel_style
from app.db import SessionLocal
from app.scrapers.septa.models import (
    EXCEL_COLUMNS,
    OPEN_BID_EXCEL_COLUMNS,
    SeptaBid,
    SeptaOpenBid,
    SeptaRun,
)

logger = logging.getLogger(__name__)

# Worksheet names. The quotes sheet keeps its original title so an existing
# consumer reading the workbook by sheet name is unaffected by the Bids sheet
# arriving beside it.
QUOTES_SHEET = "SEPTA Quotes"
OPEN_BIDS_SHEET = "SEPTA Bids"

# Columns actually present on SeptaBid (used to filter a scraped record dict).
_BID_FIELDS = {
    "requisition_number", "summary", "open_date", "close_date",
    # Provenance from a niche run: which niche was searched, and which of its
    # keywords/commodity codes surfaced this quote.
    "niche", "matched_terms",
}

# Columns actually present on SeptaOpenBid.
_OPEN_BID_FIELDS = {"bid_number", "title", "open_date", "close_date"}

# Declared widths of the VARCHAR columns, so an over-long value is trimmed here
# rather than aborting the INSERT. Postgres raises StringDataRightTruncation for
# the whole statement, and because the batch is one transaction that means a
# single bad field loses every row in the run — which is exactly what happened
# when the Open Bids grid shifted a bid title into `open_date`. The scrape is
# the expensive part; a trimmed field plus a loud warning is always a better
# outcome than an empty table, and `raw_data` still holds the untrimmed row.
_COLUMN_LIMITS: dict[str, int] = {
    "requisition_number": 255,
    "bid_number": 255,
    "open_date": 64,
    "close_date": 64,
}


def _fit(run_id: Any, field: str, value: Any) -> Any:
    """`value`, trimmed to its column's width, warning loudly if it had to be."""
    limit = _COLUMN_LIMITS.get(field)
    if limit is None or not isinstance(value, str) or len(value) <= limit:
        return value
    logger.warning(
        "[run %s] %s was %d chars, over the column's %d — stored truncated. "
        "This usually means the grid's columns have moved; the full value is "
        "kept in raw_data. Value: %.120s",
        run_id, field, len(value), limit, value,
    )
    return value[:limit]


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
            values: dict[str, Any] = {
                k: _fit(run.get("run_id"), k, record.get(k) or None) for k in _BID_FIELDS
            }
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


def save_open_bids(run: dict[str, Any], records: list[dict[str, Any]]) -> int:
    """Upsert a run's Open Bids into septa_open_bids in one transaction.

    The Open Bids counterpart of `save_bids`, deliberately its own transaction:
    a failure storing one grid should cost that grid's rows, not the other's.
    Returns the number of rows stored.
    """
    if not records:
        return 0
    session = SessionLocal()
    try:
        _upsert_run(session, run)

        stored = 0
        seen: set[str] = set()
        for record in records:
            values: dict[str, Any] = {
                k: _fit(run.get("run_id"), k, record.get(k) or None)
                for k in _OPEN_BID_FIELDS
            }
            values["run_id"] = run["run_id"]
            # The untrimmed row, always — so a truncated column is recoverable.
            values["raw_data"] = _jsonable(record)

            number = values.get("bid_number")
            if number and number in seen:
                continue
            if number:
                seen.add(number)
                stmt = pg_insert(SeptaOpenBid).values(**values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_septa_run_bid_number",
                    set_={k: v for k, v in values.items() if k not in ("run_id", "bid_number")},
                )
                session.execute(stmt)
            else:
                session.add(SeptaOpenBid(**values))
            stored += 1

        session.commit()
        logger.info("[run %s] stored %d open-bid rows in DB", run.get("run_id"), stored)
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


def _open_bid_rows_for_run(run_id: str) -> list[SeptaOpenBid]:
    session = SessionLocal()
    try:
        return session.execute(
            select(SeptaOpenBid).where(SeptaOpenBid.run_id == run_id).order_by(SeptaOpenBid.id)
        ).scalars().all()
    finally:
        session.close()


def _write_sheet(sheet, columns: list[tuple[str, str]], rows: list[Any], getter) -> int:
    """Header row plus one row per record, in the hub's standard styling.
    Returns how many records were written."""
    return excel_style.write_table(
        sheet,
        [header for _, header in columns],
        ([getter(row, attr) for attr, _ in columns] for row in rows),
    )


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build this run's workbook from the DB. Returns the total row count.

    Two sheets, one per module: "SEPTA Quotes" from `septa_bids` and "SEPTA
    Bids" from `septa_open_bids`. The quotes sheet is written first and keeps
    its name and columns, so this reads the same as it always did for a run
    that found no open bids — the Bids sheet is then present but empty rather
    than absent, which distinguishes "the Bids pass found nothing" from "this
    workbook predates the Bids pass".
    """
    quotes = _rows_for_run(run_id)
    open_bids = _open_bid_rows_for_run(run_id)

    workbook, sheet = excel_style.new_workbook(QUOTES_SHEET)
    _write_sheet(sheet, EXCEL_COLUMNS, quotes, lambda r, attr: getattr(r, attr, None))
    _write_sheet(
        workbook.create_sheet(OPEN_BIDS_SHEET),
        OPEN_BID_EXCEL_COLUMNS, open_bids, lambda r, attr: getattr(r, attr, None),
    )

    workbook.save(str(out_path))
    logger.info(
        "[run %s] wrote %d quote row(s) and %d open-bid row(s) to %s",
        run_id, len(quotes), len(open_bids), out_path,
    )
    return len(quotes) + len(open_bids)


def generate_excel_from_records(
    records: list[dict[str, Any]],
    out_path: str | Path,
    open_bids: list[dict[str, Any]] | None = None,
) -> int:
    """Build the workbook straight from in-memory records (DB-unavailable fallback).

    Mirrors `generate_excel`, including both sheets. Only records carrying the
    sheet's key column are written — a requisition number for a quote, a bid
    number for an open bid. `open_bids` is optional so an existing caller that
    only has quotes still works.
    """
    kept_quotes = [r for r in records if r.get("requisition_number")]
    kept_bids = [r for r in (open_bids or []) if r.get("bid_number")]

    workbook, sheet = excel_style.new_workbook(QUOTES_SHEET)
    _write_sheet(sheet, EXCEL_COLUMNS, kept_quotes, lambda r, attr: r.get(attr))
    _write_sheet(
        workbook.create_sheet(OPEN_BIDS_SHEET),
        OPEN_BID_EXCEL_COLUMNS, kept_bids, lambda r, attr: r.get(attr),
    )

    workbook.save(str(out_path))
    return len(kept_quotes) + len(kept_bids)
