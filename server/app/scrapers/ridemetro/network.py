"""The Euna Supplier Network side of the flow: the hop out of the Bonfire
portal, the My Network tab, and the agency roster it lists.

Layout of the page this reads (verified against the live account):

    <a class="supplier-btn" href="https://vendor.bonfirehub.com" target="_blank">
        <span class="supplier-text">My Euna Supplier Network</span></a>

    <a role="tab" href="/agencies">My Network</a>

    <ol>
      <li data-testid="agency-2974208-us-list-item">
        …<p>Metropolitan Transit Authority of Harris County (METRO)</p>   <- name
        …<p>Date Created</p>2026-07-14
        …<p>Status</p>Incomplete                                         <- the filter
        <a aria-label="Go to Metropolitan Transit …" target="_blank"
           href="https://ridemetro.bonfirehub.com/registration">Go to Agency</a>
      </li>
      <li data-testid="agency-1080662-ca-list-item">
        …<p>Bonfire</p>
        …<p>Status</p>Complete
        <button aria-label="Go to Bonfire">Go to Agency</button>         <- no href
      </li>
      …
    </ol>

Two things about that markup drive everything below.

*Go to Agency is not always a link.* Roughly a quarter of the roster renders it
as a bare `<button>` with no href — on this account every such card is a `-ca`
(Canadian region) agency, whose portal the SPA reaches by navigating the current
tab itself rather than by opening a target="_blank" href. A locator that says
`a[aria-label^='Go to ']` therefore does not merely lose those cards' URLs, it
loses the cards: they never enter the roster at all. Both element types are
matched here, and an agency with a control but no href is legal — it is opened
by clicking, not by URL.

*The roster is a list of MUI cards, not a table.* The "Status column" is a
labelled field inside each card, and its value sits in a text node *after* the
label's <p>, which is why it is read off the enclosing box's text rather than an
element of its own. An Incomplete agency's button points at that agency's
/registration page rather than its portal, which is why those rows are skipped:
there are no opportunities behind them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)

# "Go to Agency" control label prefix — also how we recover an agency's name.
_GO_TO = "Go to "

# The control is an <a> for agencies the SPA can link straight to and a <button>
# for the ones it navigates to itself. Anything that only matches the anchor
# silently drops the button-rendered agencies from the whole sweep.
_GO_TO_CONTROL = f"a[aria-label^='{_GO_TO}'], button[aria-label^='{_GO_TO}']"

SEL = {
    # Bonfire portal -> Euna Supplier Network. The <li> around it is rendered
    # only when the account actually has a supplier network.
    "supplier_network_button": (By.CSS_SELECTOR, "a.supplier-btn, a[href*='vendor.bonfirehub.com']"),
    # Supplier network top nav: Dashboard | My Network | Account Settings | My Tasks.
    "my_network_tab": (By.CSS_SELECTOR, "a[role='tab'][href='/agencies'], a[href='/agencies']"),
    # One card per agency; the active/inactive split is a tab above them. This
    # is the roster's ground truth — every card has this attribute, whether or
    # not it has a link, a logo or a readable status.
    "agency_cards": (By.CSS_SELECTOR, "li[data-testid^='agency-']"),
    "go_to_agency": (By.CSS_SELECTOR, _GO_TO_CONTROL),
    # "Active(24)" / "Inactive(0)" — the count is how we tell a short render from
    # a genuinely short roster.
    "agency_tabs": (By.CSS_SELECTOR, "[aria-label='Agency Tabs'] button[role='tab']"),
}

# Status values that mean "registration finished, portal reachable". Matched
# whole-word: "Incomplete" contains "complete", so a substring test would let
# every skipped agency through.
_COMPLETE = re.compile(r"\bcomplete\b", re.IGNORECASE)
_INCOMPLETE = re.compile(r"\bincomplete\b", re.IGNORECASE)
_ACTIVE_COUNT = re.compile(r"active\s*\((\d+)\)", re.IGNORECASE)
# data-testid="agency-1080662-ca-list-item" -> "1080662-ca"
_CARD_ID = re.compile(r"^agency-(.+?)-list-item$")


@dataclass
class Agency:
    """One row of the My Network roster."""

    name: str
    status: str
    url: str
    # The portal's own id for the row ("2974208-us"), off the card's
    # data-testid. Diagnostics quote it because agency names are long, repeat
    # across regions, and are not what the portal keys on.
    agency_id: str = ""
    # Filled in as the run progresses, and reported on the run row.
    opportunities: int = 0
    error: str | None = None
    # Why this agency has no rows, when that is a fact about the agency rather
    # than a failure to read it — an org in My Network that publishes no public
    # portal, say. Kept apart from `error` so the report and the console can
    # tell "nothing to read here" from "we could not read it".
    note: str | None = None
    documents: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return bool(_COMPLETE.search(self.status or ""))

    @property
    def is_incomplete(self) -> bool:
        """Explicitly Incomplete, as opposed to a status we could not read.

        Both are skipped, but only one of them is the portal telling us to.
        """
        return bool(_INCOMPLETE.search(self.status or ""))

    @property
    def label(self) -> str:
        """How an agency is named in a log line: id first, then the name."""
        return f"[{self.agency_id or '?'}] {self.name}"

    def as_record(self) -> dict:
        return {
            "name": self.name,
            "agency_id": self.agency_id,
            "url": self.url,
            "status": self.status,
            "skipped": not self.is_complete,
            "opportunities": self.opportunities,
            "error": self.error,
            "note": self.note,
        }


def _card_id(card) -> str:
    match = _CARD_ID.match((card.get_attribute("data-testid") or "").strip())
    return match.group(1) if match else ""


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


def _card_name(card) -> str:
    """The agency's name, from the card's heading.

    Used when the "Go to Agency" control is missing or unlabelled. The select
    checkbox carries the same name ("Select Bonfire") and is tried second
    because the heading is the thing a reader sees.
    """
    try:
        heading = card.find_element(By.CSS_SELECTOR, "p")
        name = (heading.get_attribute("textContent") or heading.text or "").strip()
        if name:
            return name
    except NoSuchElementException:
        pass
    try:
        box = card.find_element(By.CSS_SELECTOR, "input[type='checkbox'][aria-label^='Select ']")
        return (box.get_attribute("aria-label") or "")[len("Select "):].strip()
    except NoSuchElementException:
        return ""


def read_agencies(driver) -> list[Agency]:
    """Read the My Network roster in the order the page lists it.

    Every card the portal renders comes back, including the ones whose "Go to
    Agency" is a button with no href: those are navigable by click, so a missing
    URL is not a reason to drop the agency. A card with no name at all is
    dropped with a log line, since there is then nothing to click, report or
    reconcile it by.
    """
    agencies: list[Agency] = []
    for card in driver.find_elements(*SEL["agency_cards"]):
        try:
            agency_id = _card_id(card)
            name = ""
            url = ""
            try:
                control = card.find_element(By.CSS_SELECTOR, _GO_TO_CONTROL)
            except NoSuchElementException:
                # An agency whose control has not rendered can still be listed
                # and reconciled against the Active count; the sweep reports it
                # rather than pretending the roster was this short.
                control = None
                logger.warning("agency card %s has no 'Go to Agency' control", agency_id or "?")
            if control is not None:
                label = (control.get_attribute("aria-label") or "").strip()
                if label.startswith(_GO_TO):
                    name = label[len(_GO_TO):].strip()
                # A <button> control has no href; that agency is opened by
                # clicking it, and `url` stays empty on purpose.
                url = (control.get_attribute("href") or "").strip()
            if not name:
                name = _card_name(card)
            status = _card_status(card)
        except StaleElementReferenceException:
            logger.warning("the My Network list re-rendered while it was being read")
            continue
        if not name:
            logger.warning("skipping an unreadable agency card (%s)", agency_id or "no id")
            continue
        agencies.append(Agency(name=name, status=status, url=url, agency_id=agency_id))
    return agencies


def go_to_selector(agency_name: str) -> tuple[str, str]:
    """A locator for one agency's "Go to Agency" control.

    Matches the anchor and the button forms both, for the same reason
    `_GO_TO_CONTROL` does. The name is quoted because agency names carry
    apostrophes, accents and parentheses ("Metropolitan Transit Authority of
    Harris County (METRO)"), which break a naively interpolated selector.
    """
    label = _GO_TO + agency_name
    quoted = '"' + label.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return (By.CSS_SELECTOR, f"a[aria-label={quoted}], button[aria-label={quoted}]")


def active_count(driver) -> int | None:
    """How many agencies the Active tab claims, or None if the tab isn't there.

    Used as a cross-check: the roster renders client-side, so a count that
    exceeds the cards we found means the list was still filling in (or is paged)
    and the run should say so rather than quietly sweeping a subset. The number
    counts every card in the Active panel, Incomplete ones included — it is the
    total to reconcile against, not the number that will be scraped.
    """
    for tab in driver.find_elements(*SEL["agency_tabs"]):
        match = _ACTIVE_COUNT.search((tab.get_attribute("textContent") or "").strip())
        if match:
            return int(match.group(1))
    return None
