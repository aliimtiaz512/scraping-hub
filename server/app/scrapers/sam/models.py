"""Database models for SAM.gov scrape runs and the bids scraped from them.

The scrape + evaluation logic lives in the vendored engine
(server/scrappers/sam/); this is only the hub-native storage layer. Each bid
carries the nine scraped fields plus the evaluator's `decision`/`reason` and the
complete original record in `raw_data`. Layout mirrors the other hub portals
(runs + bids, DB-first with an Excel fallback).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SamRun(Base):
    """One row per SAM.gov scrape run."""

    __tablename__ = "sam_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The filters used for the run (date range / NAICS / award-notice), as a summary.
    search: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["SamBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class SamBid(Base):
    """One row per bid scraped and evaluated from SAM.gov."""

    __tablename__ = "sam_bids"
    __table_args__ = (UniqueConstraint("run_id", "notice_id", name="uq_sam_run_notice"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("sam_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    notice_id: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str | None] = mapped_column(Text)               # Notice Title
    department: Mapped[str | None] = mapped_column(Text)          # Department/Ind. Agency
    subtier: Mapped[str | None] = mapped_column(Text)
    office: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    updated_date: Mapped[str | None] = mapped_column(String(64))
    bid_repeat_count: Mapped[int] = mapped_column(Integer, default=0)
    naics_code: Mapped[str | None] = mapped_column(String(32))
    naics_title: Mapped[str | None] = mapped_column(Text)
    date_offers_due: Mapped[str | None] = mapped_column(String(64))
    published_date: Mapped[str | None] = mapped_column(String(64))
    # Evaluator output (two-mode): PURSUE | REJECT (PENDING | ERROR only on an
    # evaluation exception — not a business decision mode).
    decision: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)

    # Complete original scraped record: {field -> value}.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["SamRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers (matches the
# sam-septa export exactly).
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("title", "Notice Title"),
    ("notice_id", "Notice ID"),
    ("decision", "Decision"),
    ("reason", "Reason"),
    ("department", "Department/Ind. Agency"),
    ("description", "Description"),
    ("subtier", "Subtier"),
    ("updated_date", "Updated Date"),
    ("bid_repeat_count", "Bid Repeat Count"),
    ("naics_code", "NAICS Code"),
    ("naics_title", "NAICS Title"),
    ("date_offers_due", "Date Offers Due"),
    ("published_date", "Published Date"),
    ("office", "Office"),
]
