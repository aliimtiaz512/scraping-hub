"""The posting-date window a MyFlorida run searches within.

One definition shared by all three execution modes — keyword, commodity code and
the ad-status sweep — because they are three ways of driving the *same* search
form, and a window that meant something different depending on which button
started the run would be a bug nobody could see from the output.

Two formats meet here and the boundary between them is the point of this module:

* **ISO (`yyyy-mm-dd`) on the wire.** It is what `<input type="date">` produces,
  it sorts, and it is unambiguous — `03/04/2026` is the fourth of March to half
  the world and the third of April to the other half.
* **`mm/dd/yyyy` at the portal.** It is what MyFlorida's own fields take.

Converting at the edge rather than in the browser keeps the ambiguous format
inside the one process that knows which portal it is talking to. A caller may
send either (a `curl` user reaching for the portal's format should not be
tripped up), but what is typed into MyFlorida's Posting Start Date / End Date
fields is always `portal_start` / `portal_end`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# The formats `parse` accepts from a caller, in the order they are tried.
_ACCEPTED = ("%Y-%m-%d", "%m/%d/%Y")

# What MyFlorida's own date inputs take.
PORTAL_FORMAT = "%m/%d/%Y"

# ---------------------------------------------------------------------------
# Whether the window is typed into the portal's own date fields.
# ---------------------------------------------------------------------------
#
# True: every mode fills MyFlorida's Posting Start Date / End Date inputs before
# submitting the search (see `MFMPScraper.apply_date_range`).
#
# It survives as a switch rather than being deleted because of how this
# particular failure would look if the injection ever had to be turned off.
# MyFlorida renders no "no results" message, so a search that was never narrowed
# is DOM-identical to one that was — a user who set a window and got back
# everything would have no way to tell, and the run record would agree with
# them. So with this False, nothing is typed and every run that requests a
# window says loudly that it did not get one (see
# `MFMPScraper.report_date_window`). That is also the state a run lands in when
# the fields are on the page but will not take the value, which is the case this
# was really written for.
PORTAL_DATE_FILTER_READY = True


class DateRangeError(ValueError):
    """A window the portal could not be asked for. Carries a message meant for
    the person who typed it, not for a log."""


@dataclass(frozen=True)
class PostingDateRange:
    """A posting-date window. Either end may be open."""

    start: date | None = None
    end: date | None = None

    @property
    def is_set(self) -> bool:
        return self.start is not None or self.end is not None

    @property
    def portal_start(self) -> str:
        return self.start.strftime(PORTAL_FORMAT) if self.start else ""

    @property
    def portal_end(self) -> str:
        return self.end.strftime(PORTAL_FORMAT) if self.end else ""

    def isoformat(self) -> tuple[str | None, str | None]:
        """The window as it goes back to the console and onto the run record."""
        return (
            self.start.isoformat() if self.start else None,
            self.end.isoformat() if self.end else None,
        )

    def describe(self) -> str:
        """The window in words, for run summaries and log lines.

        Spelled out rather than reduced to a pair of dates because this string
        is what sits next to an empty result set, and "posted on or after
        2026-08-01" explains an empty export in a way that "2026-08-01" does
        not.
        """
        if self.start and self.end:
            return f"posted {self.start.isoformat()} to {self.end.isoformat()}"
        if self.start:
            return f"posted on or after {self.start.isoformat()}"
        if self.end:
            return f"posted on or before {self.end.isoformat()}"
        return "any posting date"


def same_portal_date(shown: str | None, expected: str | None) -> bool:
    """Whether a date field's on-screen value is the date we typed into it.

    Read back rather than trusted, because a filter that did not take is
    invisible on this portal. Compared as dates and not as strings: Angular
    Material re-renders what it parsed, so a field typed `08/01/2026` can come
    back `8/1/2026` — the same day, and not a mismatch worth failing a run over.
    Two empty values match, which is how a field we meant to clear is confirmed
    clear.
    """
    left, right = (shown or "").strip(), (expected or "").strip()
    if not left or not right:
        return not left and not right
    try:
        return datetime.strptime(left, PORTAL_FORMAT) == datetime.strptime(right, PORTAL_FORMAT)
    except ValueError:
        return left == right


def _one(value: str | None, field: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _ACCEPTED:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise DateRangeError(
        f"{field} is not a date I can read: {text!r}. Use yyyy-mm-dd (e.g. 2026-08-01)."
    )


def parse(start: str | None, end: str | None) -> PostingDateRange:
    """Build a window from what the caller sent, or raise `DateRangeError`.

    Both ends are optional and either may stand alone — "everything posted since
    the first of the month" is a normal thing to ask for, and demanding a
    closing date for it would only invite someone to type today's and get a
    window that quietly stops being right tomorrow.

    An inverted range is rejected rather than silently swapped. The portal would
    return nothing for it, and on a search with no "no results" message an empty
    grid is exactly the outcome that gets mistaken for "there are no bids".
    """
    parsed = PostingDateRange(_one(start, "Start date"), _one(end, "End date"))
    if parsed.start and parsed.end and parsed.end < parsed.start:
        raise DateRangeError(
            f"End date ({parsed.end.isoformat()}) is before the start date "
            f"({parsed.start.isoformat()}) — no advertisement can fall in that window."
        )
    return parsed
