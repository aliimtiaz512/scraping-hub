"""The Euna Supplier Network side of the flow: the hop out of the Bonfire
portal, the My Network tab, and the agency roster it lists.

Layout of the page this reads (verified against the live account):

    <a class="supplier-btn" href="https://vendor.bonfirehub.com" target="_blank">
        <span class="supplier-text">My Euna Supplier Network</span></a>

    <a role="tab" href="/agencies">My Network</a>

    <ol>
      <li data-testid="agency-2512862-us-list-item">
        …<p>Ada County</p>                       <- name
        …<p>Date Created</p>2025-11-19
        …<p>Status</p>Incomplete                 <- the filter
        <a aria-label="Go to Ada County" target="_blank"
           href="https://adacounty.bonfirehub.com/registration">Go to Agency</a>
      </li>
      …
    </ol>

Note the roster is a list of MUI cards, not a table — the "Status column" is a
labelled field inside each card, and its value sits in a text node *after* the
label's <p>, which is why it is read off the enclosing box's text rather than an
element of its own. An Incomplete agency's button points at that agency's
/registration page rather than its portal, which is the other half of why those
rows are skipped: there are no opportunities behind them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)

# "Go to Agency" anchor label prefix — also how we recover an agency's name.
_GO_TO = "Go to "

SEL = {
    # Bonfire portal -> Euna Supplier Network. The <li> around it is rendered
    # only when the account actually has a supplier network.
    "supplier_network_button": (By.CSS_SELECTOR, "a.supplier-btn, a[href*='vendor.bonfirehub.com']"),
    # Supplier network top nav: Dashboard | My Network | Account Settings | My Tasks.
    "my_network_tab": (By.CSS_SELECTOR, "a[role='tab'][href='/agencies'], a[href='/agencies']"),
    # One card per agency; the active/inactive split is a tab above them.
    "agency_cards": (By.CSS_SELECTOR, "li[data-testid^='agency-']"),
    "go_to_agency": (By.CSS_SELECTOR, f"a[aria-label^='{_GO_TO}']"),
    # "Active(5)" / "Inactive(0)" — the count is how we tell a short render from
    # a genuinely short roster.
    "agency_tabs": (By.CSS_SELECTOR, "[aria-label='Agency Tabs'] button[role='tab']"),
}

# Status values that mean "registration finished, portal reachable". Matched
# whole-word: "Incomplete" contains "complete", so a substring test would let
# every skipped agency through.
_COMPLETE = re.compile(r"\bcomplete\b", re.IGNORECASE)
_ACTIVE_COUNT = re.compile(r"active\s*\((\d+)\)", re.IGNORECASE)


@dataclass
class Agency:
    """One row of the My Network roster."""

    name: str
    status: str
    url: str
    # Filled in as the run progresses, and reported on the run row.
    opportunities: int = 0
    error: str | None = None
    documents: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return bool(_COMPLETE.search(self.status or ""))

    def as_record(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "status": self.status,
            "skipped": not self.is_complete,
            "opportunities": self.opportunities,
            "error": self.error,
        }


def _card_status(card) -> str:
    """The value of the card's Status field.

    The markup is `<div><div><icon><p>Status</p></div>Incomplete</div>`: the
    value is a bare text node next to the label's wrapper, so there is no
    element to select it by. We take the enclosing box's text and drop the
    label — the same thing a reader sees.
    """
    try:
        box = card.find_element(
            By.XPATH, ".//p[normalize-space()='Status']/ancestor::div[2]"
        )
    except NoSuchElementException:
        return ""
    text = (box.get_attribute("textContent") or box.text or "").strip()
    return re.sub(r"^\s*Status\s*", "", text, flags=re.IGNORECASE).strip()


def read_agencies(driver) -> list[Agency]:
    """Read the My Network roster in the order the page lists it.

    Cards whose name or link can't be read are dropped with a log line rather
    than guessed at: without a URL there is nothing to visit anyway.
    """
    agencies: list[Agency] = []
    for card in driver.find_elements(*SEL["agency_cards"]):
        try:
            anchor = card.find_element(*SEL["go_to_agency"])
            label = (anchor.get_attribute("aria-label") or "").strip()
            name = label[len(_GO_TO):].strip() if label.startswith(_GO_TO) else ""
            if not name:
                # Fall back to the card's own heading.
                name = (card.find_element(By.CSS_SELECTOR, "p").get_attribute("textContent") or "").strip()
            url = (anchor.get_attribute("href") or "").strip()
        except (NoSuchElementException, StaleElementReferenceException):
            logger.warning("skipping an agency card with no 'Go to Agency' link")
            continue
        if not name or not url:
            continue
        agencies.append(Agency(name=name, status=_card_status(card), url=url))
    return agencies


def go_to_selector(agency_name: str) -> tuple[str, str]:
    """A locator for one agency's "Go to Agency" button.

    The name is quoted because agency names carry apostrophes and parentheses
    ("Metropolitan Transit Authority of Harris County (METRO)"), which break a
    naively interpolated attribute selector.
    """
    label = _GO_TO + agency_name
    quoted = '"' + label.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return (By.CSS_SELECTOR, f"a[aria-label={quoted}]")


def active_count(driver) -> int | None:
    """How many agencies the Active tab claims, or None if the tab isn't there.

    Used as a cross-check: the roster renders client-side, so a count that
    exceeds the cards we found means the list was still filling in (or is paged)
    and the run should say so rather than quietly sweeping a subset.
    """
    for tab in driver.find_elements(*SEL["agency_tabs"]):
        match = _ACTIVE_COUNT.search((tab.get_attribute("textContent") or "").strip())
        if match:
            return int(match.group(1))
    return None
