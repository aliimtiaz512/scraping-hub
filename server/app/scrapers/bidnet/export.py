"""BidNet persistence: store scraped solicitations in the DB and build the
per-run Excel sheet from the DB (openpyxl).

The on-demand export endpoint and the auto-generated run Excel both use the same
EXCEL_COLUMNS mapping, so they always agree.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import excel_style
from app.db import SessionLocal
from app.scrapers.bidnet.models import EXCEL_COLUMNS, BidnetBid, BidnetRun

logger = logging.getLogger(__name__)

# Columns actually present on BidnetBid (used to filter a scraped record dict).
_BID_FIELDS = {
    "reference_number", "solicitation_number", "solicitation_type", "title",
    "publication_date", "question_acceptance_deadline", "closing_date",
    "documents_count", "matched_keyword", "niche", "status", "detail_url",
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

    Records with no reference number are still inserted (the unique constraint
    is on `(run_id, reference_number)` and Postgres treats NULLs as distinct), so
    a bid whose detail page failed to render is preserved rather than dropped.
    A genuine duplicate reference *is* skipped — but never silently: it is logged
    with the reference so an unexpected collision is visible.
    """
    session = SessionLocal()
    try:
        _upsert_run(session, run)

        stored = 0
        seen_refs: set[str] = set()
        skipped_dupes: list[str] = []
        for record in records:
            values: dict[str, Any] = {k: (record.get(k) or None) for k in _BID_FIELDS}
            values["run_id"] = run["run_id"]
            values["raw_data"] = _jsonable({k: v for k, v in record.items() if k != "documents"})

            ref = values.get("reference_number")
            if ref and ref in seen_refs:
                skipped_dupes.append(ref)
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
        if skipped_dupes:
            logger.warning(
                "[run %s] %d record(s) shared a reference number already stored for this "
                "run and were not written again: %s",
                run.get("run_id"), len(skipped_dupes), ", ".join(skipped_dupes),
            )
        logger.info(
            "[run %s] stored %d of %d bid row(s) in DB", run.get("run_id"), stored, len(records)
        )
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
    workbook, sheet = excel_style.new_workbook("BidNet Bids")
    excel_style.write_table(
        sheet,
        [header for _, header in EXCEL_COLUMNS],
        ([getattr(bid, attr, None) for attr, _ in EXCEL_COLUMNS] for bid in rows),
    )
    workbook.save(str(out_path))
    return len(rows)


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build this run's Excel sheet from bidnet_bids. Returns the row count."""
    count = _write_workbook(_rows_for_run(run_id), out_path)
    logger.info("[run %s] wrote %d rows to %s", run_id, count, out_path)
    return count


def _rows_for_runs(run_ids: list[str]) -> list[BidnetBid]:
    if not run_ids:
        return []
    session = SessionLocal()
    try:
        return session.execute(
            select(BidnetBid).where(BidnetBid.run_id.in_(run_ids)).order_by(BidnetBid.id)
        ).scalars().all()
    finally:
        session.close()


def generate_excel_for_runs(run_ids: list[str], out_path: str | Path) -> int:
    """Build one sheet covering several runs, de-duplicated by reference number.

    A niche can be run more than once in a day — a re-run after a portal
    hiccup, or a second pass with different filters. Its folder holds a single
    `<Niche>_Bids.xlsx`, and that sheet has to carry every run's bids or the
    re-run would quietly replace the earlier one's results. Bids seen in more
    than one run are written once, keeping the most recent row, since the same
    solicitation surfacing twice is one solicitation.

    Returns the number of rows written.
    """
    rows = _rows_for_runs(run_ids)
    deduped: dict[str, BidnetBid] = {}
    ordered: list[BidnetBid] = []
    for row in rows:
        ref = (row.reference_number or "").strip()
        if not ref:
            # No reference number to key on — a bid whose detail page failed to
            # render. Kept as its own row rather than collapsed into another.
            ordered.append(row)
            continue
        if ref in deduped:
            # Later row wins: same run_id order means the newer scrape.
            ordered[ordered.index(deduped[ref])] = row
            deduped[ref] = row
            continue
        deduped[ref] = row
        ordered.append(row)

    count = _write_workbook(ordered, out_path)
    logger.info(
        "wrote %d rows to %s from %d run(s) (%d duplicate reference(s) collapsed)",
        count, out_path, len(run_ids), len(rows) - count,
    )
    return count


def generate_excel_from_records(records: list[dict[str, Any]], out_path: str | Path) -> int:
    """Build this run's Excel sheet straight from the in-memory scraped records.

    **Every record is written.** This used to skip records with no reference
    number, which silently dropped exactly the bids most worth seeing — the ones
    whose detail page could not be read — while the caller logged the unfiltered
    count, so the log claimed more rows than the file held. A record that could
    not be fully scraped now appears with its `status` (EXTRACTION_FAILED /
    PARTIAL_DATA) and `detail_url` so it can be chased by hand.

    Returns the number of rows written, which callers must log rather than the
    length of what they passed in.
    """
    workbook, sheet = excel_style.new_workbook("BidNet Bids")
    count = excel_style.write_table(
        sheet,
        [header for _, header in EXCEL_COLUMNS],
        ([record.get(attr) for attr, _ in EXCEL_COLUMNS] for record in records),
    )
    workbook.save(str(out_path))
    return count


def export_all_excel(out_path: str | Path) -> int:
    """Build an Excel of every stored solicitation (backs the on-demand export)."""
    return _write_workbook(_all_rows(), out_path)
