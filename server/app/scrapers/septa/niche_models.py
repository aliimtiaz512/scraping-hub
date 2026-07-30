"""Database models for the SEPTA niche catalog.

A *niche* is a business area (e.g. "AI / ML") that owns a set of search terms:
free-text **keywords** and SEPTA **commodity codes**. A run selects one niche and
the scraper searches every one of its terms separately, merging the results.

The catalog is seeded from `app/scrapers/septa/niches.py` at startup — the file
is the source of truth and these tables are its materialised form, which is what
lets the API serve the dropdown and the scraper resolve a niche's terms with a
plain query. See `niches.seed_niches`.

Kept in its own module (rather than in `models.py`) so the run/bid storage that
the scraper already depends on stays untouched.
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


class SeptaNiche(Base):
    """One business area, owning the keywords and commodity codes below."""

    __tablename__ = "septa_niches"

    # The stable catalog key (e.g. "ai_ml") — what the API and runs refer to.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(255))
    # Filename-safe form of the label, for run folders and sheet names.
    slug: Mapped[str | None] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # A niche dropped from the catalog file is deactivated, never deleted —
    # historical bids still name it.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    keywords: Mapped[list["SeptaNicheKeyword"]] = relationship(
        back_populates="niche", cascade="all, delete-orphan"
    )
    codes: Mapped[list["SeptaNicheCode"]] = relationship(
        back_populates="niche", cascade="all, delete-orphan"
    )


class SeptaNicheKeyword(Base):
    """One free-text keyword searched on its own against Open Quotes."""

    __tablename__ = "septa_niche_keywords"
    __table_args__ = (
        UniqueConstraint("niche_key", "term", name="uq_septa_niche_keyword"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    niche_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("septa_niches.key", ondelete="CASCADE"), index=True
    )
    term: Mapped[str] = mapped_column(String(255))
    # Optional grouping (e.g. "core" / "extended") carried through from the
    # catalog file. Purely informational — every tier is searched.
    tier: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    niche: Mapped["SeptaNiche"] = relationship(back_populates="keywords")


class SeptaNicheCode(Base):
    """One SEPTA commodity code searched on its own against Open Quotes."""

    __tablename__ = "septa_niche_codes"
    __table_args__ = (
        UniqueConstraint("niche_key", "code", name="uq_septa_niche_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    niche_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("septa_niches.key", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    niche: Mapped["SeptaNiche"] = relationship(back_populates="codes")
