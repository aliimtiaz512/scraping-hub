"""Shared close-date filter used by every portal.

The rule, applied uniformly across portals: keep only bids whose closing date is
still at least MIN_DAYS_UNTIL_CLOSE days away, so there is enough runway to
prepare and submit. A bid we can confirm closes sooner is dropped; a bid whose
close date we cannot read is KEPT (we can't prove it fails the rule) and counted,
so a drop is never silent.

Every portal names and formats its closing date differently (Close Date, Closing
Date, Due Date/Time, Date Offers Due, End Date…), so `parse_close_date` is
deliberately permissive: it tries a list of explicit formats first, then falls
back to a robust dateutil parse — but only when the text actually contains a
4-digit year, so a stray number never parses into a bogus "past" date that would
wrongly drop a bid.
"""

from __future__ import annotations

import logging
import re
import warnings
from datetime import date, datetime
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# Keep only bids closing at least this many days out. One definition, every portal.
MIN_DAYS_UNTIL_CLOSE = 7

# Explicit formats tried first (fast, unambiguous), against the whole string and
# its first whitespace-delimited token (so a trailing time/label doesn't defeat
# an otherwise-valid date). dateutil covers anything past this list.
_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%b %d, %Y",
    "%B %d, %Y",
)

_YEAR_RE = re.compile(r"\b\d{4}\b")


def parse_close_date(raw: Any) -> date | None:
    """Parse a portal's close-date value into a calendar date, or None if it
    can't be read. None is the signal to KEEP a bid (we can't prove it fails)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    candidates = [text]
    first_token = text.split()[0]
    if first_token != text:
        candidates.append(first_token)
    for candidate in candidates:
        for fmt in _FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue

    # Robust fallback — but only when a real 4-digit year is present, so a bare
    # number or partial string never resolves to a bogus (usually past) date.
    if _YEAR_RE.search(text):
        try:
            from dateutil import parser as _du

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # ignore unknown-tzname (e.g. "EST")
                return _du.parse(text, fuzzy=True).date()
        except (ValueError, OverflowError, TypeError):
            pass
    return None


def days_until_close(raw: Any, today: date | None = None) -> int | None:
    """Whole days from today until the close date, or None if unreadable."""
    parsed = parse_close_date(raw)
    if parsed is None:
        return None
    return (parsed - (today or date.today())).days


def keeps(raw: Any, today: date | None = None) -> bool:
    """True if this bid should be kept: an unreadable date (can't disprove) or a
    date at least MIN_DAYS_UNTIL_CLOSE days out."""
    days = days_until_close(raw, today)
    return days is None or days >= MIN_DAYS_UNTIL_CLOSE


def filter_records(
    records: Iterable[dict[str, Any]],
    get_close: Callable[[dict[str, Any]], Any],
    today: date | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Split records by the close-date rule.

    `get_close(record)` returns that record's raw close-date value. Returns
    (kept, skipped_closing_soon, kept_unreadable): the surviving records, how many
    were dropped for closing too soon, and how many were kept despite an
    unreadable close date.
    """
    kept: list[dict[str, Any]] = []
    skipped = 0
    unreadable = 0
    for rec in records:
        days = days_until_close(get_close(rec), today)
        if days is None:
            unreadable += 1
            kept.append(rec)
        elif days >= MIN_DAYS_UNTIL_CLOSE:
            kept.append(rec)
        else:
            skipped += 1
    return kept, skipped, unreadable
