"""Database models for BidNet Direct scrape runs and their solicitations.

Mirrors the original standalone BidNet `Bid` model (backend/models.py), re-parented
onto the shared declarative Base, with a `run_id` link and a per-run table so runs
don't clobber each other.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BidnetRun(Base):
    """One row per BidNet Direct scrape run."""

    __tablename__ = "bidnet_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    keyword: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["BidnetBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class BidnetBid(Base):
    """One row per solicitation scraped from BidNet Direct.

    Columns are the same set the original scraper produced.
    """

    __tablename__ = "bidnet_bids"
    __table_args__ = (UniqueConstraint("run_id", "reference_number", name="uq_bidnet_run_ref"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("bidnet_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reference_number: Mapped[str | None] = mapped_column(String(128), index=True)
    solicitation_number: Mapped[str | None] = mapped_column(String(128))
    solicitation_type: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[str | None] = mapped_column(String(64))
    question_acceptance_deadline: Mapped[str | None] = mapped_column(String(64))
    closing_date: Mapped[str | None] = mapped_column(String(64))
    documents_count: Mapped[str | None] = mapped_column(String(32))
    matched_keyword: Mapped[str | None] = mapped_column(Text)

    run: Mapped["BidnetRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers (matches the
# original on-demand export).
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("reference_number", "Reference Number"),
    ("solicitation_number", "Solicitation Number"),
    ("solicitation_type", "Solicitation Type"),
    ("title", "Title"),
    ("publication_date", "Publication Date"),
    ("question_acceptance_deadline", "Question Acceptance Deadline"),
    ("closing_date", "Closing Date"),
    ("documents_count", "Documents Count"),
    ("matched_keyword", "Matched Keyword"),
]
