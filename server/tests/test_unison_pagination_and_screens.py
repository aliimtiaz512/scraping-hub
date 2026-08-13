"""Unison: the whole listing, the early-exit screens, and fewer manual reviews.

The reported failure: the portal said 115 buys, the run processed 100, and
nothing said so. The walk existed — it looked for `<a title="Next Page">`, which
this portal does not render. The control is a row *inside* the results table
("< Prev  1 2  Next >"), the same row the extractor has always had to skip. So
`next_page_url()` found nothing, read that as "last page", and the run finished
clean fifteen buys short, because the check that would have caught it read the
summary line out of an element this markup does not have either.

    server/.venv/bin/python -m pytest server/tests/test_unison_pagination_and_screens.py
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.unison import evaluation as ev  # noqa: E402
from app.scrapers.unison.engine.unison_scraper import UnisonMarketplaceScraper  # noqa: E402


# =============================================================================
# Pagination
# =============================================================================


class FakeAnchor:
    def __init__(self, href="", title="", text="", css_class=""):
        self._attrs = {"href": href, "title": title, "class": css_class}
        self.text = text

    def get_attribute(self, name):
        return self._attrs.get(name)


class FakeListing:
    """A paged listing whose Next control can be rendered in either shape.

    `titled=False` is the portal as it actually is: the link reads "Next >" and
    carries no title attribute.
    """

    def __init__(self, total, per_page=100, titled=False, next_link=True):
        self.total = total
        self.per_page = per_page
        self.titled = titled
        self.next_link = next_link
        self.page = 1
        self.current_url = f"https://m.unison.com/fbweb/allOpps.do?pageNum=1&pageSize={per_page}&filterId=-1"
        self.visited = [self.current_url]

    # -- what the page says --------------------------------------------------

    @property
    def first(self):
        return (self.page - 1) * self.per_page + 1

    @property
    def last(self):
        return min(self.page * self.per_page, self.total)

    def summary_text(self):
        return f"{self.first} - {self.last} of {self.total} Buys"

    def rows_here(self):
        # The keys `extract_request_data` actually builds — "Buyer#", not the
        # hub's `buyer_number`, which the runner applies later. A fake that used
        # the hub's names is exactly why the de-duplication bug reached a live
        # run: every row keyed blank, so page 2 read as "all already seen".
        return [
            {"Buyer#": f"BUY-{n}", "Detail URL": f"/d/{n}", "Buyer Description": ""}
            for n in range(self.first, self.last + 1)
        ]

    # -- the driver surface the scraper uses ---------------------------------

    def find_element(self, _by, value):
        from selenium.common.exceptions import NoSuchElementException

        if value == "body":
            return FakeAnchor(text=f"Seller Dashboard {self.summary_text()} < Prev 1 2 Next >")
        raise NoSuchElementException(value)  # no span.page-summary on this markup

    def find_elements(self, _by, xpath):
        if not self.next_link or self.last >= self.total:
            # The last page still renders "Next >", inert.
            if "Next" in xpath and self.next_link:
                return [FakeAnchor(text="Next >", css_class="disabled")]
            return []
        href = (
            f"https://m.unison.com/fbweb/allOpps.do?pageNum={self.page + 1}"
            f"&pageSize={self.per_page}&filterId=-1"
        )
        if "@title='Next Page'" in xpath:
            return [FakeAnchor(href=href, title="Next Page")] if self.titled else []
        if "Next" in xpath or "pageNum=" in xpath:
            return [FakeAnchor(href=href, text="Next >")]
        return []

    def get(self, url):
        self.visited.append(url)
        self.current_url = url
        page = url.split("pageNum=")[1].split("&")[0]
        self.page = int(page)


def _scraper(listing):
    scraper = UnisonMarketplaceScraper()
    scraper.driver = listing
    # The page controls need a real select element; the walk itself is what is
    # under test, so they are stubbed out.
    scraper.set_page_size = lambda size="100": True
    scraper.apply_filter_id = lambda filter_id: True
    scraper._await_listing_reload = lambda: None
    scraper.extract_request_data = listing.rows_here
    return scraper


def test_the_walk_follows_a_next_link_that_has_no_title_attribute(caplog):
    """The bug, exactly: 115 buys, 100 on page 1, and the only Next control is a
    link reading "Next >" inside the results table."""
    caplog.set_level(logging.INFO)
    listing = FakeListing(total=115, titled=False)
    rows = _scraper(listing).collect_listing()

    assert len(rows) == 115, f"read {len(rows)} of 115"
    assert [r["Buyer#"] for r in rows][-1] == "BUY-115"


def test_the_titled_link_still_works(caplog):
    """The portal renders it on some listings; the fix must not trade one shape
    for the other."""
    listing = FakeListing(total=115, titled=True)
    assert len(_scraper(listing).collect_listing()) == 115


def test_the_walk_reports_the_pages_it_covered():
    listing = FakeListing(total=250)
    scraper = _scraper(listing)
    scraper.collect_listing()

    assert scraper.pages_scraped == 3
    assert scraper.expected_buys == 250


def test_a_single_page_listing_stops_at_one_page():
    listing = FakeListing(total=40)
    scraper = _scraper(listing)
    rows = scraper.collect_listing()

    assert len(rows) == 40
    assert scraper.pages_scraped == 1


def test_no_next_control_at_all_falls_back_to_the_page_number(caplog):
    """If the summary says buys remain and no link can be found, the control is
    there and unrecognised — go by page number rather than call a short listing
    complete."""
    caplog.set_level(logging.WARNING)
    listing = FakeListing(total=115, next_link=False)
    rows = _scraper(listing).collect_listing()

    assert len(rows) == 115
    assert any("continuing by page number" in r.getMessage() for r in caplog.records)


def test_a_short_listing_is_reported_loudly(caplog):
    """When it genuinely cannot get the rest, the run must say so — this is the
    check that was silent while fifteen buys went missing."""
    caplog.set_level(logging.ERROR)
    listing = FakeListing(total=115, next_link=False)
    scraper = _scraper(listing)
    scraper.page_url_for = lambda n: None          # no way forward at all
    rows = scraper.collect_listing()

    assert len(rows) == 100
    assert any("INCOMPLETE LISTING" in r.getMessage() for r in caplog.records)


def test_a_buy_repeated_across_pages_is_counted_once():
    """A listing that shifts under the walk re-renders a buy on the next page.
    Counted twice, it hides a row that was genuinely missed."""
    listing = FakeListing(total=150)
    scraper = _scraper(listing)
    scraper.extract_request_data = lambda: [
        {"Buyer#": "BUY-1", "Detail URL": "/d/1"},
        {"Buyer#": "BUY-2", "Detail URL": "/d/2"},
    ]
    rows = scraper.collect_listing()

    assert [r["Buyer#"] for r in rows] == ["BUY-1", "BUY-2"]


def test_the_total_is_read_from_the_first_page():
    """Read at the end it is the last page's summary, which agrees with itself
    however few pages were walked — and so could never catch an early stop."""
    listing = FakeListing(total=115)
    scraper = _scraper(listing)
    scraper.collect_listing()

    assert scraper.expected_buys == 115


def test_the_summary_is_found_outside_its_dedicated_element():
    listing = FakeListing(total=115)
    scraper = _scraper(listing)

    assert scraper.page_counts() == (1, 100, 115)


def test_an_inert_next_link_ends_the_walk():
    """The last page renders the same "Next >" with the anchor disabled."""
    scraper = UnisonMarketplaceScraper()
    scraper.driver = FakeListing(total=100)      # one page: last >= total
    assert scraper.next_page_url() is None


def test_a_page_url_is_only_built_from_a_paged_listing_url():
    """Guessing a query string for a URL that never carried one would invent a
    route the portal never showed us."""
    scraper = UnisonMarketplaceScraper()
    scraper.driver = FakeListing(total=100)
    scraper.driver.current_url = "https://m.unison.com/fbweb/sellerDashboard.do"

    assert scraper.page_url_for(2) is None


# =============================================================================
# Early-exit screens
# =============================================================================


@pytest.mark.parametrize("description", [
    "Cisco switches via GSA Schedules",
    "Purchase under gsa schedules 70",
    "GSA SCHEDULE 84 buy",
])
def test_gsa_schedules_is_rejected_before_the_funnel(description):
    verdict = ev.screen({"buy_description": description})

    assert verdict is not None
    rule, decision, reason = verdict
    assert (rule, decision) == ("screen:gsa", "REJECT")
    assert "GSA" in reason


def test_the_contract_vehicle_is_read_wherever_the_portal_put_it():
    """It is in the Buy Description on some buys and a General Information row on
    others; a screen that reads one passes exactly the buys stating the other."""
    verdict = ev.screen({
        "buy_description": "Network hardware refresh",
        "general_info": {"extra": {"Contract Vehicle": "GSA Schedules"}},
    })

    assert verdict is not None and verdict[0] == "screen:gsa"


@pytest.mark.parametrize("category", [
    "31A5 -- Hospitality and Food Services",
    "Hospitality and Food Services",
    "hospitality",
    "Food Services",
    "Catering",
])
def test_hospitality_and_food_is_rejected_before_the_funnel(category):
    verdict = ev.screen({"category": category})

    assert verdict is not None
    assert (verdict[0], verdict[1]) == ("screen:cat", "REJECT")


def test_the_subcategory_is_screened_too():
    verdict = ev.screen({"category": "Services", "subcategory": "Food Service Equipment"})

    assert verdict is not None and verdict[1] == "REJECT"


def test_a_hardware_buy_is_not_screened():
    """`7B20 -- HARDWARE AND PERPETUAL LICENSE SOFTWARE` is the category the
    classifier is kept away from precisely because it reads as a false reject.
    The screens must not reintroduce that."""
    assert ev.screen({
        "buy_description": "Dell OptiPlex workstations",
        "category": "7B20 -- HARDWARE AND PERPETUAL LICENSE SOFTWARE",
    }) is None


def test_a_screened_buy_never_reaches_the_funnel(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(ev, "evaluate_bid", lambda *a, **k: called.append("funnel"))

    verdict = ev.evaluate({"buy_number": "B1", "buy_description": "GSA Schedules order"})

    assert called == [], "the screen must skip the evaluation matrix entirely"
    assert verdict["decision"] == "REJECT"
    assert verdict["rule"] == "screen:gsa"


def test_the_rule_code_fits_the_column():
    """`rule` is String(16) — a code longer than that would fail the insert."""
    for record in ({"buy_description": "GSA Schedules"}, {"category": "Hospitality"}):
        assert len(ev.screen(record)[0]) <= 16


# =============================================================================
# Manual review, resolved
# =============================================================================


MANUAL = {"decision": "MANUAL_REVIEW", "reason": "unlisted service",
          "rule": "none", "requirement_type": "SERVICE", "location": "US_MAINLAND"}


def test_a_product_buy_parked_in_manual_review_is_decided():
    """The funnel never sees the Line Item table. A buy whose rows are quantified
    goods is a supply — that is a decision, not a judgement call."""
    record = {"line_items": [
        {"description": "Laptop", "unit": "each", "qty": "25"},
        {"description": "Docking station", "unit": "ea", "qty": "25"},
        {"description": "Shipping", "unit": "", "qty": ""},
    ]}
    result = ev.resolve_manual_review(record, dict(MANUAL))

    assert result["decision"] == "PURSUE"
    assert result["rule"] == "A"
    assert result["decision_before_strict"] == "MANUAL_REVIEW"
    assert "line items" in result["reason"]


def test_an_unevidenced_service_fails_closed():
    """Strict evaluation: a service on neither list, with nothing to show it is a
    product buy, is rejected rather than queued for a person."""
    result = ev.resolve_manual_review({"line_items": []}, dict(MANUAL))

    assert result["decision"] == "REJECT"
    assert result["decision_before_strict"] == "MANUAL_REVIEW"


def test_every_resolved_verdict_says_what_it_was_before():
    """Fewer manual reviews, not fewer traceable ones — a run has to be able to
    list exactly which buys the strictness decided."""
    for record in ({"line_items": []}, {"line_items": [
        {"description": "Cable", "unit": "each", "qty": "5"}]}):
        assert ev.resolve_manual_review(record, dict(MANUAL))["decision_before_strict"]


def test_decisions_the_funnel_actually_made_are_left_alone():
    for decision in ("PURSUE", "REJECT"):
        original = {"decision": decision, "rule": "B12", "reason": "as decided"}
        assert ev.resolve_manual_review({"line_items": []}, dict(original)) == original


def test_the_strictness_is_one_switch(monkeypatch):
    """Off, every borderline buy goes back to the queue — which is what the flow
    did before, and what a reviewer who wants them back would ask for."""
    monkeypatch.setattr(ev, "STRICT_FALLBACK", False)
    result = ev.resolve_manual_review({"line_items": []}, dict(MANUAL))

    assert result["decision"] == "MANUAL_REVIEW"
    assert "decision_before_strict" not in result


# -- what a live run caught that the fakes did not ----------------------------


def test_a_second_page_of_new_buys_is_not_discarded_as_seen():
    """The regression, from a live run: 136 detected, page 1 gave 100, page 2
    gave 36 genuinely new buys, and every one was dropped as "already seen".

    The de-duplication key read `buyer_number` — the *hub's* field name, applied
    later by the runner — so every row keyed to the empty string. Page 1's blank
    key then matched page 2's, and a whole page was thrown away by a guard whose
    job was to protect the count."""
    listing = FakeListing(total=136)
    rows = _scraper(listing).collect_listing()

    assert len(rows) == 136
    assert len({r["Buyer#"] for r in rows}) == 136


def test_rows_that_cannot_be_keyed_are_kept_not_merged():
    """An unkeyed row is one that cannot be compared, not one that equals every
    other unkeyed row. Dropping a buy is unrecoverable; a duplicate row is not."""
    listing = FakeListing(total=3, per_page=3)
    scraper = _scraper(listing)
    scraper.extract_request_data = lambda: [{"Buyer Description": "no id"}] * 3

    assert len(scraper.collect_listing()) == 3


def test_the_walk_does_not_step_past_the_last_page():
    """Also from the live run: on the last page a candidate selector matched a
    link that was not "next" at all, so the walk loaded a page 3 that does not
    exist, read nothing from it, and reported the listing short."""
    listing = FakeListing(total=136)
    scraper = _scraper(listing)
    scraper.collect_listing()

    assert scraper.pages_scraped == 2, "two pages hold 136 buys at 100 a page"
    assert [url.split("pageNum=")[1].split("&")[0] for url in listing.visited] == ["1", "2"]


def test_a_backwards_link_is_never_followed():
    """The later candidates match on href shape rather than on the word Next, so
    without a direction check they would happily return the Prev link."""
    scraper = UnisonMarketplaceScraper()

    class Backwards(FakeListing):
        def find_elements(self, _by, xpath):
            return [FakeAnchor(
                href="https://m.unison.com/fbweb/allOpps.do?pageNum=1&pageSize=100",
                text="Next >",
            )]

    listing = Backwards(total=200)
    listing.page = 2
    listing.current_url = "https://m.unison.com/fbweb/allOpps.do?pageNum=2&pageSize=100"
    scraper.driver = listing

    assert scraper.next_page_url() is None


# -- the Filter By criterion has to actually take ------------------------------


def test_a_stale_filter_control_is_retried_not_abandoned():
    """Setting Show: 100 reloads the listing, which detaches the Filter By select
    found a moment later. The criterion was dropped on a warning, so a run asking
    for "Posted Today" quietly read the entire listing."""
    from selenium.common.exceptions import StaleElementReferenceException

    scraper = UnisonMarketplaceScraper()
    attempts = {"n": 0}

    class Flaky:
        def find_element(self, *_a, **_k):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise StaleElementReferenceException("detached")
            return _FakeSelect()

    scraper.driver = Flaky()
    scraper._await_listing_reload = lambda: None
    import app.scrapers.unison.engine.unison_scraper as engine
    original_wait, original_select = engine.WebDriverWait, engine.Select
    engine.WebDriverWait = lambda driver, timeout: _ImmediateWait(driver)
    engine.Select = lambda element: element
    try:
        assert scraper.apply_filter_id("1") is True
    finally:
        engine.WebDriverWait, engine.Select = original_wait, original_select
    assert attempts["n"] == 2, "re-found the control instead of giving up"


class _FakeSelect:
    def select_by_value(self, value):
        self.value = value

    @property
    def first_selected_option(self):
        return FakeAnchor(text="Posted Today")


class _ImmediateWait:
    def __init__(self, driver):
        self.driver = driver

    def until(self, _condition):
        return self.driver.find_element()


def test_a_stale_label_read_does_not_discard_an_applied_filter():
    """From the live run: the criterion was selected, the read-back of the
    option's text went stale, and a working filter was reported as a failure —
    a logging detail invalidating the act it was describing."""
    from selenium.common.exceptions import StaleElementReferenceException

    class StaleLabel:
        def __init__(self):
            self.selected = None

        def select_by_value(self, value):
            self.selected = value

        @property
        def first_selected_option(self):
            raise StaleElementReferenceException("re-rendered by the selection")

    control = StaleLabel()
    scraper = UnisonMarketplaceScraper()
    scraper.driver = type("D", (), {"find_element": lambda *_a, **_k: control})()
    scraper._await_listing_reload = lambda: None
    import app.scrapers.unison.engine.unison_scraper as engine
    original_wait, original_select = engine.WebDriverWait, engine.Select
    engine.WebDriverWait = lambda driver, timeout: _ImmediateWait(driver)
    engine.Select = lambda element: element
    try:
        assert scraper.apply_filter_id("1") is True
    finally:
        engine.WebDriverWait, engine.Select = original_wait, original_select
    assert control.selected == "1"


# -- the diagnostic counter stream --------------------------------------------


def test_the_walk_reports_every_page_as_it_goes(caplog):
    """The stream is the point: a truncation should be visible as it happens,
    not inferred from a short spreadsheet hours later."""
    caplog.set_level(logging.INFO)
    _scraper(FakeListing(total=136)).collect_listing()
    log = [r.getMessage() for r in caplog.records]

    assert "[SEARCH EXECUTED]: Total 136 Bids Detected across Pages." in log
    assert "[PAGE 1]: Extracting rows 1 to 100..." in log
    assert " └── [PAGE 1 SUCCESS]: 100/100 processed. Running total 100 of 136." in log
    assert "[PAGINATING]: Navigating to Page 2..." in log
    assert "[PAGE 2]: Extracting rows 101 to 136..." in log
    assert " └── [PAGE 2 SUCCESS]: 36/36 processed. Running total 136 of 136." in log
    assert any("[LISTING COMPLETE]" in line and "100% Coverage" in line for line in log)


def test_a_page_that_comes_up_short_says_so_on_its_own_line(caplog):
    """SUCCESS is claimed against the row span the portal itself stated, so a
    page that yields fewer rows than it advertised is named at that page rather
    than absorbed into the total."""
    caplog.set_level(logging.INFO)
    listing = FakeListing(total=136)
    scraper = _scraper(listing)
    scraper.extract_request_data = lambda: listing.rows_here()[:90]   # 10 lost
    scraper.collect_listing()

    assert any("[PAGE 1 SHORT]: 90/100 processed." in r.getMessage() for r in caplog.records)


def test_coverage_is_stated_when_the_walk_falls_short(caplog):
    caplog.set_level(logging.ERROR)
    listing = FakeListing(total=115, next_link=False)
    scraper = _scraper(listing)
    scraper.page_url_for = lambda n: None
    scraper.collect_listing()

    assert any("87% coverage" in r.getMessage() for r in caplog.records)


def test_the_console_preview_is_not_a_hundred_row_cap():
    """The slice that mirrored records into the run state stopped at 100, so a
    136-buy run showed 100 rows in the console — indistinguishable from the
    truncation bug it sat beside."""
    from app.scrapers.unison import runner

    assert runner.LIVE_PREVIEW_CEILING >= 1000
