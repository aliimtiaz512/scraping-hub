"""The results walk must not stop at page 1 because something covered the button.

BidNet's cookie banner renders over the pagination bar on a fresh session, so
Selenium's native click lands on the banner and raises
ElementClickInterceptedException. The walk used to treat that as "no further
pages" and stop — quietly, with a plausible-looking count.

On a keyword search that is nearly invisible: most keywords return a single
page, so page 1 *is* the whole result set. On a member-agency sweep it is the
difference between 25 bids and 1,850, which is exactly what it cost on the run
that produced this test.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_pagination.py
"""

import os
import sys

import pytest
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    WebDriverException,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper  # noqa: E402


class _Button:
    """A next-page control that the banner intercepts until it is dismissed."""

    def __init__(self, blocked_until_dismissed: bool = True, js_also_fails: bool = False):
        self.blocked = blocked_until_dismissed
        self.js_also_fails = js_also_fails
        self.native_clicks = 0

    def click(self):
        self.native_clicks += 1
        if self.blocked:
            raise ElementClickInterceptedException("element click intercepted")


class _Driver:
    def __init__(self, button: _Button, banner: bool = True):
        self.button = button
        self.banner = banner
        self.js_clicks = 0

    def find_elements(self, _by, _selector):
        # The cookie banner's Accept button, while the banner is up.
        return [_Banner(self)] if self.banner else []

    def execute_script(self, _script, element=None):
        if isinstance(element, _Banner):
            self.banner = False
            self.button.blocked = False
            return None
        self.js_clicks += 1
        if self.button.js_also_fails:
            raise WebDriverException("still not clickable")
        return None


class _Banner:
    def __init__(self, driver):
        self.driver = driver

    def is_displayed(self):
        return True


@pytest.fixture
def scraper(tmp_path):
    run = run_manager.create_run("bidnet", tmp_path, {"niche_label": "X"})
    return BidnetScraper(run["run_id"], ["kw"], None, "X")


def test_an_intercepted_next_click_dismisses_the_banner_and_carries_on(scraper):
    """The walk continues — and it continues on the *same* button, not by
    reloading, so the page it lands on is the one that was next."""
    button = _Button()
    scraper.driver = _Driver(button)

    assert scraper._click_next(button, page_num=1) is True
    assert button.native_clicks == 1, "the native click is still tried first"
    assert scraper.driver.banner is False, "the banner was never dismissed"
    assert scraper.driver.js_clicks == 1


def test_an_unblocked_next_click_costs_nothing_extra(scraper):
    """The fallback is a fallback: a page with no banner over it never reaches
    the DOM click, and never touches the banner selector."""
    button = _Button(blocked_until_dismissed=False)
    scraper.driver = _Driver(button, banner=False)

    assert scraper._click_next(button, page_num=1) is True
    assert scraper.driver.js_clicks == 0


def test_a_genuinely_unclickable_next_is_an_error_not_a_silent_stop(scraper):
    """Stopping is still the only option — but a truncated harvest reported as a
    complete one is how 25 bids passed for 1,850, so it goes on the run."""
    button = _Button(js_also_fails=True)
    scraper.driver = _Driver(button)

    assert scraper._click_next(button, page_num=7) is False
    errors = run_manager.get_run(scraper.run_id)["errors"]
    assert any("page 7" in e and "NOT" in e for e in errors), errors


def test_dismissing_the_banner_never_raises(scraper):
    """It is called from the middle of `open_filtered_session`; anything it
    raised would abort the whole filtered session over a banner that may not
    even be there."""
    scraper.driver = None  # the bluntest possible failure
    scraper._dismiss_cookie_banner()  # must not raise
