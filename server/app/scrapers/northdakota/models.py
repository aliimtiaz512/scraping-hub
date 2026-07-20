"""Database models for North Dakota (ND Buys / Ivalua) scrape runs and the
public solicitation requests scraped from them.

The portal is an Ivalua procurement platform; the "Public Solicitation Requests"
grid renders a fixed set of columns (RFx Name, publication/bid dates, commodities,
status), and every row carries a stable Ivalua object id (`data-id`) used here as
the dedup key. The complete original row is always preserved in `raw_data`.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class NorthDakotaRun(Base):
    """One row per North Dakota scrape run."""

    __tablename__ = "northdakota_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The search criteria used for the run (keyword and/or commodity).
    search: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["NorthDakotaBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class NorthDakotaBid(Base):
    """One row per solicitation scraped from the Public Solicitation Requests grid."""

    __tablename__ = "northdakota_bids"
    __table_args__ = (UniqueConstraint("run_id", "rfp_id", name="uq_northdakota_run_rfp"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("northdakota_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Ivalua object id from the grid row's data-id — the stable dedup key.
    rfp_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(Text)          # RFx Name
    pub_begin_date: Mapped[str | None] = mapped_column(String(64))  # Publication begin date
    pub_end_date: Mapped[str | None] = mapped_column(String(64))    # Publication end date
    begin_date: Mapped[str | None] = mapped_column(String(64))      # Begin
    close_date: Mapped[str | None] = mapped_column(String(64))      # End (bid due)
    commodity: Mapped[str | None] = mapped_column(Text)             # Link Solicitation - Commodities
    remaining_time: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(64))
    detail_url: Mapped[str | None] = mapped_column(Text)
    # The keyword/commodity search that surfaced this row (blank for an unfiltered run).
    matched_keyword: Mapped[str | None] = mapped_column(Text)

    # Complete original scraped record: {field -> value}.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["NorthDakotaRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("rfp_id", "RFP ID"),
    ("title", "RFx Name"),
    ("pub_begin_date", "Publication Begin"),
    ("pub_end_date", "Publication End"),
    ("begin_date", "Begin"),
    ("close_date", "End (Bid Due)"),
    ("commodity", "Commodities"),
    ("remaining_time", "Remaining Time"),
    ("status", "Status"),
    ("detail_url", "Detail URL"),
    ("matched_keyword", "Matched Keyword"),
]
