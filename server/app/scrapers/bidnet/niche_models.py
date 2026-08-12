"""Database models for the BidNet Direct niche catalog.

A *niche* is a business sector (e.g. "Graphic Design & Visual Communication")
that owns a set of search keywords. A run selects exactly one niche, and the
scraper searches every keyword it owns **separately, one at a time** — never
combined into a single boolean query, which narrows the result set and loses
bids.

The catalog is seeded from `app/scrapers/bidnet/niches.py` at startup: that file
is the source of truth and these tables are its materialised form, which is what
lets the API serve the dropdown and the scraper resolve a niche's keywords with
a plain query. See `niches.seed_niches`.

Keywords live here rather than in the API payload on purpose — the frontend only
ever sees a niche key, a label and a count, never the terms themselves.

Kept in its own module (rather than in `models.py`) so the run/bid storage the
scraper already depends on stays untouched. Mirrors the SEPTA catalog, which
solves the same problem.
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BidnetNiche(Base):
    """One business sector, owning the search keywords below."""

    __tablename__ = "bidnet_niches"

    # The stable catalog key (e.g. "graphic_design") — what the API and runs
    # refer to, and the only part of the catalog the frontend ever sends back.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(255))
    # Filename-safe form of the label, used for the run folder and sheet names.
    slug: Mapped[str | None] = mapped_column(String(128))
    # Where the sector's codes/standards are recorded — reference only, never
    # searched (BidNet's own NIGP filter uses different internal ids).
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # A niche dropped from the catalog file is deactivated, never deleted —
    # historical bids still name it.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    keywords: Mapped[list["BidnetNicheKeyword"]] = relationship(
        back_populates="niche", cascade="all, delete-orphan"
    )


class BidnetNicheKeyword(Base):
    """One search term. Each is typed into BidNet's search box on its own.

    Stored bare: quoting a phrase makes no difference to what BidNet returns
    (verified against the live portal — see the `niches` module docstring).

    Two kinds live here, in one table because they are the same thing to the
    portal — text typed into the same box: the sector's `keyword`s and its
    `nigp` class-item / UNSPSC codes. `sort_order` keeps every keyword ahead of
    every code, which is the order a run searches them in.

    Terms are unique *within* a niche only: a run searches one niche, so the same
    term legitimately belongs to two sectors (e.g. "WCAG" under both Graphic
    Design and Software).
    """

    __tablename__ = "bidnet_niche_keywords"
    __table_args__ = (UniqueConstraint("niche_key", "term", name="uq_bidnet_niche_keyword"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    niche_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("bidnet_niches.key", ondelete="CASCADE"), index=True
    )
    term: Mapped[str] = mapped_column(String(255))
    # "keyword" | "nigp" — see niches.KIND_KEYWORD / KIND_NIGP. Defaulted rather
    # than required so a row inserted by hand is a keyword unless it says
    # otherwise, which is what every row predating the codes was.
    kind: Mapped[str] = mapped_column(String(16), default="keyword", server_default="keyword")
    notes: Mapped[str | None] = mapped_column(Text)
    # Search order within the niche — the run works through them in this order.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    niche: Mapped["BidnetNiche"] = relationship(back_populates="keywords")
