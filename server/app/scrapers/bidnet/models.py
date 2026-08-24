"""Database models for BidNet Direct scrape runs and their solicitations.

Mirrors the original standalone BidNet `Bid` model (backend/models.py), re-parented
onto the shared declarative Base, with a `run_id` link and a per-run table so runs
don't clobber each other.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BidnetRun(Base):
    """One row per BidNet Direct scrape run."""

    __tablename__ = "bidnet_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    keyword: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bids: Mapped[list["BidnetBid"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class BidnetBid(Base):
    """One row per solicitation scraped from BidNet Direct.

    Columns are the same set the original scraper produced.
    """

    __tablename__ = "bidnet_bids"
    __table_args__ = (UniqueConstraint("run_id", "reference_number", name="uq_bidnet_run_ref"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("bidnet_runs.run_id", ondelete="CASCADE"), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reference_number: Mapped[str | None] = mapped_column(String(128), index=True)
    solicitation_number: Mapped[str | None] = mapped_column(String(128))
    solicitation_type: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[str | None] = mapped_column(String(64))
    question_acceptance_deadline: Mapped[str | None] = mapped_column(String(64))
    closing_date: Mapped[str | None] = mapped_column(String(64))
    # Historical only. The scraper stopped opening the documents tab when
    # attachment downloading was retired (scraper.DOWNLOAD_DOCUMENTS), so rows
    # written from then on leave this NULL. The column is kept so rows scraped
    # before that still read back, and is no longer exported.
    documents_count: Mapped[str | None] = mapped_column(String(32))
    # Every keyword of the run's niche that surfaced this solicitation, comma-
    # joined — the niche is searched one keyword at a time and the same bid is
    # often found by several.
    matched_keyword: Mapped[str | None] = mapped_column(Text)
    # Which of our searches surfaced this bid. A niche run stores its niche
    # label; the member-agency sweep (member_agencies.py) searches no niche at
    # all and stores the issuing agency instead — same column, because it
    # answers the same question ("under what heading did this reach us") and
    # occupies the same place in the export.
    #
    # 255, not the 64 that sized it when a niche label was all it ever held:
    # agencies name themselves at length ("City and County of Denver Climate
    # Action, Sustainability & Resiliency" is 68 characters) and one overlong
    # value aborts the whole run's insert, not just its own row. The scraper
    # caps what it writes as well — see member_agencies.MAX_AGENCY_LENGTH.
    niche: Mapped[str | None] = mapped_column(String(255), index=True)
    # How complete this record is — see scraper.RECORD_STATUSES. Every bid the
    # run opened is stored, including ones whose detail page could not be read,
    # so a scraped solicitation is never silently absent from the output.
    status: Mapped[str | None] = mapped_column(String(32), index=True)
    # The solicitation's detail page, so a PARTIAL_DATA / EXTRACTION_FAILED row
    # can be chased by hand.
    detail_url: Mapped[str | None] = mapped_column(Text)

    # Complete original scraped record: {field -> value}. Preserved so nothing is
    # lost even when the portal's fields differ from what we mapped.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["BidnetRun"] = relationship(back_populates="bids")


# Column order for the generated Excel, mapped to friendly headers. **The same
# list for both execution modes** — a per-niche sheet from "Run all niches" and
# the consolidated sheet from "Run all member agency bids" have identical
# columns in identical order, so the two can be read (and pasted) side by side.
#
# The bid leads and the run's own bookkeeping follows it. `Status` and `Niche`
# used to sit in A and B, which put two columns a reader rarely sorts on in
# front of the reference number they identify the row by — and pushed the title
# and the dates far enough right to need scrolling. They are now the last two:
# every column up to `Detail URL` is what the portal published about the
# solicitation, then `Niche` (which of our searches surfaced it) and `Status`
# (how completely we read it).
#
# Nothing reads these by position. Both export paths iterate this list to build
# the header row and each data row, and `excel_style.write_table` sizes the
# columns from what it is given — so this list is the whole of the column order,
# and reordering it is the whole of the change.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("reference_number", "Reference Number"),
    ("solicitation_number", "Solicitation Number"),
    ("solicitation_type", "Solicitation Type"),
    ("title", "Title"),
    ("publication_date", "Publication Date"),
    ("question_acceptance_deadline", "Question Acceptance Deadline"),
    ("closing_date", "Closing Date"),
    ("matched_keyword", "Matched Keyword"),
    ("detail_url", "Detail URL"),
    # The last two, in this order, in both modes: `Niche` names the search that
    # surfaced the bid (the niche's label, or the issuing member agency in the
    # sweep), and `Status` says how completely we read it.
    ("niche", "Niche"),
    ("status", "Status"),
]
