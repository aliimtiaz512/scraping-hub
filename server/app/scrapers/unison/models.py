"""Database models for Unison Marketplace scrape runs and the buyer requests
scraped from them.

The scrape logic lives in the vendored engine (server/scrappers/unison/); this
is only the hub-native storage layer. Layout mirrors the other portals (runs +
requests, DB-first with an Excel fallback).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class UnisonRun(Base):
    """One row per Unison Marketplace scrape run."""

    __tablename__ = "unison_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search: Mapped[str | None] = mapped_column(Text)   # the filter_by value, if any
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requests: Mapped[list["UnisonRequest"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class UnisonRequest(Base):
    """One row per buyer request scraped from the Unison Marketplace dashboard."""

    __tablename__ = "unison_requests"
    __table_args__ = (UniqueConstraint("run_id", "buyer_number", name="uq_unison_run_buyer"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("unison_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    buyer_number: Mapped[str | None] = mapped_column(String(255), index=True)  # "Buyer#"
    buyer_description: Mapped[str | None] = mapped_column(Text)
    buyer: Mapped[str | None] = mapped_column(Text)
    end_date: Mapped[str | None] = mapped_column(String(255))

    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["UnisonRun"] = relationship(back_populates="requests")


# Column order for the generated Excel, mapped to friendly headers.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("buyer_number", "Buyer#"),
    ("buyer_description", "Buyer Description"),
    ("buyer", "Buyer"),
    ("end_date", "End Date"),
]
