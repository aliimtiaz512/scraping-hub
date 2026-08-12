"""The Publish Date filter is applied once per run, then kept.

The old flow re-drove the whole sidebar after every keyword search, because
every keyword began with a page reload and a reload clears the panels. That is
four postbacks per keyword on a slow portal, and — worse — a window in which a
keyword's results existed *before* its date filter had landed.

The filters belong to the search form, not to a keyword's results: applied once
they ride along with every later search. So the run applies them before the
first keyword and then only *checks* them, per keyword, read-only. These tests
hold both halves of that: that it is applied exactly once in a healthy run, and
that a session which loses it is caught and repaired rather than quietly
exporting unfiltered bids.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_session_filters.py
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import scraper as bidnet  # noqa: E402
from app.scrapers.bidnet.filters import DateFilter, SidebarFilterRequest  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper, LinkHarvest  # noqa: E402
from app.scrapers.bidnet.sidebar import SidebarDriver  # noqa: E402

RANGE = DateFilter(type="RANGE", range_start="08/04/2026", range_end="08/10/2026")
FILTERS = SidebarFilterRequest(status="OPEN", published_date=RANGE)


# =============================================================================
# The read-only check: SidebarDriver.state_intact
# =============================================================================


class FakeSidebarPage:
    """Answers the three reads `state_intact` makes, and records every script it
    is asked to run so a test can prove nothing was clicked or written."""

    def __init__(self, status="OPEN", date_state=None, fields=None):
        self.status = status
        self.date_state = date_state if date_state is not None else {
            "mode_checked": True,
            "range_start": "08/04/2026",
            "range_end": "08/10/2026",
            "day": "",
            "within": "",
        }
        self.fields = fields or {}
        self.scripts: list[str] = []

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if ":checked" in script:
            return self.status
        if "mode_checked" in script:
            return self.date_state
        if "arguments[0].forEach" in script:          # the hidden-field read
            return {name: self.fields.get(name) for name in args[0]}
        raise AssertionError(f"state_intact ran an unexpected script: {script[:80]}")


def test_a_page_still_holding_the_window_reports_intact():
    page = FakeSidebarPage()
    intact, drifted = SidebarDriver(page).state_intact(FILTERS)

    assert (intact, drifted) == (True, [])


def test_checking_the_filters_touches_nothing():
    """The whole economy of applying once depends on the check being free — a
    check that clicked Apply would be the per-keyword re-drive under a new name,
    and would re-open the window this change exists to close."""
    page = FakeSidebarPage()
    SidebarDriver(page).state_intact(FILTERS)

    ran = " ".join(page.scripts)
    assert ".click()" not in ran
    assert "requestSubmit" not in ran
    assert ".value =" not in ran


def test_a_window_the_page_dropped_is_reported_with_its_panel_named():
    """The failure this guards: a search that silently lost the date filter and
    returned every bid the keyword matched, at any publish date."""
    page = FakeSidebarPage(date_state={
        "mode_checked": True, "range_start": "", "range_end": "",
        "day": "", "within": "",
    })
    intact, drifted = SidebarDriver(page).state_intact(FILTERS)

    assert intact is False
    assert any("published_date" in d for d in drifted)


def test_an_unticked_mode_checkbox_counts_as_drift():
    page = FakeSidebarPage(date_state={
        "mode_checked": False, "range_start": "08/04/2026",
        "range_end": "08/10/2026", "day": "", "within": "",
    })
    intact, drifted = SidebarDriver(page).state_intact(FILTERS)

    assert intact is False
    assert any("RANGE" in d for d in drifted)


def test_the_iso_twin_is_accepted_as_the_same_day():
    """The visible field shows 08/04/2026 and the posted twin holds 2026-08-04.
    Comparing them as text would report a correctly applied window as drifted on
    every keyword, and re-apply the sidebar 22 times for nothing."""
    page = FakeSidebarPage(date_state={
        "mode_checked": True, "range_start": "2026-08-04",
        "range_end": "2026-08-10", "day": "", "within": "",
    })

    assert SidebarDriver(page).state_intact(FILTERS)[0] is True


def test_a_status_radio_that_moved_is_drift():
    page = FakeSidebarPage(status="CLOSED")
    intact, drifted = SidebarDriver(page).state_intact(FILTERS)

    assert intact is False
    assert any("status" in d for d in drifted)


def test_a_list_panel_that_lost_its_selection_is_drift():
    request = SidebarFilterRequest(status="OPEN", locations=["43", "49"])
    page = FakeSidebarPage(fields={"regionId": "43"})     # 49 gone
    intact, drifted = SidebarDriver(page).state_intact(request)

    assert intact is False
    assert any("Location" in d for d in drifted)


# =============================================================================
# The run: applied once, checked per keyword
# =============================================================================


KEYWORDS = ["gasket", "valve", "hose"]


@pytest.fixture
def driven(monkeypatch, tmp_path):
    """A run with the browser replaced but `run()`'s own loop intact, recording
    the order of everything that touches the portal."""
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    instance = BidnetScraper(run["run_id"], list(KEYWORDS), FILTERS, "X")
    trace: list[str] = []

    def record(name, result=None, *, args=False):
        def call(*a, **k):
            # Recorded as "name:term" so the empty bootstrap search reads as
            # "search:" and is distinguishable from a keyword's own.
            trace.append(f"{name}:{a[0] if a else ''}" if args else name)
            return result() if callable(result) else result
        return call

    for name, value in {
        "start_driver": record("start_driver"),
        "login": record("login"),
        "reset_search_state": record("reset"),
        "ensure_logged_in": record("ensure_logged_in"),
        "search": record("search", args=True),
        "_ensure_first_result_page": record("page_1", True),
        "result_count": record("count", 2),
        "filter_member_agency": record("group"),
        "apply_sidebar_filters": record("APPLY", {"dates": ["published_date:RANGE"]}, args=True),
        "collect_links": record(
            "collect", lambda: LinkHarvest(links=["https://b/1"], rows_detected=1, rows_parsed=1)
        ),
        "process_bid": lambda link, folder: {"reference_number": "1", "title": link, "documents": []},
        "_write_master_excel": lambda records: None,
        "_save_run_row": lambda: None,
        "cleanup": lambda: None,
    }.items():
        monkeypatch.setattr(instance, name, value)

    monkeypatch.setattr(bidnet.export, "save_bids", lambda run, records: len(records))
    monkeypatch.setattr(bidnet, "archive_run", lambda run_id: None)
    monkeypatch.setattr(bidnet, "notify_scrape_completion", lambda *a, **k: None)
    monkeypatch.setattr(bidnet.run_manager, "remove_empty_folder", lambda run_id: None)
    return instance, trace


def _intact(monkeypatch, outcomes=None):
    """Stand in for the sidebar's read-only check. `outcomes` is consumed one
    keyword at a time; the default is a session that holds its filters."""
    remaining = list(outcomes or [])

    class FakeSidebarDriver:
        def __init__(self, _driver, note=None, debug=None):
            pass

        def state_intact(self, _request):
            ok = remaining.pop(0) if remaining else True
            return (True, []) if ok else (False, ["published_date: range_start is ''"])

    monkeypatch.setattr(bidnet, "SidebarDriver", FakeSidebarDriver)


def test_the_filters_are_applied_once_for_the_whole_run(driven, monkeypatch):
    """Three keywords, one application. This is the change: the sidebar used to
    be driven once per keyword, each time a full set of postbacks."""
    instance, trace = driven
    _intact(monkeypatch)
    instance.run()

    assert trace.count("APPLY:") == 1, f"applied {trace.count('APPLY:')} times: {trace}"
    assert "APPLY:gasket" not in trace, "no keyword re-applies a filter that is still active"


def test_the_filters_are_applied_before_the_first_keyword_is_searched(driven, monkeypatch):
    """Order is the point: a keyword searched before its date window landed
    returns bids the window would have excluded, and nothing downstream can tell
    them from bids it allowed."""
    instance, trace = driven
    _intact(monkeypatch)
    instance.run()

    assert trace.index("APPLY:") < trace.index("search:gasket")


def test_no_keyword_reloads_the_page_over_the_filters(driven, monkeypatch):
    """A reload is what clears the sidebar, so the per-keyword reload had to go.
    One remains — the session's own setup, before anything is filtered."""
    instance, trace = driven
    _intact(monkeypatch)
    instance.run()

    assert trace.count("reset") == 1
    assert trace.index("reset") < trace.index("APPLY:")


