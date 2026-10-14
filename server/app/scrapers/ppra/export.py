"""Build styled Excel workbook from ppra_bids."""

import logging
from pathlib import Path

from sqlalchemy import select

from app.core import excel_style
from app.db import SessionLocal
from app.scrapers.ppra.models import EXCEL_COLUMNS, PpraBid

logger = logging.getLogger(__name__)


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Build PPRA Tenders Excel workbook from ppra_bids for run_id."""
    session = SessionLocal()
    try:
        rows = session.execute(
            select(PpraBid).where(PpraBid.run_id == run_id).order_by(PpraBid.page_no, PpraBid.position)
        ).scalars().all()
    finally:
        session.close()

    workbook, sheet = excel_style.new_workbook("PPRA Tenders")
    headers = [hdr for _, hdr in EXCEL_COLUMNS]

    def _val(bid: PpraBid, attr: str):
        v = getattr(bid, attr, None)
        if attr == "closing_at" and v is not None:
            # Format closing date
            return str(v)
        return v

    excel_style.write_table(
        sheet,
        headers,
        ([_val(bid, attr) for attr, _ in EXCEL_COLUMNS] for bid in rows),
    )

    workbook.save(str(out_path))
    logger.info("[run %s] generated PPRA Excel with %d rows at %s", run_id, len(rows), out_path)
    return len(rows)

