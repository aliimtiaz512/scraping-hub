"""Repository operations for PPRA runs, bids, and local mirroring."""

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dateutil import parser as dt_parser
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.core.closing_filter import parse_close_date
from app.db import SessionLocal
from app.scrapers.ppra.models import PpraBid, PpraRun

logger = logging.getLogger(__name__)


def _safe_date(val: Any) -> date | None:
    """Coerce string/date into date or None. Never returns invalid strings to Postgres."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    # A genuine date must mention a 4-digit year; strings like "2/25-26" or "10/12" are notice refs
    if not re.search(r"\b20\d{2}\b", s):
        return None
    try:
        parsed = parse_close_date(s)
        if parsed:
            return parsed
    except Exception:
        pass
    try:
        # Fallback dateutil parser
        dt = dt_parser.parse(s)
        return dt.date()
    except Exception:
        return None


def _safe_datetime(val: Any) -> datetime | None:
    """Coerce string/datetime into UTC-aware datetime or None. Never returns invalid strings."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, tzinfo=timezone.utc)
    s = str(val).strip()
    if not re.search(r"\b20\d{2}\b", s):
        return None
    try:
        dt = dt_parser.parse(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    d = _safe_date(val)
    if d:
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return None


def init_run_in_db(
    run_id: str,
    filters: dict[str, Any],
    filters_hash: str,
    options: dict[str, Any],
    parent_run_id: str | None = None,
) -> None:
    session = SessionLocal()
    try:
        run = PpraRun(
            run_id=run_id,
            parent_run_id=parent_run_id,
            status="running",
            filters=filters,
            filters_hash=filters_hash,
            options=options,
            started_at=datetime.now(),
        )
        session.add(run)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_run_progress(run_id: str, **fields: Any) -> None:
    session = SessionLocal()
    try:
        run = session.get(PpraRun, run_id)
        if run:
            for k, v in fields.items():
                setattr(run, k, v)
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def get_parent_run_hashes(parent_run_id: str) -> dict[str, str]:
    """Map of {tender_no: content_hash} from the parent run to detect changes."""
    session = SessionLocal()
    try:
        rows = session.execute(
            select(PpraBid.tender_no, PpraBid.content_hash).where(PpraBid.run_id == parent_run_id)
        ).all()
        return {r[0]: r[1] for r in rows}
    finally:
        session.close()


def upsert_bids(
    run_id: str,
    page_no: int,
    records: list[dict[str, Any]],
    parent_hashes: dict[str, str] | None = None,
) -> tuple[int, int, int]:
    """Upsert bids into ppra_bids. Returns (new_count, changed_count, unchanged_count)."""
    if not records:
        return 0, 0, 0

    session = SessionLocal()
    new_c = 0
    changed_c = 0
    unchanged_c = 0

    try:
        for rec in records:
            tender_no = rec["tender_no"]
            content_hash = rec["content_hash"]
            change_status = "new"

            if parent_hashes is not None:
                if tender_no not in parent_hashes:
                    change_status = "new"
                    new_c += 1
                elif parent_hashes[tender_no] != content_hash:
                    change_status = "changed"
                    changed_c += 1
                else:
                    change_status = "unchanged"
                    unchanged_c += 1
            else:
                new_c += 1

            values = {
                "run_id": run_id,
                "page_no": page_no,
                "position": rec["position"],
                "tender_no": tender_no,
                "title": rec["title"],
                "description": rec.get("description"),
                "sector": rec.get("sector"),
                "tender_ref_no": rec.get("tender_ref_no"),
                "tender_type": rec.get("tender_type"),
                "category": rec.get("category"),
                "nature": rec.get("nature"),
                "organization": rec.get("organization"),
                "department": rec.get("department"),
                "city": rec.get("city"),
                "country": rec.get("country"),
                "status": rec.get("status"),
                "advertised_on": _safe_date(rec.get("advertised_on")),
                "closing_at": _safe_datetime(rec.get("closing_at")),
                "detail_url": rec.get("detail_url"),
                "invoice_url": rec.get("invoice_url"),
                "content_hash": content_hash,
                "change_status": change_status,
                "raw_data": rec.get("raw_data") or {},
            }

            stmt = pg_insert(PpraBid).values(**values).on_conflict_do_update(
                constraint="uq_ppra_run_tender",
                set_={k: v for k, v in values.items() if k not in ("run_id", "tender_no")},
            )
            session.execute(stmt)

        session.commit()
        return new_c, changed_c, unchanged_c
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def append_to_mirror(run_id: str, records: list[dict[str, Any]]) -> None:
    """Append records to data/ppra/{run_id}/tenders.jsonl if mirroring is active."""
    if not getattr(settings, "ppra_local_mirror", True):
        return

    try:
        mirror_dir = Path("data/ppra") / run_id
        mirror_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = mirror_dir / "tenders.jsonl"

        with open(jsonl_path, "a", encoding="utf-8") as f:
            for r in records:
                clean_r = {k: v for k, v in r.items() if k != "raw_data"}
                f.write(json.dumps(clean_r, default=str) + "\n")
    except Exception as exc:
        logger.warning("[run %s] local mirror write failed: %s", run_id, exc)

