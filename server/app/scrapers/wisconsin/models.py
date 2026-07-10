"""Database models for Wisconsin eSupplier (PeopleSoft) scrape runs and bids."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class WisconsinRun(Base):
    """One row per Wisconsin eSupplier scrape run."""

    __tablename__ = "wisconsin_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The search criteria used for the run (keyword / agency / NIGP code).
    search: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["WisconsinBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class WisconsinBid(Base):
    """One row per solicitation scraped from the Current Solicitations grid."""

    __tablename__ = "wisconsin_bids"
    __table_args__ = (UniqueConstraint("run_id", "event_number", name="uq_wisconsin_run_event"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("wisconsin_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event_number: Mapped[str | None] = mapped_column(String(64), index=True)
    solicitation_reference: Mapped[str | None] = mapped_column(String(128))
    event_type: Mapped[str | None] = mapped_column(String(128))
    event_title: Mapped[str | None] = mapped_column(Text)
    agency: Mapped[str | None] = mapped_column(Text)
    event_status: Mapped[str | None] = mapped_column(String(128))
    due_datetime: Mapped[str | None] = mapped_column(String(64))

    run: Mapped["WisconsinRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("event_number", "Event Number"),
    ("solicitation_reference", "Solicitation Reference #"),
    ("event_type", "Event Type"),
    ("event_title", "Event Title"),
    ("agency", "Agency"),
    ("event_status", "Event Status"),
    ("due_datetime", "Due Date/Time"),
]
