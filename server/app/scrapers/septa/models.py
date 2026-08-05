"""Database models for SEPTA (vendor procurement portal) scrape runs and the
two grids scraped from them.

One run reads both of the portal's modules, and each gets its own table:

* **Open Quotes** -> `septa_bids`. A parts-requisition feed in a fixed
  four-column shape — requisition number, summary, open date, close date — with
  the requisition number as the stable per-run dedup key;
* **Open Bids** -> `septa_open_bids`. The Bid module's solicitations, the same
  four-column shape under its own names, keyed by bid number.

They are kept apart rather than merged behind a "module" flag because the key
column means something different in each, and a shared one would only be unique
within half the rows. The complete original scraped row is preserved in
`raw_data` on both. The layout intentionally mirrors the North Dakota scraper
(runs + bids, DB-first with an Excel fallback) so storage behaves the same
across every portal.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SeptaRun(Base):
    """One row per SEPTA scrape run."""

    __tablename__ = "septa_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The date filter used for the run (YYYY-MM-DD), or a human summary.
    search: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["SeptaBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    open_bids: Mapped[list["SeptaOpenBid"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class SeptaBid(Base):
    """One row per quote scraped from the SEPTA Open Quotes grid."""

    __tablename__ = "septa_bids"
    __table_args__ = (UniqueConstraint("run_id", "requisition_number", name="uq_septa_run_requisition"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("septa_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # The requisition number from the grid — the stable per-run dedup key.
    requisition_number: Mapped[str | None] = mapped_column(String(255), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    open_date: Mapped[str | None] = mapped_column(String(64))
    close_date: Mapped[str | None] = mapped_column(String(64))

    # Provenance: which niche the run searched, and which of its terms actually
    # surfaced this quote. A run searches every keyword and commodity code of a
    # niche separately, so one requisition is often found by several of them —
    # matched_terms holds all of them, comma-joined.
    niche: Mapped[str | None] = mapped_column(String(64), index=True)
    matched_terms: Mapped[str | None] = mapped_column(Text)

    # Complete original scraped record: {field -> value}.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["SeptaRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers.
#
# `niche` and `matched_terms` are deliberately absent: a run no longer searches
# per-term, so there is no niche to record and nothing for a term to have
# matched. The columns remain on the table (nullable, unwritten) so quotes
# scraped under the old per-term searches stay readable.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("requisition_number", "Requisition Number"),
    ("summary", "Summary"),
    ("open_date", "Open Date"),
    ("close_date", "Close Date"),
]


class SeptaOpenBid(Base):
    """One row per solicitation scraped from the SEPTA **Open Bids** grid.

    A separate table from `septa_bids`, not a flag on it, because the two grids
    describe different things: `septa_bids` holds Open *Quotes*, which are parts
    requisitions keyed by requisition number, while this holds the Bid module's
    solicitations — the actual RFPs — keyed by bid number. Folding them together
    would mean one column meaning two things and a dedup key that is only unique
    within half the rows.
    """

    __tablename__ = "septa_open_bids"
    __table_args__ = (UniqueConstraint("run_id", "bid_number", name="uq_septa_run_bid_number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("septa_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # The bid number from the grid — the stable per-run dedup key.
    bid_number: Mapped[str | None] = mapped_column(String(255), index=True)
    # The bid title, which is what the blacklist is matched against.
    title: Mapped[str | None] = mapped_column(Text)
    open_date: Mapped[str | None] = mapped_column(String(64))
    close_date: Mapped[str | None] = mapped_column(String(64))

    # Complete original scraped record: {field -> value}.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["SeptaRun"] = relationship(back_populates="open_bids")


# Column order for the Open Bids sheet. Same four-column shape as the quotes
# sheet, under the Bid module's own names.
OPEN_BID_EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("bid_number", "Bid Number"),
    ("title", "Bid Title"),
    ("open_date", "Open Date"),
    ("close_date", "Close Date"),
]
