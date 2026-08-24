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
from app.scrapers.bidnet.models import (
    EXCEL_COLUMNS,
    MEMBER_AGENCY_EXCEL_COLUMNS,
    BidnetBid,
    BidnetRun,
)

Columns = list[tuple[str, str]]

logger = logging.getLogger(__name__)

# The declared width of every length-limited column on BidnetBid, read off the
# model so it cannot drift from the schema.
_COLUMN_LIMITS: dict[str, int] = {
    column.name: column.type.length
    for column in BidnetBid.__table__.columns
    if getattr(column.type, "length", None)
}


def _fit(values: dict[str, Any]) -> dict[str, Any]:
    """Trim any value that is longer than the column meant to hold it.

    Postgres does not truncate an oversized value — it raises, and the raise
    aborts the transaction, which here means the **whole run's** insert. One
    solicitation whose Closing Date read "See specification, Section 4, for the
    submission schedule…" is enough to lose every other bid in the run, and that
    is exactly what happened: 1,859 records rolled back over a single cell.

    Widening the columns (see the migration) fixes the case we know about. This
    fixes the shape of the failure: these are free text the portal fills however
    a given agency pleases, and a sweep across five hundred agencies will always
    find a field none of us predicted. Losing the tail of one string is a fair
    trade for keeping the run.

    Truncation is logged with the field and the run — a silently shortened value
    would be its own small lie.
    """
    trimmed: list[str] = []
    for key, limit in _COLUMN_LIMITS.items():
        value = values.get(key)
        if isinstance(value, str) and len(value) > limit:
            values[key] = value[: limit - 1] + "…"
            trimmed.append(f"{key} ({len(value)}>{limit})")
    if trimmed:
        logger.warning(
            "[run %s] trimmed %d oversized value(s) to fit their column: %s",
            values.get("run_id"), len(trimmed), ", ".join(trimmed),
        )
    return values

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
            # Before anything is sent: no single field may abort the run's save.
            _fit(values)
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


def _write_workbook(
    rows: list[BidnetBid], out_path: str | Path, columns: Columns | None = None
) -> int:
    columns = columns or EXCEL_COLUMNS
    workbook, sheet = excel_style.new_workbook("BidNet Bids")
    excel_style.write_table(
        sheet,
        [header for _, header in columns],
        ([getattr(bid, attr, None) for attr, _ in columns] for bid in rows),
    )
    workbook.save(str(out_path))
    return len(rows)


def columns_for_run(run_id: str) -> Columns:
    """Which column layout this run's sheet uses.

    A member agency sweep drops `Matched Keyword` (it searches none) and carries
    `Documents` instead; every other run keeps the niche layout. Decided from
    the run record rather than passed in, because the generic packaging path
    (`core.exports.excel_bytes`) calls `generate_excel(run_id, path)` for every
    portal and has no way to know one BidNet run from another.

    Falls back to the niche layout for a run the manager no longer holds — the
    older and far more common shape, and the one whose columns are a superset.
    """
    from app.core import run_manager

    try:
        run = run_manager.get_run(run_id) or {}
    except Exception:  # noqa: BLE001 — never fail an export over run bookkeeping
        return EXCEL_COLUMNS
    return MEMBER_AGENCY_EXCEL_COLUMNS if run.get("member_agency_sweep") else EXCEL_COLUMNS


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build this run's Excel sheet from bidnet_bids. Returns the row count."""
    count = _write_workbook(_rows_for_run(run_id), out_path, columns_for_run(run_id))
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


def generate_excel_from_records(
    records: list[dict[str, Any]], out_path: str | Path, columns: Columns | None = None
) -> int:
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
    columns = columns or EXCEL_COLUMNS
    workbook, sheet = excel_style.new_workbook("BidNet Bids")
    count = excel_style.write_table(
        sheet,
        [header for _, header in columns],
        ([record.get(attr) for attr, _ in columns] for record in records),
    )
    workbook.save(str(out_path))
    return count


def export_all_excel(out_path: str | Path) -> int:
    """Build an Excel of every stored solicitation (backs the on-demand export)."""
    return _write_workbook(_all_rows(), out_path)
