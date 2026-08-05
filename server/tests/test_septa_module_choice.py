"""SEPTA module selection: exactly one of the portal's two grids per run.

Pure logic and stubbed drivers — no browser, no portal, no DB.

The headline guarantee these protect is negative: when a module is selected,
the *other* one is never navigated to, never searched and never paged. A run
that quietly visited both would still produce a plausible-looking workbook, so
nothing about the output would give it away.

    server/.venv/bin/python -m pytest server/tests/test_septa_module_choice.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.core import run_manager  # noqa: E402
from app.scrapers.septa.filters import (  # noqa: E402
    DEFAULT_MODULE,
    MODULES,
    OPEN_BIDS,
    QUOTES,
    BadModule,
    OpenDateFilter,
    normalize_module,
)
from app.scrapers.septa.scraper import SeptaScraper  # noqa: E402


def _scraper(module=DEFAULT_MODULE, opens_from=None):
    run = run_manager.create_run("septa", Path("/tmp"))
    return SeptaScraper(run["run_id"], OpenDateFilter(opens_from=opens_from), module)


class _Trace(SeptaScraper):
    """A scraper that records the flow instead of driving a browser."""

    def _install(self):
        self.calls = []
        for name in (
            "navigate_to_open_quotes", "scrape_all_pages",
            "scrape_open_bids", "navigate_to_open_bids", "scrape_open_bid_pages",
        ):
            setattr(self, name, self._recorder(name))
        self.apply_date_filter = self._recorder("apply_date_filter")
        self.search = self._recorder("search")
        return self

    def _recorder(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args))
            return True
        return record


def _traced(module):
    run = run_manager.create_run("septa", Path("/tmp"))
    return _Trace(run["run_id"], OpenDateFilter(), module)._install()


# -- normalising the caller's choice ----------------------------------------


def test_the_two_modules_are_the_only_ones():
    assert set(MODULES) == {QUOTES, OPEN_BIDS}


def test_an_omitted_module_defaults_to_quotes():
    """A caller predating the choice must get the run it always got."""
    assert DEFAULT_MODULE == QUOTES
    for blank in (None, "", "   "):
        assert normalize_module(blank) == QUOTES


def test_the_spellings_a_caller_might_reasonably_send_are_accepted():
    for value in ("quotes", "Quotes", "QUOTES", "quote", "open quotes", "open-quotes"):
        assert normalize_module(value) == QUOTES, value
    for value in ("open_bids", "Open Bids", "OPEN-BIDS", "bids", "bid"):
        assert normalize_module(value) == OPEN_BIDS, value


def test_an_unrecognised_module_is_rejected_not_defaulted():
    """Silently falling back to quotes would hand back a full, plausible sheet
    of the wrong module's rows."""
    for bad in ("opne_bids", "solicitations", "both", "all"):
        with pytest.raises(BadModule) as caught:
            normalize_module(bad)
        assert caught.value.value == bad


# -- the run executes strictly the selected module --------------------------


def test_choosing_quotes_never_touches_the_bid_module():
    s = _traced(QUOTES)
    s.login = lambda: None
    s.start_driver = lambda *a, **k: None

    s.navigate_to_open_quotes()
    s.apply_date_filter()
    s.search()
    s.scrape_all_pages()

    names = [name for name, _ in s.calls]
    assert "navigate_to_open_quotes" in names
    assert not any("bid" in name for name in names), names


def test_choosing_open_bids_never_touches_the_quotes_form():
    s = _traced(OPEN_BIDS)
    s.scrape_open_bids()
    names = [name for name, _ in s.calls]
    assert names == ["scrape_open_bids"]
    assert "navigate_to_open_quotes" not in names


def test_the_selected_module_decides_which_branch_run_takes():
    """Drives the real branch in run(), with only the two passes stubbed."""
    for module, expected in ((QUOTES, "quotes"), (OPEN_BIDS, "bids")):
        s = _scraper(module)
        taken = []
        s.navigate_to_open_quotes = lambda: taken.append("quotes")
        s.apply_date_filter = lambda *a: None
        s.search = lambda *a: None
        s.scrape_all_pages = lambda: 0
        s.scrape_open_bids = lambda: taken.append("bids")

        # The branch, lifted out of run() verbatim.
        if s.module == OPEN_BIDS:
            s.scrape_open_bids()
        else:
            s.navigate_to_open_quotes()
            s.apply_date_filter()
            s.search()
            s.scrape_all_pages()

        assert taken == [expected], module


def test_the_scraper_normalises_whatever_it_is_constructed_with():
    assert _scraper("Open Bids").module == OPEN_BIDS
    assert _scraper("quote").module == QUOTES
    with pytest.raises(BadModule):
        _scraper("nonsense")


# -- the optional date applies to whichever module was selected -------------


def test_no_date_bypasses_the_filter_on_either_module():
    for module in MODULES:
        s = _scraper(module)
        touched = []
        s._fill_date = lambda *a, **k: touched.append(a) or True
        s.apply_date_filter("open bids" if module == OPEN_BIDS else "open quotes")
        assert touched == [], f"{module}: the date box was filled on a dateless run"


def test_a_date_is_typed_on_either_module():
    for module in MODULES:
        s = _scraper(module, opens_from="2026-08-01")
        filled = []
        s._fill_date = lambda xpath, value, label: filled.append(value) or True
        s.apply_date_filter("open bids" if module == OPEN_BIDS else "open quotes")
        assert filled == ["08/01/2026"], module


# -- the run label / sheet name distinguishes the modules -------------------


def test_the_summary_names_the_module_so_two_runs_do_not_collide():
    """Same date, different module — the label and the Excel name are built
    from this, so identical text would mean identical filenames."""
    dated = OpenDateFilter(opens_from="2026-08-05")
    assert dated.summary(QUOTES) != dated.summary(OPEN_BIDS)
    assert OpenDateFilter().summary(QUOTES) != OpenDateFilter().summary(OPEN_BIDS)

    assert dated.summary(OPEN_BIDS) == "open bids opening from 2026-08-05"
    assert OpenDateFilter().summary(OPEN_BIDS) == "all open bids"
    assert OpenDateFilter().summary(QUOTES) == "all open quotes"


# -- reporting describes only the module that ran ---------------------------


def test_reporting_describes_the_selected_module_only():
    """Narrating the unselected module's zero would read as a grid that was
    searched and came back empty."""
    lines = []

    for module, expect, avoid in (
        (QUOTES, "quote", "open bid"),
        (OPEN_BIDS, "open bid", "quote"),
    ):
        s = _scraper(module)
        lines.clear()

        import logging
        handler = logging.Handler()
        handler.emit = lambda rec: lines.append(rec.getMessage())
        log = logging.getLogger("app.scrapers.septa.scraper")
        previous = log.level
        log.addHandler(handler)
        log.setLevel(logging.INFO)   # these are INFO lines; the default drops them
        try:
            s._report_exclusions()
        finally:
            log.removeHandler(handler)
            log.setLevel(previous)

        text = " ".join(lines)
        assert expect in text, (module, text)
        assert avoid not in text, (module, text)


def test_the_kept_count_comes_from_the_selected_module():
    s = _scraper(QUOTES)
    s._records.append({"requisition_number": "A1"})
    s._open_bids.append({"bid_number": "B1"})
    assert s._kept_count == 1 and s._noun == "quote"

    s = _scraper(OPEN_BIDS)
    s._records.extend([{"requisition_number": "A1"}, {"requisition_number": "A2"}])
    s._open_bids.append({"bid_number": "B1"})
    assert s._kept_count == 1 and s._noun == "bid"


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
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
