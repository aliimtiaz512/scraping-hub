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
}

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
