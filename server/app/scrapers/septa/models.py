"""Database models for SEPTA (vendor procurement portal) scrape runs and the
Open Quotes scraped from them.

SEPTA's "Open Quotes" grid renders a fixed four-column shape — requisition
number, summary, open date, close date. The requisition number is the stable
identity of a quote and is used here as the per-run dedup key; the complete
original scraped row is always preserved in `raw_data`. The layout intentionally
mirrors the North Dakota scraper (runs + bids, DB-first with an Excel fallback)
so storage behaves the same across every portal.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SeptaRun(Base):
    """One row per SEPTA scrape run."""

    __tablename__ = "septa_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The date filter used for the run (YYYY-MM-DD), or a human summary.
    search: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["SeptaBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class SeptaBid(Base):
    """One row per quote scraped from the SEPTA Open Quotes grid."""

    __tablename__ = "septa_bids"
    __table_args__ = (UniqueConstraint("run_id", "requisition_number", name="uq_septa_run_requisition"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("septa_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # The requisition number from the grid — the stable per-run dedup key.
    requisition_number: Mapped[str | None] = mapped_column(String(255), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    open_date: Mapped[str | None] = mapped_column(String(64))
    close_date: Mapped[str | None] = mapped_column(String(64))

    # Complete original scraped record: {field -> value}.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["SeptaRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("requisition_number", "Requisition Number"),
    ("summary", "Summary"),
    ("open_date", "Open Date"),
    ("close_date", "Close Date"),
]
