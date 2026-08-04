"""Solicitations gated behind BidNet's "required acknowledgement" page.

Requesting some bids redirects to `/private/supplier/solicitations/<id>/req-ack`,
which asks the vendor to Accept or Decline something before the bid is readable —
an attestation ("Company must be based in the United States of America"),
confirmation that an addendum was read, or a pass/fail requirement.

Two properties of that page defeated the original detail scrape, and both are
pinned down here:

  * the page *does* contain `.mets-field` elements (the acknowledgement's own),
    so waiting for that selector succeeds and the loader looks healthy;
  * every solicitation label is absent, so all seven fields come back "" — which
    was filed as EXTRACTION_FAILED and then retried onto the identical wall.

The stub driver models the real thing closely enough to matter: the Accept
button goes stale once the page moves on (that is how the code confirms the
click landed rather than assuming it), acknowledgements can be stacked, and the
cookie banner can sit over the button bar.

    server/.venv/bin/python server/tests/test_bidnet_acknowledgement.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from selenium.common.exceptions import (  # noqa: E402
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)

from app.config import settings  # noqa: E402
from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet.scraper import (  # noqa: E402
    DETAIL_FIELDS,
    STATUS_ACK_REQUIRED,
    STATUS_OK,
    BidnetScraper,
)

# Taken verbatim from the live pages that failed (bids 9454726201, 9336819005).
ACK_URL = "https://www.bidnetdirect.com/private/supplier/solicitations/9454726201/req-ack"
BID_URL = "https://www.bidnetdirect.com/private/supplier/interception/open-solicitation/9454726201?target=view"
ACK_NAME = "U.S.-Based Company"
SECOND_ACK_NAME = "Pass/Fail Requirements"
ACK_MESSAGE = (
    "Company must be based in the United States of America. Please acknowledge "
    "that the company submitting this proposal is a U.S.-based company."
)
HEADING = "RFP_F-0000000013 - DESTINATION DUTCHESS STRATEGIC PLANNING CONSULTANT RFP"


class FakeElement:
    """An element that can go stale, the way a real one does on navigation."""

    def __init__(self, text="", on_click=None, staleness=None, on_js_click=None):
        self.text = text
        self._on_click = on_click
        # A JS click reaches the element even when something overlays it, which
        # is the whole reason it is the fallback — so it is modelled separately
        # from the native click rather than sharing its interception.
        self._on_js_click = on_js_click or on_click
        # (driver, value-at-capture) — once the driver moves past that value the
        # element is stale, which is exactly what EC.staleness_of checks.
        self._staleness = staleness

    def is_displayed(self):
        return True

    def is_enabled(self):
        if self._staleness:
            driver, captured = self._staleness
            if driver.walls != captured:
                raise StaleElementReferenceException("element is not attached")
        return True

    def click(self):
        if self._on_click:
            self._on_click()


class FakeDriver:
    """An acknowledgement wall (possibly several) in front of a real bid."""

    FIELDS = {
        "Reference Number": "0000431640",
        "Solicitation Number": "2026-008",
        "Solicitation Type": "RFP - Request for Proposal (Formal)",
        "Title": "Destination Dutchess Strategic Planning Consultant RFP",
        "Publication": "07/23/2026 03:37 PM EDT",
        "Question Acceptance Deadline": "",  # genuinely absent on this bid
        "Closing Date": "09/18/2026 05:00 PM EDT",
    }

    def __init__(self, walls=1, accept_works=True, cookie_banner=False, block_native=False):
        self.walls = walls
        self.accept_works = accept_works
        self.cookie_banner = cookie_banner
        self.block_native = block_native  # native click intercepted -> JS fallback
        self.loads = 0
        self.accepted = 0
        self.cookie_dismissed = 0
        self.js_clicks = 0

    @property
    def gated(self):
        return self.walls > 0

    @property
    def current_url(self):
        return ACK_URL if self.gated else BID_URL

    def get(self, url):
        self.loads += 1

    def _accept(self):
        self.accepted += 1
        if self.accept_works:
            self.walls -= 1

    def _native_accept(self):
        if self.block_native:
            raise WebDriverException("element click intercepted")
        self._accept()

    def _dismiss_cookies(self):
        self.cookie_dismissed += 1
        self.cookie_banner = False

    def find_elements(self, by, selector):
        if selector == "#cookieBannerAcceptBtn":
            return [FakeElement(on_click=self._dismiss_cookies)] if self.cookie_banner else []
        if selector == "#requiredAcknowledgementConfirmPage":
            if not self.gated:
                return []
            return [
                FakeElement(
                    on_click=self._native_accept,
                    on_js_click=self._accept,
                    staleness=(self, self.walls),
                )
            ]
        # The acknowledgement page carries .mets-field elements of its own —
        # the reason waiting on that selector never revealed the problem.
        if ".mets-field" in selector:
            return [FakeElement()]
        if not self.gated:
            return []
        if selector == ".acknowledgementName":
            return [FakeElement(ACK_NAME if self.walls == 1 else SECOND_ACK_NAME)]
        if selector == ".noWidthAcknowledgementMessage":
            return [FakeElement(ACK_MESSAGE)]
        if selector in ("h1, h2", "h1", "h2"):
            return [FakeElement(HEADING)]
        return []

    def find_element(self, by, value):
        if str(by).lower().startswith("css"):
            found = self.find_elements(by, value)
            if not found:
                raise NoSuchElementException(value)
            return found[0]
        # the detail extractor's XPath field lookup
        if self.gated:
            raise NoSuchElementException("no solicitation fields on an acknowledgement page")
        for label, text in self.FIELDS.items():
            if f'"{label}"' in value:
                if not text:
                    raise NoSuchElementException(label)
                return FakeElement(text)
        raise NoSuchElementException(value)

    def execute_script(self, script, *args):
        if ".click()" in script:
            target = args[0] if args else None
            self.js_clicks += 1
            if target is not None and target._on_js_click:
                target._on_js_click()
            return None
        return 0  # jQuery.active, for _await_ajax_idle


class _Wait:
    """A WebDriverWait that evaluates the real expected-condition, no sleeping."""

    def __init__(self, driver):
        self.driver = driver

    def until(self, condition):
        for _ in range(50):
            try:
                result = condition(self.driver)
            except (NoSuchElementException, StaleElementReferenceException):
                result = False
            if result:
                return result
        raise TimeoutException("condition never became true")


def _scraper(driver):
    run = run_manager.create_run("bidnet", Path("/tmp"), {"niche_label": "Test"})
    s = BidnetScraper(run["run_id"], ["kw"], niche_label="Test")
    s.driver = driver
    s.wait = lambda *a, **k: _Wait(driver)
    s.screenshot = lambda *a, **k: None
    return s


def _auto(value):
    settings.bidnet_auto_accept_acknowledgements = value


# -- detection --------------------------------------------------------------


def test_the_acknowledgement_page_is_recognised():
    s = _scraper(FakeDriver(walls=1))
    gate = s._acknowledgement_gate()
    assert gate is not None
    assert gate["name"] == ACK_NAME
    assert gate["message"] == ACK_MESSAGE
    assert "/req-ack" in gate["url"]


def test_a_normal_bid_page_is_not_mistaken_for_one():
    s = _scraper(FakeDriver(walls=0))
    assert s._acknowledgement_gate() is None


# -- accepting --------------------------------------------------------------


def test_accepting_opens_the_bid_and_extracts_its_fields():
    """The whole point: Accept, then read the solicitation behind it."""
    _auto(True)
    driver = FakeDriver(walls=1)
    s = _scraper(driver)
    record = s.process_bid(BID_URL, Path("/tmp"))

    assert driver.accepted == 1
    assert record["status"] == STATUS_OK, record
    assert record["reference_number"] == "0000431640"
    assert record["solicitation_number"] == "2026-008"
    assert record["title"] == "Destination Dutchess Strategic Planning Consultant RFP"
    assert record["closing_date"] == "09/18/2026 05:00 PM EDT"


def test_stacked_acknowledgements_are_all_accepted():
    """Accepting one can land on the next rather than on the bid."""
    _auto(True)
    driver = FakeDriver(walls=3)
    s = _scraper(driver)
    record = s.process_bid(BID_URL, Path("/tmp"))

    assert driver.accepted == 3, driver.accepted
    assert record["status"] == STATUS_OK, record
    assert len(s._accepted_acknowledgements) == 3


def test_the_bid_is_read_without_reloading_it():
    """Accept navigates straight to the bid — re-requesting it is a wasted trip."""
    _auto(True)
    driver = FakeDriver(walls=1)
    s = _scraper(driver)
    s.process_bid(BID_URL, Path("/tmp"))
    assert driver.loads == 1, f"reloaded the bid {driver.loads} times"


def test_the_cookie_banner_is_dismissed_before_accepting():
    """The banner sits over the button bar and would swallow the click."""
    _auto(True)
    driver = FakeDriver(walls=1, cookie_banner=True)
    s = _scraper(driver)
    record = s.process_bid(BID_URL, Path("/tmp"))
    assert driver.cookie_dismissed == 1
    assert driver.accepted == 1
    assert record["status"] == STATUS_OK


def test_an_intercepted_native_click_falls_back_to_js():
    _auto(True)
    driver = FakeDriver(walls=1, block_native=True)
    s = _scraper(driver)
    record = s.process_bid(BID_URL, Path("/tmp"))
    assert driver.js_clicks >= 1
    assert driver.accepted == 1
    assert record["status"] == STATUS_OK


def test_accepted_acknowledgements_are_recorded_for_the_run():
    """Each one is a submission the agency can see — never accept silently."""
    _auto(True)
    driver = FakeDriver(walls=1)
    s = _scraper(driver)
    s.process_bid(BID_URL, Path("/tmp"))
    assert s._accepted_acknowledgements == [{"url": BID_URL, "name": ACK_NAME}]


# -- when accepting does not work -------------------------------------------


def test_a_swallowed_click_is_reported_not_assumed_to_have_worked():
    _auto(True)
    driver = FakeDriver(walls=1, accept_works=False)
    s = _scraper(driver)
    record = s.process_bid(BID_URL, Path("/tmp"))
    assert record["status"] == STATUS_ACK_REQUIRED, record["status"]
    assert record["title"] == HEADING
    assert s._acknowledgement_required


def test_accepting_is_bounded():
    """A page that never clears must not spin forever."""
    from app.scrapers.bidnet.scraper import MAX_ACK_ACCEPTS

    _auto(True)
    driver = FakeDriver(walls=99, accept_works=True)
    s = _scraper(driver)
    record = s.process_bid(BID_URL, Path("/tmp"))
    assert driver.accepted == MAX_ACK_ACCEPTS, driver.accepted
    assert record["status"] == STATUS_ACK_REQUIRED


def test_turning_it_off_leaves_the_bid_untouched_and_flagged():
    _auto(False)
    driver = FakeDriver(walls=1)
    s = _scraper(driver)
    record = s.process_bid(BID_URL, Path("/tmp"))
    assert driver.accepted == 0, "accepted despite the setting being off"
    assert record["status"] == STATUS_ACK_REQUIRED
    assert set(DETAIL_FIELDS) <= set(record)
    assert record["documents"] == [] and record["documents_count"] == "0"


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        _auto(True)
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001 — report, don't abort the suite
            print(f"ERROR {name}: {exc.__class__.__name__}: {exc}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