def test_each_keyword_is_typed_into_the_box_and_searched(driven, monkeypatch):
    """The bootstrap search (empty keyword) opens the page the filters go on;
    each keyword then replaces the term in the box, filters still in force."""
    instance, trace = driven
    _intact(monkeypatch)
    instance.run()

    assert [t for t in trace if t.startswith("search")] == [
        "search:", "search:gasket", "search:valve", "search:hose",
    ]


def test_a_keyword_whose_filters_survived_is_tallied_as_date_filtered(driven, monkeypatch):
    """The per-keyword date tally still exists — it is now fed by reading the
    panels back rather than by re-driving them."""
    instance, _ = driven
    _intact(monkeypatch)
    instance.run()

    assert instance._dates_applied_for == KEYWORDS
    assert instance._dates_missed_for == []
    assert run_manager.get_run(instance.run_id)["dates_applied_keywords"] == KEYWORDS


def test_a_session_that_loses_the_filter_re_applies_it_for_that_keyword(driven, monkeypatch, caplog):
    """"The filters persist across a search" is a claim about BidNet's form, not
    something the run may assume. When the check says otherwise, that keyword
    pays for a re-apply rather than exporting unfiltered bids."""
    caplog.set_level(logging.WARNING)
    instance, trace = driven
    _intact(monkeypatch, outcomes=[True, False, True])   # "valve" lost them
    instance.run()

    assert "APPLY:valve" in trace
    assert instance._filters_reapplied_for == ["valve"]
    assert run_manager.get_run(instance.run_id)["filters_reapplied_keywords"] == ["valve"]
    assert any("did not survive this search" in r.getMessage() for r in caplog.records)


