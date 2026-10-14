"""Database models for PPRA (Pakistan Public Procurement Regulatory Authority) scrape runs and tenders."""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PpraRun(Base):
    """One row per PPRA scrape run."""

    __tablename__ = "ppra_runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("ppra_runs.run_id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    filters_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    total_expected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_page: Mapped[int] = mapped_column(Integer, default=0)

    rows_scraped: Mapped[int] = mapped_column(Integer, default=0)
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
    rows_changed: Mapped[int] = mapped_column(Integer, default=0)
    skipped_closing: Mapped[int] = mapped_column(Integer, default=0)
    kept_unverified: Mapped[int] = mapped_column(Integer, default=0)

    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    bids: Mapped[list["PpraBid"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class PpraBid(Base):
    """One row per tender scraped from the PPRA active tenders listing."""

    __tablename__ = "ppra_bids"
    __table_args__ = (UniqueConstraint("run_id", "tender_no", name="uq_ppra_run_tender"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("ppra_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    tender_no: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sector: Mapped[str | None] = mapped_column(Text, nullable=True)
    tender_ref_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    tender_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    nature: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)

    advertised_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    closing_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    detail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    change_status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped["PpraRun"] = relationship(back_populates="bids")


class PpraFilterOption(Base):
    """Dropdown options cached from the PPRA website."""

    __tablename__ = "ppra_filter_options"

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Excel column definition (mapping attribute to header name)
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("tender_no", "Tender No"),
    ("title", "Title"),
    ("tender_ref_no", "Ref No"),
    ("sector", "Sector"),
    ("organization", "Organization"),
    ("department", "Department / Office"),
    ("city", "City"),
    ("status", "Status"),
    ("advertised_on", "Advertised"),
    ("closing_at", "Closing (PKT)"),
    ("detail_url", "Link"),
    ("change_status", "Change"),
]

