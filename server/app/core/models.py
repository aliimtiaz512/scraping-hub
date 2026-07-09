"""Persisted live-run state, shared by every scraper's run_manager.

Each scraper's own tables (e.g. MyFlorida's `scrape_runs`) record a normalized
run *after* it finishes and its export is ingested. This table instead mirrors
the live `run_manager` dict while a run is in flight, so that in-progress run
status survives a server restart — otherwise the frontend keeps polling a
run_id that only ever existed in memory and gets a permanent 404.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


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
