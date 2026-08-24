"""Database models for EMMA (eMaryland Marketplace Advantage) scrape runs and the
public solicitations scraped from them.

EMMA is an Ivalua procurement platform (same product as North Dakota's ND Buys);
the "Public Solicitations" grid renders a fixed set of columns (ID, Title, Status,
Due/Close Date, category, type, issuing agency, …) and every row carries a stable
Ivalua object id (`data-id`) used here as the dedup key. The complete original row
is always preserved in `raw_data`.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class EmmaRun(Base):
    """One row per EMMA scrape run."""

    __tablename__ = "emma_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A human summary of the filters used for the run (category/type/status).
    search: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["EmmaBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class EmmaBid(Base):
    """One row per solicitation scraped from the Public Solicitations grid."""

    __tablename__ = "emma_bids"
    __table_args__ = (UniqueConstraint("run_id", "emma_id", name="uq_emma_run_emma_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("emma_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Ivalua object id from the grid row's data-id — the stable dedup key.
    emma_id: Mapped[str | None] = mapped_column(String(64), index=True)
    bpm_code: Mapped[str | None] = mapped_column(String(64), index=True)   # the visible "ID" (BPM…)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64))
    close_date: Mapped[str | None] = mapped_column(String(64))             # Due / Close Date
    publish_date: Mapped[str | None] = mapped_column(String(64))
    main_category: Mapped[str | None] = mapped_column(Text)
    solicitation_type: Mapped[str | None] = mapped_column(String(128))
    issuing_agency: Mapped[str | None] = mapped_column(Text)
    time_remaining: Mapped[str | None] = mapped_column(String(128))
    award_status: Mapped[str | None] = mapped_column(String(128))
    procurement_officer: Mapped[str | None] = mapped_column(Text)
    detail_url: Mapped[str | None] = mapped_column(Text)
    # The filters that surfaced this row (blank for an unfiltered run).
    matched_filters: Mapped[str | None] = mapped_column(Text)
    # Names of the documents downloaded for this solicitation (Stage 2).
    documents: Mapped[list] = mapped_column(JSONB, default=list)

    # Complete original scraped record: {field -> value}.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["EmmaRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("bpm_code", "ID"),
    ("title", "Title"),
    ("status", "Status"),
    ("close_date", "Due / Close Date"),
    ("publish_date", "Publish Date"),
    ("main_category", "Main Category"),
    ("solicitation_type", "Solicitation Type"),
    ("issuing_agency", "Issuing Agency"),
    ("time_remaining", "Time Remaining"),
    ("award_status", "Award Status"),
    ("procurement_officer", "Procurement Officer / Buyer"),
    ("detail_url", "Bid Link"),
    ("matched_filters", "Matched Filters"),
]
