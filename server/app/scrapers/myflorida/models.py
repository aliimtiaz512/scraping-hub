"""Database models for MFMP scrape runs and the bids exported from them."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ScrapeRun(Base):
    """One row per MFMP scrape run — mirrors the in-memory run tracked by run_manager."""

    __tablename__ = "scrape_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    category: Mapped[str | None] = mapped_column(String(64))
    category_label: Mapped[str | None] = mapped_column(String(128))
    priority: Mapped[str | None] = mapped_column(String(32))
    codes: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["Bid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Bid(Base):
    """One row per bid from the MFMP Excel export.

    Known columns are mapped best-effort from the Excel headers; the complete
    original row is always preserved in `raw_data` so nothing is lost even when
    the export's columns differ from what we mapped.
    """

    __tablename__ = "mfmp_bids"
    __table_args__ = (UniqueConstraint("run_id", "ad_number", name="uq_bid_run_ad"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("scrape_runs.run_id", ondelete="CASCADE"), index=True)

    # Run context (denormalized for easy querying without a join).
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    priority: Mapped[str | None] = mapped_column(String(32))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Best-effort mapped bid fields from the export.
    ad_number: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    agency: Mapped[str | None] = mapped_column(Text)
    ad_type: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    commodity_codes: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(Text)
    contact_phone: Mapped[str | None] = mapped_column(String(64))
    estimated_amount: Mapped[float | None] = mapped_column(Numeric(18, 2))
    ad_date: Mapped[str | None] = mapped_column(String(64))
    open_date: Mapped[str | None] = mapped_column(String(64))
    close_date: Mapped[str | None] = mapped_column(String(64))

    # Complete original Excel row: {header -> value}.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["ScrapeRun"] = relationship(back_populates="bids")
