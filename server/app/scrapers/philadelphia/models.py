"""Database models for the City of Philadelphia (PHLContracts) scraper.

PHLContracts runs on Periscope Holdings' BSO platform — the same `/bso/` servlet
application behind a number of municipal portals — so the markup is 2000s-era
table layout rather than a component framework: no data attributes, no grid ids,
and the bid list is `table#resultsTable` with `td.tableText-01` cells.

Two tables, and they answer different questions:

* `philadelphia_runs` — one row per scrape, for the run history.
* `city_of_philadelphia_bids` — one row per **bid**, keyed on the bid number
  itself rather than on (run, bid). That is deliberate and is what makes a
  second run an *update* rather than a second copy: the portal's Open Bids list
  is a live set, the same bid appears in it every day until it closes, and what
  a reader wants is its current state, not one row per time it was seen.
  `first_seen_at` keeps when it first appeared; `scraped_at` moves.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PhiladelphiaRun(Base):
    """One row per PHLContracts scrape run."""

    __tablename__ = "philadelphia_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search: Mapped[str | None] = mapped_column(Text)
    bids_found: Mapped[int] = mapped_column(Integer, default=0)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    folder: Mapped[str | None] = mapped_column(Text)
    excel_path: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    bids: Mapped[list["PhiladelphiaBid"]] = relationship(back_populates="run")


class PhiladelphiaBid(Base):
    """One open bid from PHLContracts, keyed on its bid number.

    The columns are the Open Bids table's own, plus what only the detail page
    knows: everything under "Header Information" (`extra_header_data`) and the
    files that came with it (`file_paths`).

    `extra_header_data` is JSONB and deliberately unmodelled. The header table
    is a run of label/value cell pairs the portal changes per bid type — a
    micro-purchase carries Type Code and Informal Bid Flag, a formal one does
    not — so pinning those to columns would mean a migration every time
    Philadelphia adds a row. Everything found is kept under the label it was
    published with; the fields worth querying are promoted to columns above.
    """

    __tablename__ = "city_of_philadelphia_bids"

    # The portal's own bid number ("B2727750"), and the primary key: a bid is
    # one thing however many runs have seen it.
    bid_number: Mapped[str] = mapped_column(String(64), primary_key=True)

    # The run that last saw it. Nullable and ON DELETE SET NULL: clearing out an
    # old run's history must not delete bids that are still open.
    run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("philadelphia_runs.run_id", ondelete="SET NULL"), index=True
    )

    # -- the Open Bids table's columns ---------------------------------------
    organization: Mapped[str | None] = mapped_column(Text)
    alternate_id: Mapped[str | None] = mapped_column(Text)
    buyer: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    bid_opening_date: Mapped[str | None] = mapped_column(String(64))

    # -- the detail page ------------------------------------------------------
    detail_url: Mapped[str | None] = mapped_column(Text)
    #: Promoted out of the header table and into the summary sheet, because a
    #: reader sorts and filters on them. The header table's remaining labels stay
    #: in `extra_header_data` and reach the sheet as one readable column.
    fiscal_year: Mapped[str | None] = mapped_column(String(64))
    solicitation_type: Mapped[str | None] = mapped_column(Text)
    pre_bid_conference: Mapped[str | None] = mapped_column(Text)
    #: Every label/value pair under "Header Information", as published.
    extra_header_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    # -- the evaluation matrix ------------------------------------------------
    #: The shared Rule A/B/C funnel's verdict (see philadelphia/evaluation.py),
    #: the same engine SAM and Unison use. Stored rather than re-derived when a
    #: sheet is rebuilt: the kill-word list is editable, and re-running the
    #: matrix months later would silently rewrite a verdict someone acted on.
    decision: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    #: Which rule settled it — "B10", "C6", "none". String(16), as on SamBid.
    rule: Mapped[str | None] = mapped_column(String(16))
    #: HARDWARE | SERVICE, as the funnel classified it.
    requirement_type: Mapped[str | None] = mapped_column(String(20))

    #: The bid's line items, one dict per item, as read from the detail page.
    #: Kept so the item sheet can be rebuilt from the database rather than only
    #: from a workspace that is deleted when the run ends.
    items: Mapped[list] = mapped_column(JSONB, default=list)
    #: The attachments saved for this bid, as `Bids_Data/<bid>/<file>` paths
    #: *inside the archive* — a path the reader can follow in the unpacked ZIP.
    file_paths: Mapped[list] = mapped_column(JSONB, default=list)
    documents_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    #: Attachments the portal listed but that could not be saved, with why.
    document_errors: Mapped[list] = mapped_column(JSONB, default=list)

    # -- bookkeeping ----------------------------------------------------------
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    #: The complete scraped record, so a column that does not exist yet is not
    #: a value that was lost.
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    run: Mapped["PhiladelphiaRun"] = relationship(back_populates="bids")


# The master summary sheet's columns, in reading order: what it is, who wants
# it, when it closes, what kind of buy it is, then where its files are.
#
# The detail page's header table used to travel beside each bid as a JSON file,
# which is not a format a procurement team can open. It reaches the reader here
# instead: the three fields worth sorting and filtering on get columns of their
# own, and every other label the portal published lands in one readable
# "Additional Header Information" cell — so nothing is lost and nothing needs a
# migration the next time Philadelphia adds a row to that table.
#
# `Folder` stays: it is what maps a row in this sheet to a directory in the
# unpacked ZIP, and without it the sheet stops being an index of the archive.
# `Status` and `Flag Reason` are derived at render time from the description and
# title (see export._cell -> flags), not stored: the answer is a pure function of
# columns the row already carries, so deriving it means a sheet rebuilt from the
# database months later marks exactly the same rows as the one that shipped in
# the ZIP — and the excluded-niche list can be corrected without a migration or a
# re-scrape.
EXCEL_COLUMNS: list[tuple[str, str]] = [
    # 1. What the bid is. A reader identifies the row before anything judges it,
    #    so the city's own facts lead and the verdict follows them — the sheet
    #    reads the way someone reads a bid, not the way the pipeline computes it.
    ("bid_number", "Bid #"),
    ("description", "Description"),
    ("organization", "Organization"),
    ("buyer", "Buyer"),
    ("bid_opening_date", "Bid Opening Date"),
    ("fiscal_year", "Fiscal Year"),
    ("solicitation_type", "Procurement / Solicitation Type"),
    ("pre_bid_conference", "Pre-Bid Conference Date / Details"),
    ("alternate_id", "Alternate Id"),

    # 2. What this run makes of it. `Niche Flag` rather than `Status`: two
    #    columns both called Status, one from the matrix and one from the niche
    #    list, is the kind of thing a reader has to stop and decode.
    ("decision", "Evaluation Status"),
    ("requirement_type", "Requirement Type"),
    ("rule", "Matrix Rule"),
    ("reason", "Evaluation Reason"),
    ("flag_status", "Niche Flag"),
    ("flag_reason", "Niche Flag Reason"),

    # 3. Where its files are, and how to get back to the portal.
    ("documents_downloaded", "Total Document Count"),
    ("item_count", "Line Items"),
    ("folder", "Folder"),
    ("additional_header", "Additional Header Information"),
    ("detail_url", "Detail URL"),
]
