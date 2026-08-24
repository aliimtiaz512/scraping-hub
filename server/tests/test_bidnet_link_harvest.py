"""BidNet row parsing: a row that cannot be read must be reported, not dropped.

The bug these exist for: a run logged hits for 6-8 of a niche's 22 keywords and
exported 0 bids. `collect_links` matched one selector —
`tr.mets-table-row a.solicitationsTitleLink` — and any row that did not carry it
fell out of a bare `if href:` with nothing written anywhere. Rows were never
counted separately from links, so "22 rows, 0 parseable" and "0 rows" produced
the same log line and the same empty spreadsheet.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_link_harvest.py
"""

import logging
import os
import sys

import pytest
from selenium.common.exceptions import NoSuchElementException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet.scraper import (  # noqa: E402
    ROW_LINK_SELECTORS,
    BidnetScraper,
    LinkHarvest,
)

PRIMARY = ROW_LINK_SELECTORS[0]
FALLBACK = ROW_LINK_SELECTORS[1]


class FakeRow:
    """A results row that answers only the selectors it actually carries."""

    def __init__(self, links: dict[str, str], text: str = "GASKET, 1 inch"):
        self.links = links
        self.text = text


class FakeDriver:
    """Stands in for the page, answering the harvest's single JS pass.

    Models `_JS_READ_ROWS` rather than element-by-element traversal, because
    that is what the scraper now does: one pass cannot go stale mid-iteration,
    which is the whole point of the change it is testing.
    """

    def __init__(self, rows, raises: Exception | None = None):
        self.rows = rows
        self.raises = raises
        self.script_calls = 0

    def execute_script(self, _script, _row_selector, link_selectors):
        self.script_calls += 1
        if self.raises is not None:
            raising, self.raises = self.raises, None   # the retry succeeds
            raise raising
        out = []
        for index, row in enumerate(self.rows, start=1):
            href = selector = None
            for candidate in link_selectors:
                if row.links.get(candidate):
                    href, selector = row.links[candidate], candidate
                    break
            out.append({
                "index": index,
                "href": href,
                "selector": selector,
                "text": "" if href else row.text,
            })
        return out


@pytest.fixture
def scraper(tmp_path):
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    return BidnetScraper(run["run_id"], ["gasket"], None, "X")


def _harvest(scraper, rows) -> LinkHarvest:
    scraper.driver = FakeDriver(rows)
    harvest = LinkHarvest()
    scraper._harvest_page(harvest, page_num=1)
    return harvest


def test_ordinary_rows_are_all_parsed(scraper):
    rows = [FakeRow({PRIMARY: f"https://bidnetdirect.com/bid/{n}"}) for n in range(12)]
    harvest = _harvest(scraper, rows)
    assert harvest.rows_detected == 12
    assert harvest.rows_parsed == 12
    assert harvest.rows_failed == 0
    assert len(harvest.links) == 12


def test_a_renamed_title_class_falls_back_instead_of_dropping_the_row(scraper, caplog):
    """The failure mode itself: the portal re-skins the title cell, the primary
    selector matches nothing, and every row is silently lost."""
    caplog.set_level(logging.WARNING)
    rows = [
        FakeRow({FALLBACK: f"https://bidnetdirect.com/private/supplier/interception/view-notice/{n}"})
        for n in range(8)
    ]
    harvest = _harvest(scraper, rows)

    assert harvest.rows_parsed == 8, "rows must survive a renamed primary selector"
    assert len(harvest.links) == 8
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("fallback selector" in m for m in warnings)
    assert sum("fallback selector" in m for m in warnings) == 1, "reported once per run"


def test_one_unreadable_row_does_not_cost_the_others(scraper, caplog):
    """The rest of the page must still be collected — a single bad row used to
    be indistinguishable from a page that ended there."""
    caplog.set_level(logging.WARNING)
    rows = [
        FakeRow({PRIMARY: "https://bidnetdirect.com/bid/1"}),
        FakeRow({}, text="Gasket kit, no link cell"),
        FakeRow({PRIMARY: "https://bidnetdirect.com/bid/3"}),
    ]
    harvest = _harvest(scraper, rows)

    assert harvest.rows_detected == 3
    assert harvest.rows_parsed == 2
    assert harvest.rows_failed == 1
    assert len(harvest.links) == 2


def test_an_unreadable_row_names_its_index_and_selectors(scraper, caplog):
    caplog.set_level(logging.WARNING)
    _harvest(scraper, [FakeRow({}, text="Gasket kit, no link cell")])

    message = "\n".join(r.getMessage() for r in caplog.records)
    assert "row 1" in message
    assert PRIMARY in message, "the failed selector must be named"
    assert "Gasket kit" in message, "the row's text gives it an identity"


def test_an_empty_href_is_a_failure_not_a_link(scraper):
    harvest = _harvest(scraper, [FakeRow({PRIMARY: ""})])
    assert harvest.rows_parsed == 0
    assert harvest.rows_failed == 1


def test_a_repeated_row_is_counted_as_a_duplicate_not_a_loss(scraper):
    """A row the portal repeats across pages is parsed fine — it just isn't new.
    Counted apart from failures so a duplicate never reads as a parse error."""
    scraper.driver = FakeDriver([FakeRow({PRIMARY: "https://bidnetdirect.com/bid/1"})])
    harvest = LinkHarvest()
    scraper._harvest_page(harvest, page_num=1)
    scraper._harvest_page(harvest, page_num=2)

    assert harvest.rows_detected == 2
    assert harvest.rows_parsed == 2
    assert harvest.rows_failed == 0
    assert harvest.duplicates == 1
    assert len(harvest.links) == 1


