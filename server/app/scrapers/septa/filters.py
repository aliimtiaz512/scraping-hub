"""The Open Date Range filter — the only search filter a SEPTA run still has.

Entirely optional, and that is the point:

* **dates given** — fill the Open Date Range boxes, then Search;
* **no dates given** — type nothing at all, just Search, which returns every
  open quote the portal is showing.

There is deliberately no "default to today". The previous scraper substituted
today's date whenever a run had no other filter, which silently narrowed an
unfiltered run to a single day's quotes — the opposite of "fetch all available
open quote results".

The range is modelled as start + optional end so it fits the portal's form
either way: the search page carries opens *and* closes date ranges (see
`settings.septa_search_url`), of which this covers the opens pair. A request
that sets only `start` behaves exactly like the old single "opens on" date.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# What the UI and API speak (ISO), and what the ASP.NET form expects.
INPUT_FORMAT = "%Y-%m-%d"
PORTAL_FORMAT = "%m/%d/%Y"


class BadDate(ValueError):
    """A date that could not be parsed. Carries the offending value."""

    def __init__(self, field: str, value: str):
        self.field = field
        self.value = value
        super().__init__(f"{field} date {value!r} is not in YYYY-MM-DD form")


class OpenDateRange(BaseModel):
    """The run's Open Date Range, both ends optional.

    Nothing here is required — an empty instance is the normal case and means
    "no date filtering at all".
    """

    start: str | None = Field(default=None, description="Opens-from date, YYYY-MM-DD")
    end: str | None = Field(default=None, description="Opens-to date, YYYY-MM-DD")

    @property
    def is_empty(self) -> bool:
        """True when the run should bypass the date inputs entirely."""
        return not (self.start or "").strip() and not (self.end or "").strip()

    def portal_values(self) -> tuple[str | None, str | None]:
        """(start, end) in the portal's MM/DD/YYYY form; None where unset.

        Raises `BadDate` for a value that is present but unparseable, so the
        caller can warn and carry on unfiltered rather than guess at a date the
        user did not mean.
        """
        return self._convert("start", self.start), self._convert("end", self.end)

    @staticmethod
    def _convert(field: str, value: str | None) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, INPUT_FORMAT).strftime(PORTAL_FORMAT)
        except ValueError as exc:
            raise BadDate(field, text) from exc

    def summary(self) -> str:
        """One-line description of the range, for the run label and the Excel name."""
        start = (self.start or "").strip()
        end = (self.end or "").strip()
        if start and end:
            return f"opens {start} to {end}"
        if start:
            return f"opens from {start}"
        if end:
            return f"opens until {end}"
        return "all open quotes"
