"""RideMetro persistence: store the network sweep in the DB and rebuild the
agency-grouped Excel report *from* the DB (see `workbook`).

Neither Bonfire nor the Euna Supplier Network offers an export, so the data path
is scrape -> ridemetro_bids -> generated report.

Agency order and the roster itself come from the run row (`ridemetro_runs.
agencies`), not from the bid rows: an agency with nothing open has no bid rows,
and dropping it from the report would make "visited, nothing open" indistinguish-
able from "never visited".
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.ridemetro import workbook
from app.scrapers.ridemetro.models import SHEET_COLUMNS, RideMetroBid, RideMetroRun

logger = logging.getLogger(__name__)

# Columns actually present on RideMetroBid (used to filter a scraped detail dict).
_BID_FIELDS = {
    "agency", "agency_url", "project", "ref_number", "department",
    "opportunity_type", "status", "open_date", "intent_to_bid_due_date",
    "question_due_date", "close_date", "days_left", "project_description",
}

# Shown in place of an agency's rows when it has none.
NOTE_EMPTY = "No open public opportunities."
NOTE_SKIPPED = "Skipped — supplier registration Incomplete."
NOTE_FAILED = "Could not be read: {error}"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _run_values(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "status": run.get("status"),
        "account": run.get("account"),
        "started_at": _parse_dt(run.get("started_at")),
        "finished_at": _parse_dt(run.get("finished_at")),
        "opportunities_found": run.get("bids_found", 0),
        "documents_downloaded": run.get("documents_downloaded", 0),
        "folder": run.get("folder"),
        "excel_path": run.get("excel_path"),
        "agencies_found": run.get("agencies_found", 0),
        "agencies_scraped": run.get("agencies_scraped", 0),
        "agencies": run.get("agencies", []),
    }


def _upsert_run(session, run: dict[str, Any]) -> None:
    """Upsert the run-level row within the given session (no commit)."""
    values = _run_values(run)
    stmt = pg_insert(RideMetroRun).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RideMetroRun.run_id],
        set_={k: v for k, v in values.items() if k != "run_id"},
    )
    session.execute(stmt)


def save_run(run: dict[str, Any]) -> None:
    """Upsert the run-level row in ridemetro_runs (its own transaction)."""
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
    """Upsert a run's opportunities into ridemetro_bids in one transaction.

    De-duplicated by (agency, ref number) within the run — the same reference
    can legitimately appear under two agencies — with the complete scraped field
    map kept in `raw_data`, and one commit at the end. Rolls back and re-raises
    on any error so the caller can fall back to an Excel-from-records. Returns
    the number of rows stored.
    """
    session = SessionLocal()
    try:
        _upsert_run(session, run)

        stored = 0
        seen: set[tuple[str, str]] = set()
        for details in records:
            values: dict[str, Any] = {k: details.get(k) for k in _BID_FIELDS}
            values.update(
                run_id=run["run_id"],
                opportunity_url=details.get("opportunity_url"),
                zip_filename=details.get("zip_filename"),
                raw_data=details.get("raw_data", {}),
            )

            ref = values.get("ref_number")
            key = (values.get("agency") or "", ref or "")
            if ref and key in seen:
                continue
            if ref:
                seen.add(key)
                stmt = pg_insert(RideMetroBid).values(**values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_ridemetro_run_agency_ref",
                    set_={
                        k: v for k, v in values.items()
                        if k not in ("run_id", "agency", "ref_number")
                    },
                )
                session.execute(stmt)
            else:
                session.add(RideMetroBid(**values))
            stored += 1

        session.commit()
        logger.info("[run %s] stored %d bid rows in DB", run.get("run_id"), stored)
        return stored
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# -- report ----------------------------------------------------------------


def _note_for(agency: dict[str, Any]) -> str:
    if agency.get("error"):
        return NOTE_FAILED.format(error=agency["error"])
    if agency.get("skipped"):
        return NOTE_SKIPPED
    return NOTE_EMPTY


def _group(
    records: Iterable[dict[str, Any]],
    roster: list[dict[str, Any]],
) -> tuple[list[tuple[str, list[dict[str, Any]]]], dict[str, str]]:
    """Bucket records by agency, ordered by the roster.

    Roster agencies come first, in My Network's order, so a run's report always
    lists the whole network. Any agency that somehow has rows without being on
    the roster is appended rather than dropped.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(record.get("agency") or "(unknown agency)", []).append(record)

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    notes: dict[str, str] = {}
    for agency in roster:
        name = agency.get("name") or "(unnamed agency)"
        groups.append((name, buckets.pop(name, [])))
        notes[name] = _note_for(agency)
    for name, rows in buckets.items():
        groups.append((name, rows))
    return groups, notes


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build the run's report from the DB. Returns the opportunity row count."""
    session = SessionLocal()
    try:
        run_row = session.get(RideMetroRun, run_id)
        roster = list(run_row.agencies or []) if run_row else []
        bids = session.execute(
            select(RideMetroBid).where(RideMetroBid.run_id == run_id).order_by(RideMetroBid.id)
        ).scalars().all()
    finally:
        session.close()

    records = [
        {"agency": bid.agency, **{attr: getattr(bid, attr, None) for attr, _ in SHEET_COLUMNS}}
        for bid in bids
    ]
    groups, notes = _group(records, roster)
    written = workbook.build(groups, out_path, notes)
    logger.info("[run %s] report holds %d row(s) across %d agency block(s)",
                run_id, written, len(groups))
    return written


def generate_excel_from_records(
    records: list[dict[str, Any]],
    out_path: str | Path,
    roster: list[dict[str, Any]] | None = None,
) -> int:
    """Build the report straight from in-memory records (DB-unavailable fallback).

    Mirrors `generate_excel`, including the roster-driven agency order, so the
    fallback sheet is the same document the DB would have produced.
    """
    kept = [r for r in records if r.get("ref_number") or r.get("opportunity_url")]
    groups, notes = _group(kept, roster or [])
    return workbook.build(groups, out_path, notes)