def test_rows_dropped_covers_both_kinds(scraper):
    harvest = LinkHarvest(rows_detected=5, rows_parsed=3, rows_failed=2, duplicates=1)
    assert harvest.rows_dropped == 3


def test_reading_the_first_row_uses_the_same_fallbacks(scraper):
    """It must not disagree with the harvest about what a row's link is."""
    scraper.driver = FakeDriver(
        [FakeRow({FALLBACK: "https://bidnetdirect.com/private/supplier/interception/view-notice/1"})]
    )
    assert scraper._first_row_link() == (
        "https://bidnetdirect.com/private/supplier/interception/view-notice/1"
    )


def test_the_first_row_is_none_when_there_are_no_rows(scraper):
    scraper.driver = FakeDriver([])
    assert scraper._first_row_link() is None


# -- the sidebar's one destructive state -------------------------------------


def test_an_empty_purchasing_group_does_not_zero_the_search(caplog):
    """BidNet ships Purchasing Group fully ticked and reads "no group" as "no
    results" — so an empty selection returned an empty field, verified clean,
    and every keyword exported nothing. It must mean "leave it alone" instead."""
    import logging as _logging

    from app.scrapers.bidnet.filters import SECTIONS, SidebarFilterRequest

    caplog.set_level(_logging.WARNING)
    section = next(s for s in SECTIONS if s.name == "purchasing_groups")
    assert section.default_all, "guard: this test is about the default-all panel"

    request = SidebarFilterRequest(purchasing_groups=[])
    assert request.selection_for(section) is None, "must not be written as an empty field"
    assert any("zero results" in r.getMessage() for r in caplog.records)


def test_an_explicit_purchasing_group_still_narrows():
    from app.scrapers.bidnet.filters import SECTIONS, SidebarFilterRequest

    section = next(s for s in SECTIONS if s.name == "purchasing_groups")
    request = SidebarFilterRequest(purchasing_groups=["123", "456"])
    assert request.selection_for(section) == ["123", "456"]


# -- the two href shapes, measured on a live results page ---------------------


def test_the_fallback_covers_both_solicitation_link_shapes():
    """Probed on a live 25-row results page: solicitation links come in two href
    shapes, both carrying `class="solicitationsTitleLink mets-command-link"` —

        /private/supplier/interception/view-notice/444124954092        (19 of 25)
        /private/supplier/interception/open-solicitation/9490210669?target=view (6)

    A fallback keyed on `view-notice` alone drops the other six, a quarter of the
    page, counted as unparseable rows. `/interception/` is what they share."""
    import re

    fallback = ROW_LINK_SELECTORS[1]
    pattern = re.search(r"href\*='([^']+)'", fallback)
    assert pattern, f"expected an href-contains fallback, got {fallback!r}"
    needle = pattern.group(1)

    for href in (
        "/private/supplier/interception/view-notice/444124954092",
        "/private/supplier/interception/open-solicitation/9490210669?target=view",
    ):
        assert needle in href, f"{fallback!r} would drop {href}"


def test_both_link_shapes_are_harvested_from_the_same_page(scraper):
    """The mixed page the probe found, end to end."""
    base = "https://www.bidnetdirect.com/private/supplier/interception"
    rows = [FakeRow({PRIMARY: f"{base}/view-notice/{n}"}) for n in range(19)]
    rows += [FakeRow({PRIMARY: f"{base}/open-solicitation/{n}?target=view"}) for n in range(6)]

    harvest = _harvest(scraper, rows)
    assert harvest.rows_detected == 25
    assert harvest.rows_parsed == 25
    assert len(harvest.links) == 25


def test_open_solicitation_rows_survive_a_renamed_primary_class(scraper):
    """The exact case the old chain lost: primary class gone, and the row is an
    `open-solicitation` link rather than a `view-notice` one."""
    href = (
        "https://www.bidnetdirect.com/private/supplier/interception/"
        "open-solicitation/9490210669?target=view"
    )
    harvest = _harvest(scraper, [FakeRow({FALLBACK: href})])
    assert harvest.rows_parsed == 1
    assert harvest.links == [href]


# -- stale elements -----------------------------------------------------------


def test_a_re_render_mid_read_retries_instead_of_losing_the_page(scraper, monkeypatch):
    """The loophole this replaced: the harvest walked live element handles, so a
    lazy re-render partway through detached every row still to be visited. Each
    one then raised StaleElementReferenceException — a WebDriverException, so
    they were counted as *unparseable*. Silent under-collection reported as a
    parsing failure, on a page that was merely still settling."""
    from selenium.common.exceptions import StaleElementReferenceException

    rows = [FakeRow({PRIMARY: f"https://b/{n}"}) for n in range(5)]
    scraper.driver = FakeDriver(rows, raises=StaleElementReferenceException("re-rendered"))
    settled: list[int] = []
    monkeypatch.setattr(scraper, "_await_ajax_idle", lambda timeout=0: settled.append(timeout))

    harvest = LinkHarvest()
    scraper._harvest_page(harvest, page_num=1)

    assert settled, "the page must be given time to settle before the retry"
    assert harvest.rows_detected == 5
    assert harvest.rows_parsed == 5, "a stale read must not become 5 unparseable rows"
    assert harvest.rows_failed == 0
    assert scraper.driver.script_calls == 2, "read, settle, read again"


def test_the_whole_page_is_read_in_one_pass(scraper):
    """One round-trip per page, not one per row per selector — 25 rows x 4
    selectors was 100 round-trips through a slow portal, and 100 chances to go
    stale."""
    rows = [FakeRow({PRIMARY: f"https://b/{n}"}) for n in range(25)]
    harvest = _harvest(scraper, rows)

    assert harvest.rows_parsed == 25
    assert scraper.driver.script_calls == 1
