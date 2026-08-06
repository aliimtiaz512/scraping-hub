"""Database models for Unison Marketplace scrape runs and the buyer requests
scraped from them.

The scrape logic lives in the vendored engine (server/scrappers/unison/); this
is only the hub-native storage layer. Layout mirrors the other portals (runs +
requests, DB-first with an Excel fallback).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    search: Mapped[str | None] = mapped_column(Text)   # a human-readable summary
    # The portal's "Filter By" criteria this run used: the option's value and
    # its label ("3" / "Posted Last 7 Days"). -1 is "Select Criteria" — no
    # filter, the whole listing.
    filter_id: Mapped[str | None] = mapped_column(String(8))
    filter_label: Mapped[str | None] = mapped_column(Text)
    pages_scraped: Mapped[int] = mapped_column(Integer, default=0)
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

    # -- dashboard listing --------------------------------------------------
    buyer_number: Mapped[str | None] = mapped_column(String(255), index=True)  # full "Buy #"
    # The part after the underscore in a Buy # ("1210780_01" -> "01"), and "0"
    # when there is none — a count of zero rather than a blank cell. The portal
    # uses the suffix to sequence reposts of the same buy.
    bid_upload_count: Mapped[str | None] = mapped_column(String(16), default="0")
    buyer_description: Mapped[str | None] = mapped_column(Text)
    buyer: Mapped[str | None] = mapped_column(Text)
    end_date: Mapped[str | None] = mapped_column(String(255))
    detail_url: Mapped[str | None] = mapped_column(Text)

    # -- General Buy Information (detail page) ------------------------------
    solicitation_number: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(Text)
    subcategory: Mapped[str | None] = mapped_column(Text)
    naics: Mapped[str | None] = mapped_column(Text)
    naics_size_standard: Mapped[str | None] = mapped_column(Text)
    sam_contract_opportunity: Mapped[str | None] = mapped_column(String(64))
    set_aside: Mapped[str | None] = mapped_column(Text)
    end_time: Mapped[str | None] = mapped_column(String(64))
    seller_question_deadline: Mapped[str | None] = mapped_column(Text)
    delivery: Mapped[str | None] = mapped_column(Text)
    repost_reason: Mapped[str | None] = mapped_column(Text)

    # -- Shipping Information (the place-of-performance signal) -------------
    shipping_city: Mapped[str | None] = mapped_column(Text)
    shipping_state: Mapped[str | None] = mapped_column(String(128))
    shipping_zip: Mapped[str | None] = mapped_column(String(32))

    # -- line items, attachments --------------------------------------------
    line_item_count: Mapped[int] = mapped_column(Integer, default=0)
    line_items: Mapped[list] = mapped_column(JSONB, default=list)     # [{no, description, qty, unit}]
    seller_attachments_required: Mapped[str | None] = mapped_column(Text)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0)
    attachments: Mapped[list] = mapped_column(JSONB, default=list)    # [{name, size, file}]

    # -- evaluation ----------------------------------------------------------
    # PURSUE | REJECT | MANUAL_REVIEW, and the standard reason phrase. These two
    # are the only evaluation fields the export carries; the rest are kept for
    # auditing a decision after the fact, never shown to the reader.
    decision: Mapped[str | None] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    requirement_type: Mapped[str | None] = mapped_column(String(16))  # HARDWARE | SERVICE
    rule: Mapped[str | None] = mapped_column(String(16))              # A | B12 | C5 | kill_word…
    location: Mapped[str | None] = mapped_column(String(32))
    requirement_hinted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Everything else read off the detail page — Bidding Requirements, Buy
    # Terms, and any General Info field the portal adds later. Internal: it
    # feeds the evaluator's full text and is never exported.
    detail_sections: Mapped[dict] = mapped_column(JSONB, default=dict)
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["UnisonRun"] = relationship(back_populates="requests")

    @property
    def attachment_names(self) -> str:
        """The downloaded documents, comma-joined — the export's one attachment
        column. The files themselves travel in the run's ZIP."""
        return ", ".join(
            str(a.get("name") or "") for a in (self.attachments or []) if a.get("name")
        )


# Column order for the generated Excel, mapped to friendly headers.
#
# Decision and Reason lead, as they do in the SAM export. The evaluator's
# working-out — requirement type, matched rule, detected location, the section
# text, the document text it read — is deliberately absent: it exists to reach
# the decision, not to be read alongside it.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("buyer_number", "Buy#"),
    ("bid_upload_count", "Bid Upload Count"),
    ("decision", "Decision"),
    ("reason", "Reason"),
    ("buyer_description", "Buy Description"),
    ("solicitation_number", "Solicitation #"),
    ("category", "Category"),
    ("subcategory", "Subcategory"),
    ("naics", "NAICS"),
    ("naics_size_standard", "NAICS Code Size Standard"),
    ("sam_contract_opportunity", "SAM Contract Opportunity"),
    ("set_aside", "Set-Aside Requirement"),
    ("buyer", "Buyer"),
    ("end_date", "End Date"),
    ("end_time", "End Time"),
    ("seller_question_deadline", "Seller Question Deadline"),
    ("delivery", "Delivery"),
    ("repost_reason", "Repost Reason"),
    ("shipping_city", "Shipping City"),
    ("shipping_state", "Shipping State"),
    ("shipping_zip", "Shipping Zip"),
    ("line_item_count", "Line Items"),
    ("seller_attachments_required", "Seller Attachments Required"),
    ("attachment_names", "Buy Attachments"),
    ("detail_url", "Detail URL"),
]
