"""Sweep persistence — separate tables from the niche flow's `mfmp_bids`.

Two tables rather than one, because criteria doc §5.2 wants a threshold change
replayable over history without re-fetching: the per-niche C/T/S breakdown for
all six niches is 18 numbers per advertisement, which belongs in rows, not in
columns on the bid.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SweepBid(Base):
    """One advertisement, with the lane it was routed into."""

    __tablename__ = "mfmp_sweep_bids"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # -- portal fields --------------------------------------------------------
    ad_number: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    agency: Mapped[str | None] = mapped_column(Text)
    ad_type: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(60))
    ad_date: Mapped[str | None] = mapped_column(String(60))
    open_date: Mapped[str | None] = mapped_column(String(60))
    close_date: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(Text)

    # -- classification (criteria doc §7) -------------------------------------
    primary_niche: Mapped[str] = mapped_column(String(16), index=True)
    primary_score: Mapped[int] = mapped_column(Integer, default=0)
    match_strength: Mapped[str | None] = mapped_column(String(16))
    secondary_niches: Mapped[dict | None] = mapped_column(JSONB)
    other_reason: Mapped[str | None] = mapped_column(String(40))
    closest_niche: Mapped[str | None] = mapped_column(String(16))
    closest_niche_score: Mapped[int | None] = mapped_column(Integer)
    flags: Mapped[dict | None] = mapped_column(JSONB)

    # -- explainability (§9.5) ------------------------------------------------
    matched_codes: Mapped[dict | None] = mapped_column(JSONB)
    code_source: Mapped[str | None] = mapped_column(String(20))
    matched_keywords: Mapped[dict | None] = mapped_column(JSONB)
    suppressed_terms: Mapped[dict | None] = mapped_column(JSONB)
    deliverables_detected: Mapped[dict | None] = mapped_column(JSONB)

    # -- provenance -----------------------------------------------------------
    documents: Mapped[dict | None] = mapped_column(JSONB)
    document_chars: Mapped[int] = mapped_column(Integer, default=0)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_mfmp_sweep_bids_run_niche", "run_id", "primary_niche"),
    )


class SweepScore(Base):
    """One (advertisement, niche) score — six rows per advertisement, always.

    Stored for every niche including the losers, because §5.2's tuning signal is
    "a cluster of 35s all pointing at one niche means a lexicon gap", which only
    exists if the near-misses were kept.
    """

    __tablename__ = "mfmp_sweep_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bid_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mfmp_sweep_bids.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    ad_number: Mapped[str] = mapped_column(String(120), index=True)

    niche: Mapped[str] = mapped_column(String(16), index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    code_points: Mapped[int] = mapped_column(Integer, default=0)
    title_points: Mapped[int] = mapped_column(Integer, default=0)
    scope_points: Mapped[int] = mapped_column(Integer, default=0)
    matched_keywords: Mapped[dict | None] = mapped_column(JSONB)
    suppressed_terms: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_mfmp_sweep_scores_run_niche", "run_id", "niche"),
    )


# Workbook column order, shared by the writer and the DB rebuild so the two can
# never drift. (attribute-or-key, header).
SHEET_COLUMNS: list[tuple[str, str]] = [
    ("ad_number", "Ad Number"),
    ("title", "Title"),
    ("agency", "Agency"),
    ("ad_type", "Ad Type"),
    ("status", "Status"),
    ("ad_date", "Ad Date"),
    ("open_date", "Open Date"),
    ("close_date", "Close Date"),
    ("role", "Role"),
    ("match_strength", "Match Strength"),
    ("score", "Score"),
    ("code_points", "C"),
    ("title_points", "T"),
    ("scope_points", "S"),
    ("n1", "N1"),
    ("n2", "N2"),
    ("n3", "N3"),
    ("n4", "N4"),
    ("n5", "N5"),
    ("n6", "N6"),
    ("primary_niche", "Primary Niche"),
    ("secondary_niches", "Secondary Niches"),
    ("matched_codes", "Matched Codes"),
    ("code_source", "Code Source"),
    ("matched_keywords", "Matched Keywords"),
    ("deliverables_detected", "Deliverables Detected"),
    ("suppressed_terms", "Suppressed Terms"),
    ("flags", "Flags"),
    ("description", "Description"),
    ("documents", "Documents"),
    ("document_chars", "Document Text"),
]

# The Other sheet explains itself (§5.2) with three extra columns.
OTHER_EXTRA_COLUMNS: list[tuple[str, str]] = [
    ("other_reason", "Other Reason"),
    ("closest_niche", "Closest Niche"),
    ("closest_niche_score", "Closest Niche Score"),
]
