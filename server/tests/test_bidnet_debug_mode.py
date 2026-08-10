"""The testing-phase switches: headed mode, debug pacing, date bypass.

These are temporary by design, so what they are tested for is that turning them
back off is genuinely one line each and leaves no residue — a debug switch that
quietly changes production behaviour after being "turned off" is worse than none.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_debug_mode.py
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import scraper as bidnet  # noqa: E402
from app.scrapers.bidnet.filters import DateFilter, SidebarFilterRequest  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper  # noqa: E402

RANGE = DateFilter(type="RANGE", range_start="08/04/2026", range_end="08/10/2026")


@pytest.fixture
def scraper(tmp_path):
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    return BidnetScraper(run["run_id"], ["gasket"], None, "X")


# -- the flag is the only thing to flip --------------------------------------


def test_headed_mode_logs_the_step_and_paces_it(scraper, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(bidnet, "HEADLESS_MODE", False)
    slept: list[float] = []
    monkeypatch.setattr(bidnet, "DEBUG_PAUSE_SECONDS", 1.5)
    monkeypatch.setattr(bidnet.time, "sleep", slept.append)

    scraper.live_debug('Applying keyword "%s"...', "GASKET")

    assert any('[LIVE DEBUG]: Applying keyword "GASKET"...' in r.getMessage() for r in caplog.records)
    assert slept == [1.5], "a watched step pauses long enough to be seen"


def test_production_mode_neither_logs_nor_pauses(scraper, caplog, monkeypatch):
    """The whole point of the single flag: with it back on there is no residue —
    no debug lines in the log and no sleeps in the flow."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(bidnet, "HEADLESS_MODE", True)
    slept: list[float] = []
    monkeypatch.setattr(bidnet.time, "sleep", slept.append)

    scraper.live_debug("Opening BidNet Direct login page...")

    assert not any("LIVE DEBUG" in r.getMessage() for r in caplog.records)
    assert slept == []


def test_the_browser_is_forced_visible_only_while_headed(scraper, monkeypatch):
    """Headless None means "let the run's own Live preview flag decide", which is
    the production path; False forces a visible window for every run."""
    calls: list[dict] = []
    monkeypatch.setattr(scraper, "start_driver", lambda **kw: calls.append(kw))
    monkeypatch.setattr(scraper, "login", _stop)
    monkeypatch.setattr(scraper, "_save_run_row", lambda: None)
    monkeypatch.setattr(scraper, "cleanup", lambda: None)
    monkeypatch.setattr(bidnet.run_manager, "remove_empty_folder", lambda run_id: None)

    monkeypatch.setattr(bidnet, "HEADLESS_MODE", False)
    scraper.run()
    assert calls == [{"headless": False}]

    calls.clear()
    monkeypatch.setattr(bidnet, "HEADLESS_MODE", True)
    scraper.run()
    assert calls == [{"headless": None}], "production defers to the per-run flag"


def _stop(*_args, **_kwargs):
    from app.core.base_scraper import StopRequested

    raise StopRequested("enough — the driver call is what this test is about")


# -- a maximised window ------------------------------------------------------


def test_a_visible_window_is_maximised_and_a_hidden_one_is_not():
    """The sidebar's date pickers sit below the fold at a fixed 1920x1080 once
    browser chrome takes its slice, which is exactly what needs watching."""
    from app.core.base_scraper import BaseScraper

    source = BaseScraper.start_driver.__doc__ or ""
    assert source is not None
    import inspect

    body = inspect.getsource(BaseScraper.start_driver)
    assert "--start-maximized" in body
    # …and it must be on the visible branch, not applied unconditionally.
    maximised = body.index("--start-maximized")
    fixed_size = body.index("--window-size=1920,1080")
    assert fixed_size < maximised, "fixed size is the headless branch, maximised the visible one"


# -- the date bypass ---------------------------------------------------------


def test_bypassing_dates_strips_them_before_the_sidebar_sees_them(scraper, monkeypatch, caplog):
    """The bypass has to remove the panels from the request, not merely skip
    reading them back — the failing run's dates were reaching the portal
    half-applied, and a bypass that still touched the controls would prove
    nothing about whether they are the cause."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", False)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)

    seen: list[SidebarFilterRequest] = []
    monkeypatch.setattr(scraper, "_visible_row_count", lambda: 0)
    monkeypatch.setattr(scraper, "result_count", lambda: 0)
    monkeypatch.setattr(bidnet, "SidebarDriver", _recorder(seen))

    scraper.apply_sidebar_filters()

    assert seen and seen[0].dates() == [], "no date panel reaches the sidebar"
    assert seen[0].status == "OPEN", "the rest of the request is untouched"
    assert any("were NOT applied" in r.getMessage() for r in caplog.records)


def test_the_bypass_is_recorded_on_the_run_so_the_counts_are_not_misread(scraper, monkeypatch):
    """A run whose results are wider than requested must say so — otherwise its
    numbers get compared against a date-filtered expectation."""
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", False)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)
    monkeypatch.setattr(scraper, "_visible_row_count", lambda: 0)
    monkeypatch.setattr(scraper, "result_count", lambda: 0)
    monkeypatch.setattr(bidnet, "SidebarDriver", _recorder([]))

    scraper.apply_sidebar_filters()

    warnings = run_manager.get_run(scraper.run_id)["warnings"]
    assert any("bypassed for testing" in w for w in warnings)
    assert any("08/04/2026" in w for w in warnings), "name the window that was dropped"


def test_dates_still_reach_the_sidebar_when_the_bypass_is_off(scraper, monkeypatch):
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", True)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)

    seen: list[SidebarFilterRequest] = []
    monkeypatch.setattr(scraper, "_visible_row_count", lambda: 0)
    monkeypatch.setattr(scraper, "result_count", lambda: 0)
    monkeypatch.setattr(bidnet, "SidebarDriver", _recorder(seen))

    scraper.apply_sidebar_filters()

    assert seen and [n for n, _ in seen[0].dates()] == ["published_date"]


def _recorder(seen):
    class FakeSidebarDriver:
        def __init__(self, _driver, note=None, debug=None):
            self.debug = debug

        def apply(self, request):
            seen.append(request)
            return {"status": request.status, "sections": {}, "dates": []}

    return FakeSidebarDriver


# -- a working filter is not a run error -------------------------------------


def test_a_verified_date_filter_emptying_a_keyword_is_not_an_error(scraper, monkeypatch, caplog):
    """Once the date panel reads its own window back off the page, a keyword
    whose bids all fall outside it is the filter working. Logging that as a run
    error is how a real failure gets lost among a dozen expected ones."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", True)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)
    monkeypatch.setattr(scraper, "_visible_row_count", _counts([7, 0]))
    monkeypatch.setattr(scraper, "result_count", _counts([7, 0]))
    monkeypatch.setattr(bidnet, "SidebarDriver", _applied_dates())

    scraper.apply_sidebar_filters()

    assert scraper._dates_verified is True
    assert run_manager.get_run(scraper.run_id)["errors"] == []
    assert any("fall outside" in r.getMessage() for r in caplog.records)


