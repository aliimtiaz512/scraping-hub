"""Database models for RideMetro (Euna Supplier Network) scrape runs.

A run no longer covers one portal: it walks every agency in the account's Euna
Supplier Network and reads each one's Open Public Opportunities list, so a bid
row is identified by *agency + ref number*, not by ref number alone (two
agencies can and do issue the same reference).
"""

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

    # Network sweep bookkeeping.
    agencies_found: Mapped[int] = mapped_column(Integer, default=0)    # rows on My Network
    agencies_scraped: Mapped[int] = mapped_column(Integer, default=0)  # of those, Complete + read
    # The full My Network roster for this run, in portal order:
    # [{name, url, status, skipped, opportunities, error}]. This is what lets the
    # Excel show an agency that was visited and had nothing open — a fact no bid
    # row can carry — and keeps the report's agency order stable when it is
    # rebuilt from the DB.
    agencies: Mapped[list] = mapped_column(JSONB, default=list)

    bids: Mapped[list["RideMetroBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RideMetroBid(Base):
    """One opportunity from one agency's Open Public Opportunities list.

    Columns mirror that list's own columns. The complete scraped field map is
    also kept in `raw_data` so extra/renamed portal columns are not lost — the
    list is not identical across agencies (only some publish a Department).
    """

    __tablename__ = "ridemetro_bids"
    __table_args__ = (
        UniqueConstraint("run_id", "agency", "ref_number", name="uq_ridemetro_run_agency_ref"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("ridemetro_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Which agency's portal this came from.
    agency: Mapped[str | None] = mapped_column(Text, index=True)
    agency_url: Mapped[str | None] = mapped_column(Text)

    # Opportunities-list fields.
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
    project_description: Mapped[str | None] = mapped_column(Text)

    # Provenance.
    opportunity_url: Mapped[str | None] = mapped_column(Text)
    zip_filename: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["RideMetroRun"] = relationship(back_populates="bids")


# The report's data columns, in order, as (bid attribute, sheet header). These
# are the columns written under each agency banner in the Excel — the agency
# itself is the banner, so it is deliberately not repeated in every row.
SHEET_COLUMNS: list[tuple[str, str]] = [
    ("status", "Status"),
    ("ref_number", "Ref #"),
    ("project", "Project"),
    ("department", "Department"),
    ("close_date", "Closing Date"),
    ("days_left", "Days Left"),
    ("opportunity_url", "Bid URL"),
]

# What the /ridemetro/bids API returns per row: the sheet columns plus the
# agency, which the sheet carries in its banner rather than in the row.
API_FIELDS: list[str] = ["agency", "agency_url", *[attr for attr, _ in SHEET_COLUMNS]]
