"""Database models for RideMetro (Bonfire) scrape runs and their opportunities."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RideMetroRun(Base):
    """One row per RideMetro scrape run."""

    __tablename__ = "ridemetro_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opportunities_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["RideMetroBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RideMetroBid(Base):
    """One row per opportunity scraped from the RideMetro portal.

    Columns mirror the portal's "Project Details" section. The complete scraped
    field map is also kept in `raw_data` so extra/renamed portal fields are not
    lost.
    """

    __tablename__ = "ridemetro_bids"
    __table_args__ = (UniqueConstraint("run_id", "ref_number", name="uq_ridemetro_run_ref"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("ridemetro_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Project Details fields.
    project: Mapped[str | None] = mapped_column(Text)
    ref_number: Mapped[str | None] = mapped_column(String(128), index=True)  # "Ref. #"
    department: Mapped[str | None] = mapped_column(Text)
    opportunity_type: Mapped[str | None] = mapped_column(String(128))  # "Type"
    status: Mapped[str | None] = mapped_column(String(64))
    open_date: Mapped[str | None] = mapped_column(String(64))
    intent_to_bid_due_date: Mapped[str | None] = mapped_column(String(64))
    question_due_date: Mapped[str | None] = mapped_column(String(64))
    close_date: Mapped[str | None] = mapped_column(String(64))
    days_left: Mapped[str | None] = mapped_column(String(64))
    contact_information: Mapped[str | None] = mapped_column(Text)
    project_description: Mapped[str | None] = mapped_column(Text)

    # Provenance.
    opportunity_url: Mapped[str | None] = mapped_column(Text)
    zip_filename: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["RideMetroRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("project", "Project"),
    ("ref_number", "Ref. #"),
    ("department", "Department"),
    ("opportunity_type", "Type"),
    ("status", "Status"),
    ("open_date", "Open Date"),
    ("intent_to_bid_due_date", "Intent to Bid Due Date"),
    ("question_due_date", "Question Due Date"),
    ("close_date", "Close Date"),
    ("days_left", "Days Left"),
    ("contact_information", "Contact Information"),
    ("project_description", "Project Description"),
    ("opportunity_url", "Opportunity URL"),
    ("zip_filename", "Documents Zip"),
]
