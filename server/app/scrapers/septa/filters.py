"""What a SEPTA run searches: which module, and the optional "Opens from" date.

**The module is chosen per run, and exactly one runs.** The portal has two:
Open Quotes (a parts-requisition feed) and the Bid module's Open Bids (the
actual solicitations). A run navigates to the selected one and searches it —
there is no "both", so a run's output is never a blend of two grids the reader
has to tell apart.

The date is entirely optional, and that is the point. It applies to whichever
module was selected:

* **a date given** — fill the "Opens from" box, then Search;
* **no date** — type nothing at all, just Search, which returns every open
  row in that module.

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

# The two modules a run can search, as the API and UI name them.
QUOTES = "quotes"
OPEN_BIDS = "open_bids"
MODULES: tuple[str, ...] = (QUOTES, OPEN_BIDS)

# Quotes is the default so a caller that omits the field — an existing script,
# an older client — gets exactly the run it used to get.
DEFAULT_MODULE = QUOTES

# How each module is written in logs, run labels and the Excel name.
MODULE_LABELS: dict[str, str] = {
    QUOTES: "open quotes",
    OPEN_BIDS: "open bids",
}

# Accepted spellings, so the API is forgiving about a hyphen or a space without
# the scraper ever having to guess at something genuinely unrecognised.
_ALIASES: dict[str, str] = {
    "quote": QUOTES,
    "quotes": QUOTES,
    "open quotes": QUOTES,
    "open_quotes": QUOTES,
    "open-quotes": QUOTES,
    "bid": OPEN_BIDS,
    "bids": OPEN_BIDS,
    "open bids": OPEN_BIDS,
    "open_bids": OPEN_BIDS,
    "open-bids": OPEN_BIDS,
}


class BadDate(ValueError):
    """A date that could not be parsed. Carries the offending value."""

    def __init__(self, value: str):
        self.value = value
        super().__init__(f"opens-from date {value!r} is not in YYYY-MM-DD form")


class BadModule(ValueError):
    """A module name that is neither of the portal's two. Carries the value."""

    def __init__(self, value: str):
        self.value = value
        super().__init__(
            f"module {value!r} is not one of {', '.join(MODULES)}"
        )


def normalize_module(value: str | None) -> str:
    """The canonical module name for `value`, defaulting when it is blank.

    Raises `BadModule` for anything unrecognised rather than falling back to the
    default: silently searching Open Quotes because "opne_bids" was misspelled
    would hand back a full, plausible sheet of the wrong module's rows.
    """
    text = (value or "").strip().lower()
    if not text:
        return DEFAULT_MODULE
    try:
        return _ALIASES[text]
    except KeyError:
        raise BadModule(value or "") from None


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

    def summary(self, module: str = DEFAULT_MODULE) -> str:
        """One-line description of the run, for its label and the Excel name.

        Names the module as well as the date, because with the module now
        selectable the date alone no longer identifies what was searched — two
        runs on the same date against different modules would otherwise be
        labelled identically and their sheets named the same.
        """
        what = MODULE_LABELS.get(module, MODULE_LABELS[DEFAULT_MODULE])
        text = (self.opens_from or "").strip()
        return f"{what} opening from {text}" if text else f"all {what}"
