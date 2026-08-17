"""Every filter a Unison run can apply, and whether it is on.

All three are **off for the testing phase**: a run signs in and takes whatever
the seller dashboard shows by default, with nothing dropped on the way out.
They are gathered here rather than deleted so switching one back on is a boolean
in this file, not an archaeology exercise across the runner, the engine and the
frontend.

    APPLY_PORTAL_FILTER    the dashboard's own dropdown (its "Posted Today"
                           default is a date window in disguise — it hides
                           everything posted before today)
    EXCLUDE_KEYWORDS       drop a request whose description matches a term
    APPLY_CLOSE_DATE_FILTER the hub-wide rule that keeps only bids closing at
                           least MIN_DAYS_UNTIL_CLOSE (7) days out

Re-enabling one is the flag plus nothing else: the values each filter needs when
it is on are kept beside it, and the call sites already route through here.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, filter_records

# -- the portal's own dropdown ----------------------------------------------
#
# No longer a switch here: the Filter By criterion is chosen per run in the
# console and travels on the run. The default is the portal's own "Select
# Criteria" — no filter at all — so an unconfigured run still reads the whole
# listing, as it has since the filters were turned off for testing.

#: The portal's `<select id="allOppFilterId">` options, by value. The value is
#: what the engine selects on; the label is what the console shows.
PORTAL_FILTERS: list[tuple[str, str]] = [
    ("-1", "Select Criteria"),
    ("1", "Posted Today"),
    ("2", "Posted Last 3 Days"),
    ("3", "Posted Last 7 Days"),
    ("4", "Closing Today"),
    ("5", "Closing Next 3 Days"),
    ("6", "Closing Next 7 Days"),
]
#: "Select Criteria" — the whole listing, unfiltered.
DEFAULT_FILTER_ID = "-1"
#: Results per page. The portal defaults to 25; 100 is its largest option, so a
#: run makes a quarter of the page loads for the same buys.
PAGE_SIZE = "100"


def filter_label(filter_id: str | None) -> str:
    """The display label for an option value, or the value itself if unknown."""
    wanted = str(filter_id or DEFAULT_FILTER_ID)
    return next((label for value, label in PORTAL_FILTERS if value == wanted), wanted)


def is_valid_filter(filter_id: str | None) -> bool:
    return str(filter_id or DEFAULT_FILTER_ID) in {value for value, _ in PORTAL_FILTERS}


def catalog() -> list[dict[str, str]]:
    """The options, for the console's Filter By dropdown."""
    return [{"value": value, "label": label} for value, label in PORTAL_FILTERS]

# -- description keywords ----------------------------------------------------

#: Drop requests whose description contains one of EXCLUDED_KEYWORDS.
EXCLUDE_KEYWORDS = False
#: The engine's original exclusion list, kept for when it goes back on.
#:
#: This is *not* what takes GSA buys off the table — `evaluation.screen_listing`
#: is, and it keeps them in the report marked REJECT with a reason. This list
#: makes the engine drop a row before the hub ever sees it, so a buy excluded
#: here leaves no trace of having existed. Two things to know before switching
#: it on: the GSA term duplicates a screen that already works and matches far
#: more forms of it, and these are plain substrings — "gsa schedules" here would
#: miss "GSA-Schedule", exactly as the screen did before it was made a pattern.
EXCLUDED_KEYWORDS: list[str] = [
    "gsa schedules", "food rfi", "market research", "foods", "meal", "survey",
]

# -- the 7-day close-date rule -----------------------------------------------

#: Apply the shared close-date rule (app/core/closing_filter) to the results.
APPLY_CLOSE_DATE_FILTER = False


def excluded_keywords() -> list[str]:
    """The description terms to drop on, empty when the filter is off."""
    return list(EXCLUDED_KEYWORDS) if EXCLUDE_KEYWORDS else []


def apply_close_date_filter(
    records: list[dict[str, Any]],
    get_close: Callable[[dict[str, Any]], Any],
) -> tuple[list[dict[str, Any]], int, int, bool]:
    """Split records by the close-date rule, or pass them through untouched.

    Returns `(kept, skipped_closing_soon, kept_unreadable, applied)` — the same
    triple `filter_records` gives, plus whether the rule actually ran. Callers
    report the tallies only when it did: a run that filtered nothing must not
    claim a filter it did not apply.
    """
    if not APPLY_CLOSE_DATE_FILTER:
        return list(records), 0, 0, False
    kept, skipped, unreadable = filter_records(records, get_close)
    return kept, skipped, unreadable, True


def summary(filter_id: str | None = None) -> str:
    """One line naming what narrowed this run, for the run log."""
    portal = str(filter_id or DEFAULT_FILTER_ID) != DEFAULT_FILTER_ID
    active = [
        name for name, on in (
            (f"portal filter ({filter_label(filter_id)})", portal),
            ("keyword exclusions", EXCLUDE_KEYWORDS),
            (f"close date ≥{MIN_DAYS_UNTIL_CLOSE}d", APPLY_CLOSE_DATE_FILTER),
        ) if on
    ]
    return ", ".join(active) if active else "none (unfiltered)"


def describe(filter_id: str | None = None) -> dict[str, bool]:
    """Which filters are on, for the run state and the console."""
    return {
        "portal_filter": str(filter_id or DEFAULT_FILTER_ID) != DEFAULT_FILTER_ID,
        "keyword_exclusions": EXCLUDE_KEYWORDS,
        "close_date": APPLY_CLOSE_DATE_FILTER,
    }
