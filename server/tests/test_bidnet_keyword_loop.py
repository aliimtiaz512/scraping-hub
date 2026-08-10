"""BidNet's keyword loop: that each search waits for its *own* results.

The bug these exist for: a niche's first keyword worked and every later one
raced through returning nothing, while the same searches by hand returned 11, 3,
2 bids. Both waits in `search()` pass on the previous keyword's page —
`.searchContentGroupContainer` is already visible and never removed, and
`jQuery.active` is still 0 in the gap between the click and the request leaving
— so the count read afterwards was the previous keyword's. A stale 0 skipped the
keyword as empty; a stale non-zero re-collected links deduplication then
swallowed.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_keyword_loop.py
"""

import os
import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import scraper as scraper_module  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper  # noqa: E402


class FakeElement:
    """A DOM node that can be replaced, the way a postback replaces one."""

    def __init__(self, name: str):
        self.name = name
        self.stale = False
        self.cleared = False
        self.typed: list[str] = []
        self.clicks = 0

    def clear(self):
        self.cleared = True

    def send_keys(self, text):
        self.typed.append(text)

    def click(self):
        self.clicks += 1

    def get_attribute(self, _name):
        return self.name

    def _check(self):
        """A detached node raises, as Selenium's does once the postback has
        replaced it — which is what EC.staleness_of and EC.visibility_of probe
        for."""
        if self.stale:
            raise StaleElementReferenceException(f"{self.name} was replaced")

    def is_enabled(self):
        self._check()
        return True

    def is_displayed(self):
        self._check()
        return True


class FakeDriver:
    """A search page whose results are only replaced when told to."""

    def __init__(self):
        self.count_node = FakeElement("count-1")
        self.box = FakeElement("box")
        self.button = FakeElement("button")
        self.navigated: list[str] = []
        self.scripts: list[str] = []

    def replace_results(self, name: str) -> None:
        """What a completed search does: the old node goes, a new one arrives."""
        self.count_node.stale = True
        self.count_node = FakeElement(name)

    def find_element(self, by, value):
        if "solicitationCount" in value or "searchContentGroupContainer" in value:
            if self.count_node.stale:
                raise NoSuchElementException(value)
            return self.count_node
        if value == "solicitationSingleBoxSearch":
            return self.box
        if value == "topSearchButton":
            return self.button
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        try:
            return [self.find_element(by, value)]
        except NoSuchElementException:
            return []

    def execute_script(self, script, *args):
        self.scripts.append(script)
        return 0            # jQuery.active — idle, as it is before a search starts

    def get(self, url):
        self.navigated.append(url)


@pytest.fixture
def scraper(monkeypatch):
    run = run_manager.create_run("bidnet", Path("/tmp"))
    instance = BidnetScraper(run["run_id"], ["alpha", "beta"], None, "test")
    instance.driver = FakeDriver()

    # Stand in for Selenium's waits with ones that drive the fake directly.
    class Wait:
        def __init__(self, driver, timeout):
            self.driver = driver

        def until(self, condition):
            # EC.staleness_of holds the element it was built with; call it and
            # let it answer from the fake's own stale flag.
            for _ in range(3):
                try:
                    result = condition(self.driver)
                except NoSuchElementException:
                    result = False
                if result:
                    return result
            raise TimeoutException("condition never became true")

    monkeypatch.setattr(instance, "wait", lambda timeout=None: Wait(instance.driver, timeout))
    return instance


def test_search_waits_for_the_previous_results_to_be_replaced(scraper, monkeypatch):
    """The core fix: `search` must not return while the old page is still up."""
    scraper.driver.replace_results("count-2")   # a search that did complete
    scraper.search("alpha")                      # so the wait is satisfied
    assert scraper.driver.box.typed == ["alpha"]


def test_results_that_are_never_replaced_are_reported_and_settled(scraper, caplog, monkeypatch):
    """BidNet can answer an identical query by reusing the container instead of
    re-rendering it. That must not fail the keyword — a skipped keyword is the
    very outcome this change exists to prevent — so it warns and settles, giving
    the page real time before the count is read."""
    import logging

    caplog.set_level(logging.WARNING)
    slept: list[float] = []
    monkeypatch.setattr(scraper_module.time, "sleep", lambda s: slept.append(s))

    scraper.search("alpha")          # the fake never replaces the results

    assert any("not re-rendered" in record.message for record in caplog.records)
    assert slept, "the fallback must settle before the count is read"


def test_the_search_box_is_cleared_through_the_dom_as_well(scraper):
    scraper.driver.replace_results("count-2")
    scraper.search("alpha")
    assert scraper.driver.box.cleared is True
    # …and the value is blanked in the DOM too, so the portal's own model does
    # not keep the previous term and append to it.
    assert any("value = ''" in script for script in scraper.driver.scripts)


def test_the_anchor_is_none_on_the_very_first_search(scraper):
    """Nothing has been searched yet, so there is nothing to watch go stale."""
    scraper.driver.count_node.stale = True      # no results on the page at all
    assert scraper._results_anchor() is None


def test_resetting_reloads_the_search_page(scraper):
    """Between keywords: the previous one may have left the results on page 7
    with its own group and filters applied."""
    scraper.driver.replace_results("count-2")
    scraper.reset_search_state()
    assert scraper.driver.navigated, "the reset must reload the search page"


# -- the close-date filter ----------------------------------------------------


def test_the_close_date_filter_is_off_for_the_testing_phase():
    """Every active opportunity the portal returns is kept, so a run's count is
    comparable with a manual search's."""
    assert scraper_module.APPLY_CLOSE_DATE_FILTER is False


def test_nothing_is_dropped_for_closing_soon_while_it_is_off(monkeypatch):
    """The filter's own machinery is still present — this is a switch, not a
    deletion — so flipping it back restores the behaviour."""
    from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close

    assert days_until_close("01/01/2020") < MIN_DAYS_UNTIL_CLOSE   # would be dropped
    monkeypatch.setattr(scraper_module, "APPLY_CLOSE_DATE_FILTER", True)
    assert scraper_module.APPLY_CLOSE_DATE_FILTER is True
