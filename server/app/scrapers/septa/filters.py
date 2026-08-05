"""The Open Date Range "from" box — the only search filter a SEPTA run has.

Entirely optional, and that is the point:

* **a date given** — fill the "Opens from" box, then Search;
* **no date** — type nothing at all, just Search, which returns every open
  quote.

There is deliberately no "default to today". The previous scraper substituted
today's date whenever a run had no other filter, which silently narrowed an
unfiltered run to a single day's quotes — the opposite of "fetch all available
open quote results".

Only the *from* side is used. The portal's form also carries an "opens to" box
(and a closes pair), but the run does not fill them: an open-ended lower bound
is what "everything from this date onward" means, and adding an upper bound
only ever hides quotes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# What the UI and API speak (ISO), and what the ASP.NET form expects.
INPUT_FORMAT = "%Y-%m-%d"
PORTAL_FORMAT = "%m/%d/%Y"


class BadDate(ValueError):
    """A date that could not be parsed. Carries the offending value."""

    def __init__(self, value: str):
        self.value = value
        super().__init__(f"opens-from date {value!r} is not in YYYY-MM-DD form")


class OpenDateFilter(BaseModel):
    """The run's optional "Opens from" date.

    Nothing here is required — an empty instance is the normal case and means
    "no date filtering at all".
    """

    opens_from: str | None = Field(default=None, description="Opens-from date, YYYY-MM-DD")

    @property
    def is_empty(self) -> bool:
        """True when the run should bypass the date input entirely."""
        return not (self.opens_from or "").strip()

    def portal_value(self) -> str | None:
        """The date in the portal's MM/DD/YYYY form, or None when unset.

        Raises `BadDate` for a value that is present but unparseable, so the
        caller can warn and carry on unfiltered rather than guess at a date the
        user did not mean.
        """
        text = (self.opens_from or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, INPUT_FORMAT).strftime(PORTAL_FORMAT)
        except ValueError as exc:
            raise BadDate(text) from exc

    def summary(self) -> str:
        """One-line description, for the run label and the Excel name."""
        text = (self.opens_from or "").strip()
        return f"opens from {text}" if text else "all open quotes"
