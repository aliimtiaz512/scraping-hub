"""NAICS reference codes — a searchable table of 6-digit NAICS codes and titles.

Unlike the bid portals this is reference data, not per-run bids: the refresh
scrape (vendored server/scrappers/naics/) repopulates this one global table,
upserting by `code`.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class NaicsCode(Base):
    __tablename__ = "naics_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
