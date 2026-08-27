"""Persisted live-run state, shared by every scraper's run_manager.

Each scraper's own tables (e.g. MyFlorida's `scrape_runs`) record a normalized
run *after* it finishes and its export is ingested. This table instead mirrors
the live `run_manager` dict while a run is in flight, so that in-progress run
status survives a server restart — otherwise the frontend keeps polling a
run_id that only ever existed in memory and gets a permanent 404.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Checkpoint(Base):
    """Where a run had got to, durable enough to survive the run itself.

    Distinct from `RunState` above, which mirrors the whole live run dict for the
    console's benefit. This is the far smaller thing a *resume* needs: how many
    records are done, which ones, and where in the portal's pagination the walk
    had reached.

    It is separate for two reasons. It is written on a different rhythm — every
    few records, rather than on every status poke — and it must stay legible when
    the run dict around it does not: a run killed by a network drop has a
    `RunState` full of half-truths (status "running", no finished_at) and a
    checkpoint that is still exactly right.

    `processed` holds the identifiers already extracted — a detail URL, a
    solicitation id, an ad number, whatever that portal identifies a record by.
    Resume skips them by membership rather than by counting, because a count
    only helps if the portal returns the same rows in the same order on the way
    back, and none of them promise that.
    """

    __tablename__ = "run_checkpoints"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scraper: Mapped[str | None] = mapped_column(String(32), index=True)
    # How many records have been fully extracted and are safe to skip.
    records_done: Mapped[int] = mapped_column(Integer, default=0)
    # How many the run expects in total, when it knows. Null while unknown.
    records_total: Mapped[int | None] = mapped_column(Integer)
    # The portal's own position: page number, offset, keyword index — whatever
    # that scraper needs to get back to where it was. Shape is per-portal.
    position: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Identifiers already done, so a resume skips them rather than re-extracting.
    processed: Mapped[list[str]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RunState(Base):
    __tablename__ = "run_state"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scraper: Mapped[str | None] = mapped_column(String(32), index=True)
    # ISO-8601 string, mirroring the in-memory run dict; sorts lexicographically.
    started_at: Mapped[str | None] = mapped_column(String(32))
    # The complete run_manager dict (status, step, counts, errors, bids, ...).
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
