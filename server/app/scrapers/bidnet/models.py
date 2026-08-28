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
    # Text, not String(64), despite the names. These are whatever the issuing
    # agency typed into a date field, and across five hundred member agencies
    # that is regularly not a date — "See specification for the submission
    # schedule", a date plus a timezone plus a parenthetical, an instruction to
    # refer to an addendum. At 64 characters one such value raised
    # StringDataRightTruncation, and because the run saves in one transaction it
    # took all 1,859 of that run's bids down with it. Nothing reads these as
    # dates in the database (the close-date rule parses them in Python), so
    # there was never anything for the limit to protect.
    publication_date: Mapped[str | None] = mapped_column(Text)
    question_acceptance_deadline: Mapped[str | None] = mapped_column(Text)
    closing_date: Mapped[str | None] = mapped_column(Text)
    # How many attachments the solicitation carries. Counted off the documents
    # tab, never downloaded (scraper.DOWNLOAD_DOCUMENTS is off and stays off).
    # NULL means the count could not be read, which is deliberately distinct
    # from "0" — a bid we could not ask about is not a bid with no documents.
    # Populated by the member agency sweep; a niche run leaves it NULL rather
    # than pay a tab render per bid for a column its sheet does not carry.
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
    # The last two, in this order, in every mode: `Niche` names the search that
    # surfaced the bid (the niche's label, or the issuing member agency in the
    # sweep), and `Status` says how completely we read it.
    ("niche", "Niche"),
    ("status", "Status"),
]

# The member agency sweep's own layout. Three columns differ from the niche
# layout above:
#
# * **`Matched Keyword` is gone.** A niche run searches twenty-odd terms one at a
#   time and that column says which of them surfaced the bid — the single most
#   useful thing to know about a row there. A sweep types nothing into the box
#   at all, so the column could only ever be blank, and a column that is blank
#   in every row of every sheet is noise the reader has to learn to skip.
# * **`Documents` is present**, at the end before `Status`. It is the triage
#   signal a keyword-less sweep is short of: a bid carrying fourteen attachments
#   is a different proposition from one carrying none, and with nothing else
#   narrowing the list it is often the fastest way to sort it.
# * **`Niche` is gone.** The niche layout uses that column for "which of our
#   searches surfaced this bid"; a sweep searches nothing, so what went there
#   was the issuing member agency, briefly under an `Agency` header. Removed at
#   the reviewer's request — the sheet is a flat list of solicitations.
#
#   The `niche` **field** is unaffected: `_extract_agency` still reads the
#   agency, it is still stored on the bid row, and the run log's per-agency
#   funnel breakdown still reports it. Only the spreadsheet column is gone, so
#   an agency asked for later needs no re-scrape.
#
# What remains is the bid's own fields, then how completely we read it — the
# same shape as the niche layout's tail, so the two sheets still read side by
# side on the columns they share.
MEMBER_AGENCY_EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("reference_number", "Reference Number"),
    ("solicitation_number", "Solicitation Number"),
    ("solicitation_type", "Solicitation Type"),
    ("title", "Title"),
    ("publication_date", "Publication Date"),
    ("question_acceptance_deadline", "Question Acceptance Deadline"),
    ("closing_date", "Closing Date"),
    ("detail_url", "Detail URL"),
    ("documents_count", "Documents"),
    ("status", "Status"),
]
