"""Reading one agency's "Open Public Opportunities" list.

The Bonfire portal index renders three tabs into one page — Open Public
Opportunities, Past Public Opportunities, My Opportunities — each a DataTable.
Everything here is scoped to `#openOpportunitiesTabPane` for a reason worth
spelling out: DataTables numbers its tables in *initialisation* order, so
`#DataTables_Table_0` is the open list only on agencies where nothing else
initialises first. On an agency where the account has been invited to something,
My Opportunities takes `_0` and the open list becomes `_1` — reading by that id
silently returns Closed/Awarded rows from the wrong tab (observed on two of the
four agencies in the live network). The pane id is stable; the table id is not.

The list is not identical across agencies either: only some publish a
Department column. Columns are therefore mapped by header text, and a missing
one simply stays empty.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)

PANE = "#openOpportunitiesTabPane"

SEL = {
    "tab_link": (By.CSS_SELECTOR, "#openOpportunitiesTab a"),
    # The tab itself, as opposed to the pane behind it. A portal that has no
    # public list has no such tab at all — which is a settled fact about that
    # portal, and the thing to test before waiting on a pane that cannot appear.
    "open_tab": (By.CSS_SELECTOR, "#openOpportunitiesTab"),
    # Every tab the portal offers. Read for its labels when the open one is
    # missing, so the run log says what the page *did* have.
    "tabs": (By.CSS_SELECTOR, "[id$='OpportunitiesTab'], .nav-tabs > li, [role='tab']"),
    "pane": (By.CSS_SELECTOR, PANE),
    # The pane holds two tables: a header clone (no id) inside
    # .dataTables_scrollHead, and the real one, which carries the id.
    "table": (By.CSS_SELECTOR, f"{PANE} table[id^='DataTables_Table_'], {PANE} table.dataTable"),
    "header_cells": (
        By.CSS_SELECTOR,
        f"{PANE} table[id^='DataTables_Table_'] thead th, {PANE} .dataTables_scrollBody thead th",
    ),
    "rows": (
        By.CSS_SELECTOR,
        f"{PANE} table[id^='DataTables_Table_'] tbody tr, {PANE} .dataTables_scrollBody tbody tr",
    ),
    "opportunity_link": (By.CSS_SELECTOR, "a[href*='/opportunities/']"),
    # Enough of the portal's tab chrome to say "this page is a Bonfire agency
    # portal", separately from whether the open list rendered on it. Without
    # that distinction, an org that publishes no portal at all and a portal
    # whose pane was slow look identical: a timeout on the pane selector.
    "portal_markers": (
        By.CSS_SELECTOR,
        "#openOpportunitiesTab, #openOpportunitiesTabPane, "
        "[id$='OpportunitiesTab'], [id$='OpportunitiesTabPane']",
    ),
    # DataTables' own "fetching" banner. Present in the DOM from the start and
    # toggled by display, so its *visibility* is the signal, not its presence.
    "processing": (By.CSS_SELECTOR, f"{PANE} .dataTables_processing, .dataTables_processing"),
    # The single full-width cell DataTables puts in an otherwise rowless table.
    # It carries "Loading…" while the AJAX call is in flight and the portal's
    # real empty-state text once it has settled — the same element in two very
    # different states, which is the trap this module exists to avoid.
    "empty_cell": (By.CSS_SELECTOR, f"{PANE} td.dataTables_empty, {PANE} tbody td[colspan]"),
    # Any frame inside the pane. The portals seen so far render the table into
    # the main document; this exists so that if one ever embeds it instead, the
    # run says so rather than timing out on a selector that cannot match.
    "frames": (By.CSS_SELECTOR, "iframe, frame"),
}

# Text that means "this table has finished loading and holds nothing". Matched
# only against the placeholder cell — the one full-width cell in a rowless table
# — never against a data row, so the wording can be broad without a project
# title tripping it.
#
# Deliberately generous about vocabulary. Bonfire, DataTables' own defaults and
# individual agencies each phrase this differently ("There are no open projects
# at this time.", "No active solicitations", "No matching records found",
# "0 records found"), and an unrecognised phrasing does not fail loudly — it
# reads as "still loading" until the deadline, which turns an empty portal into
# a minute of waiting and a misleading warning.
_SETTLED_EMPTY = re.compile(
    r"\bno\s+(?:\w+\s+){0,2}"
    r"(?:bids?|projects?|opportunities|solicitations?|records?|results?|data|entries)\b"
    r"|\b0\s+(?:bids?|projects?|opportunities|solicitations?|records?|results?|entries)\b"
    r"|\bnothing\s+(?:open|found|to\s+show|to\s+display)\b"
    r"|\bnone\s+(?:found|available|at\s+this\s+time)\b",
    re.IGNORECASE,
)
# Text that means "still working".
_STILL_LOADING = re.compile(r"loading|processing|please\s+wait", re.IGNORECASE)

# Header text (normalised to lowercase alphanumerics) -> bid field. "Action"
# holds the View Opportunity link and is handled separately.
COLUMN_KEYS: dict[str, str] = {
    "status": "status",
    "ref": "ref_number",
    "refnumber": "ref_number",
    "reference": "ref_number",
    "project": "project",
    "projectname": "project",
    "department": "department",
    "closedate": "close_date",
    "closingdate": "close_date",
    "daysleft": "days_left",
    "type": "opportunity_type",
    "opendate": "open_date",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(header: str) -> str:
    return _NON_ALNUM.sub("", header.lower())


def _text(element) -> str:
    """An element's text, read from the DOM rather than the rendering.

    `.text` returns "" for anything Selenium considers not displayed, and the
    portal's DataTables panes are frequently in that state (a tab mid-fade, a
    scroll body taller than the viewport) — which yields rows of blank cells
    that look like a portal change instead of a visibility quirk.
    """
    return re.sub(r"\s+", " ", (element.get_attribute("textContent") or "")).strip()


def read_headers(driver) -> list[str]:
    """The open list's column headers, in order.

    The header cells inside the scroll body are collapsed to zero height, so
    their text is read from the DOM (see `_text`).
    """
    return [_text(th) for th in driver.find_elements(*SEL["header_cells"])]


# Hydration states, in the order a page moves through them.
LOADING = "loading"  # the table is not there yet, or its AJAX call is in flight
READY   = "ready"    # at least one real opportunity row has rendered
EMPTY   = "empty"    # the table said so: it has settled and holds nothing
IDLE    = "idle"     # built, not fetching, and holding nothing — but not saying so


def _has_table(driver) -> bool:
    return bool(driver.find_elements(*SEL["table"]))


def _is_processing(driver) -> bool:
    """Is DataTables' "fetching" banner on screen?

    The element is always in the DOM, so presence proves nothing; only
    `is_displayed()` distinguishes a request in flight from an idle table.
    """
    for banner in driver.find_elements(*SEL["processing"]):
        try:
            if banner.is_displayed():
                return True
        except StaleElementReferenceException:
            continue
    return False


def hydration_state(driver) -> str:
    """How far along the open list's client-side render is.

    Four answers, because "no rows" has three different meanings and only one of
    them is an error. READY and EMPTY are conclusions the page has stated.
    LOADING means there is visibly still something happening. IDLE is the
    ambiguous middle — a built table, nothing fetching, nothing in it, and no
    text explaining that — which is what a portal with no open bids looks like
    when it renders no placeholder at all. IDLE is *not* treated as empty here:
    only the caller, which can watch it hold still, is in a position to decide
    that (see `_await_hydrated_rows`).

    This is the check the pane selector cannot make. `#openOpportunitiesTabPane`
    is in the markup the server sends, and DataTables puts a row in the table
    the moment it initialises — so "the pane exists" and even "a row exists" are
    both true well before any opportunity has been fetched. Reading the table at
    that point returns the loading row, which carries no reference and no link,
    is discarded by `read_rows` as if it were the empty-state placeholder, and
    reports an agency with live bids as having none. A silent nothing is worse
    than a timeout, and this is what separates the two cases.
    """
    if _is_processing(driver):
        return LOADING

    rows = driver.find_elements(*SEL["rows"])
    if not rows:
        # No rows at all. If DataTables has not built the table yet there is
        # still something to wait for; if it has, and is not fetching, then this
        # is a portal that renders an empty table rather than a placeholder —
        # nothing is coming, but it never says so, hence IDLE rather than EMPTY.
        return IDLE if _has_table(driver) else LOADING

    # A row carrying a reference or a link is a real opportunity — the same test
    # `read_rows` applies, so the two can never disagree about what counts.
    headers = read_headers(driver)
    for row in rows:
        try:
            record = _extract_row(row, headers)
        except StaleElementReferenceException:
            return LOADING  # the table is being rewritten under us
        if record.get("ref_number") or record.get("opportunity_url"):
            return READY

    # No real rows. Whether that is "finished and empty" or "not finished yet"
    # is written in the placeholder cell.
    for cell in driver.find_elements(*SEL["empty_cell"]):
        try:
            text = _text(cell)
        except StaleElementReferenceException:
            return LOADING
        if _STILL_LOADING.search(text):
            return LOADING
        if _SETTLED_EMPTY.search(text):
            return EMPTY

    # Rows exist, none of them are opportunities, and no placeholder text
    # explains why. Same situation as a rowless table: quite possibly finished
    # and empty, but the page has not said so, so it is not EMPTY on this
    # evidence alone. The caller decides how long to keep looking.
    return IDLE if _has_table(driver) else LOADING


def offered_tabs(driver) -> list[str]:
    """The labels of the tabs this portal actually renders.

    Used only for diagnostics, and worth the trouble: "no Open Public
    Opportunities tab — this page offers My Opportunities" says what went wrong
    in a way that "selector timed out" never will.
    """
    labels: list[str] = []
    for tab in driver.find_elements(*SEL["tabs"]):
        try:
            label = _text(tab)
        except StaleElementReferenceException:
            continue
        if label and label not in labels:
            labels.append(label)
    return labels


def has_open_tab(driver) -> bool:
    """Does this portal offer an Open Public Opportunities tab at all?"""
    return bool(driver.find_elements(*SEL["open_tab"]))


def has_frames(driver) -> bool:
    """Does this page embed a frame the table might be inside?

    Every portal seen so far renders the open list straight into the main
    document, which is why there is no frame-switching layer here: it would be
    untested code on the hot path of every agency. This reports the condition so
    that a portal which does embed its table is named as such in the run log
    instead of failing as an ordinary selector timeout.
    """
    return bool(driver.find_elements(*SEL["frames"]))


def read_rows(driver) -> list[dict[str, Any]]:
    """Every opportunity in the open list, as scraped field dicts.

    The whole list renders at once (the pane has no server-side pagination), so
    one pass over the rows captures it. The "There are no open projects at this
    time." placeholder row carries neither a ref number nor a link and is
    dropped.
    """
    headers = read_headers(driver)
    if not headers:
        logger.warning("open opportunities pane has no header row")
    records: list[dict[str, Any]] = []
    for row in driver.find_elements(*SEL["rows"]):
        try:
            record = _extract_row(row, headers)
        except StaleElementReferenceException:
            logger.warning("an opportunities row went stale mid-read — skipping it")
            continue
        if not record.get("ref_number") and not record.get("opportunity_url"):
            continue  # the "no open projects" placeholder
        records.append(record)
    return records


def _extract_row(row, headers: list[str]) -> dict[str, Any]:
    """One table row -> the bid fields it carries, plus its opportunity URL."""
    cells = row.find_elements(By.TAG_NAME, "td")
    details: dict[str, Any] = {"raw_data": {}}
    url: str | None = None
    for index, header in enumerate(headers):
        if index >= len(cells):
            break
        cell = cells[index]
        key = COLUMN_KEYS.get(_normalise(header))
        if key:
            value = _text(cell)
            if value:
                details[key] = value
                details["raw_data"][header] = value
        elif _normalise(header) == "action":
            links = cell.find_elements(*SEL["opportunity_link"])
            if links:
                url = links[0].get_attribute("href")
    if url is None:
        # No Action column (or an unmapped header): the link is still the only
        # anchor in the row that points at an opportunity.
        try:
            links = row.find_elements(*SEL["opportunity_link"])
            url = links[0].get_attribute("href") if links else None
        except NoSuchElementException:
            url = None
    details["opportunity_url"] = url
    return details