def test_an_unverified_filter_emptying_a_keyword_is_still_an_error(scraper, monkeypatch):
    """Without a verified date panel the emptiness is unexplained, which is
    exactly the case the error exists for."""
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", True)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)
    monkeypatch.setattr(scraper, "_visible_row_count", _counts([7, 0]))
    monkeypatch.setattr(scraper, "result_count", _counts([7, 0]))
    monkeypatch.setattr(bidnet, "SidebarDriver", _applied_dates(dates=[]))

    scraper.apply_sidebar_filters()

    assert scraper._dates_verified is False
    errors = run_manager.get_run(scraper.run_id)["errors"]
    assert any("removed every result" in e for e in errors)


def _counts(values):
    remaining = list(values)
    return lambda: remaining.pop(0) if remaining else 0


def _applied_dates(dates=("published_date:RANGE",)):
    class FakeSidebarDriver:
        def __init__(self, _driver, note=None, debug=None):
            pass

        def apply(self, request):
            return {"status": request.status, "sections": {}, "dates": list(dates)}

    return FakeSidebarDriver


# -- the date filter must reach every keyword, not just the first -------------


def test_date_application_is_tallied_per_keyword(scraper, monkeypatch):
    """The sidebar is re-driven for every keyword, so "the filter applied" is 22
    separate facts. The run record used to carry only the first of them, so a
    panel that applied on keyword 1 and missed on keyword 9 read as a clean run
    while letting out-of-window bids into the export."""
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", True)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)
    monkeypatch.setattr(scraper, "_visible_row_count", lambda: 3)
    monkeypatch.setattr(scraper, "result_count", lambda: 3)

    # Applies for the first keyword, silently misses for the second.
    monkeypatch.setattr(bidnet, "SidebarDriver", _flaky_dates([True, False]))
    scraper.apply_sidebar_filters("gasket")
    scraper.apply_sidebar_filters("valve")

    assert scraper._dates_applied_for == ["gasket"]
    assert scraper._dates_missed_for == ["valve"]


def test_a_keyword_the_filter_missed_is_a_run_error(scraper, monkeypatch):
    """Because its bids are in the export unfiltered by date."""
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", True)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)
    scraper._dates_applied_for = ["gasket"]
    scraper._dates_missed_for = ["valve", "hose"]

    _finish_run(scraper, monkeypatch)

    errors = run_manager.get_run(scraper.run_id)["errors"]
    assert any("did not apply to 2 of 3" in e for e in errors)
    assert any("valve, hose" in e for e in errors)
    assert any("outside" in e for e in errors)


def test_a_filter_that_reached_every_keyword_raises_nothing(scraper, monkeypatch):
    monkeypatch.setattr(bidnet, "APPLY_DATE_FILTERS", True)
    scraper.filters = SidebarFilterRequest(status="OPEN", published_date=RANGE)
    scraper._dates_applied_for = ["gasket", "valve"]
    scraper._dates_missed_for = []

    _finish_run(scraper, monkeypatch)

    run = run_manager.get_run(scraper.run_id)
    assert run["errors"] == []
    assert run["dates_applied_keywords"] == ["gasket", "valve"]


def _finish_run(scraper, monkeypatch):
    """Drive run() past phase 1 with no keywords, so the reconciliation runs."""
    scraper.keywords = []
    for name, value in {
        "start_driver": lambda *a, **k: None,
        "login": lambda: None,
        "_write_master_excel": lambda records: None,
        "_save_run_row": lambda: None,
        "cleanup": lambda: None,
    }.items():
        monkeypatch.setattr(scraper, name, value)
    monkeypatch.setattr(bidnet.export, "save_bids", lambda run, records: 0)
    monkeypatch.setattr(bidnet, "archive_run", lambda run_id: None)
    monkeypatch.setattr(bidnet, "notify_scrape_completion", lambda *a, **k: None)
    monkeypatch.setattr(bidnet.run_manager, "remove_empty_folder", lambda run_id: None)
    scraper.run()


def _flaky_dates(outcomes):
    remaining = list(outcomes)

    class FakeSidebarDriver:
        def __init__(self, _driver, note=None, debug=None):
            pass

        def apply(self, request):
            applied = remaining.pop(0) if remaining else False
            return {
                "status": request.status,
                "sections": {},
                "dates": ["published_date:RANGE"] if applied else [],
            }

    return FakeSidebarDriver