def test_a_keyword_that_failed_mid_page_makes_the_next_one_re_filter(driven, monkeypatch):
    """Where a WebDriver failure left the browser is unknown, so the filters are
    treated as lost — the alternative is the next keyword searching a page whose
    state nobody checked."""
    from selenium.common.exceptions import WebDriverException

    instance, trace = driven
    _intact(monkeypatch)

    def collect():
        trace.append("collect")
        if trace.count("collect") == 1:
            raise WebDriverException("the detail page went away")
        return LinkHarvest(links=["https://b/1"], rows_detected=1, rows_parsed=1)

    monkeypatch.setattr(instance, "collect_links", collect)
    monkeypatch.setattr(instance, "screenshot", lambda name: None)
    instance.run()

    # The session is re-established once, between the failed keyword and the next.
    assert trace.count("APPLY:") == 2
    assert trace.index("APPLY:", trace.index("collect") + 1) < trace.index("search:valve")


def test_signing_in_again_mid_run_marks_the_filters_lost(monkeypatch, tmp_path):
    """A re-login lands on the dashboard, which has no sidebar at all. Nothing
    but the flag stops the next keyword from trusting a page that was never
    filtered."""
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    instance = BidnetScraper(run["run_id"], list(KEYWORDS), FILTERS, "X")
    instance._filters_live = True

    # Far enough into the real `login` to see the flag cleared, and no further:
    # everything after it needs a browser.
    monkeypatch.setattr(instance, "navigate", _boom)
    with pytest.raises(RuntimeError):
        instance.login()

    assert instance._filters_live is False


def _boom(*_args, **_kwargs):
    raise RuntimeError("far enough — the flag is what this test is about")


def test_no_search_terms_means_no_bootstrap_search(driven, monkeypatch):
    """A niche with nothing to search should not open a results page to filter."""
    instance, trace = driven
    instance.search_terms = []
    _intact(monkeypatch)
    instance.run()

    assert "APPLY:" not in trace
    assert "search:" not in trace


# =============================================================================
# Page 1: the other half of what the per-keyword reload was doing
# =============================================================================


class FakePagedResults:
    """A results page that knows which page it is on and whether the way back
    works."""

    def __init__(self, page, back_works=True):
        self.page = page
        self.back_works = back_works
        self.clicked = 0

    def execute_script(self, script, *args):
        if "parseInt" in script and "bar" in script:
            return self.page
        self.clicked += 1                       # the click on the First link
        if self.back_works:
            self.page = 1
        return None

    def find_elements(self, by, value):
        return [object()]


def _paged(monkeypatch, tmp_path, driver):
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    instance = BidnetScraper(run["run_id"], list(KEYWORDS), FILTERS, "X")
    instance.driver = driver
    monkeypatch.setattr(instance, "_await_ajax_idle", lambda timeout=None: True)
    return instance


def test_results_already_on_page_1_are_left_alone(monkeypatch, tmp_path):
    driver = FakePagedResults(page=1)
    assert _paged(monkeypatch, tmp_path, driver)._ensure_first_result_page() is True
    assert driver.clicked == 0


def test_results_left_on_a_later_page_are_taken_back_to_the_first(monkeypatch, tmp_path):
    """`collect_links` walks to the last page of every keyword, and the next
    keyword is now searched from there instead of from a fresh page. Harvesting
    from page 7 would lose pages 1-6 and report a plausible smaller number."""
    driver = FakePagedResults(page=7)
    assert _paged(monkeypatch, tmp_path, driver)._ensure_first_result_page() is True
    assert driver.clicked == 1
    assert driver.page == 1


def test_a_page_that_will_not_go_back_asks_for_a_reload(monkeypatch, tmp_path):
    """Reported as False rather than harvested anyway — the run then reloads and
    re-applies the filters, paying the old per-keyword cost only where it must."""
    driver = FakePagedResults(page=7, back_works=False)
    assert _paged(monkeypatch, tmp_path, driver)._ensure_first_result_page() is False
