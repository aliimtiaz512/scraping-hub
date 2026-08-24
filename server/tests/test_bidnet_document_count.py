"""Attachments are counted off the page. They are never fetched.

The member agency sweep reports how many documents each solicitation carries —
the triage signal a keyword-less sweep is otherwise short of. What it must not
do is reintroduce downloading by the back door, so these tests pin both halves:
the count is right, and nothing is written to disk getting it.

The counting itself is three steps, cheapest first, because a sweep pays it once
per bid across a couple of thousand of them: read the DOM as it stands, read the
tab's badge, and only open the tab when those two do not already agree.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_document_count.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import scraper as bidnet  # noqa: E402
from app.scrapers.bidnet.models import (  # noqa: E402
    EXCEL_COLUMNS,
    MEMBER_AGENCY_EXCEL_COLUMNS,
)
from app.scrapers.bidnet.scraper import BidnetScraper  # noqa: E402


class FakeDocsPage:
    """A solicitation page whose documents tab renders lazily, as the real one does.

    `hidden` anchors are the ones that only appear once the tab is opened —
    which is the whole reason the tab has to be opened at all.
    """

    def __init__(self, visible=0, hidden=0, badge=None, tab=True):
        self.visible = visible
        self.hidden = hidden
        self.badge = badge
        self.tab = tab
        self.opened = 0

    # -- driver surface -----------------------------------------------------

    def execute_script(self, script, *args):
        if "seen.add" in script:                      # the attachment count
            # Already-deduplicated: the collapsing of hrefs the portal renders
            # twice happens in the browser, in JS this fake stands in for, so it
            # is deliberately NOT covered here — only the flow around it is.
            return self.visible + (self.hidden if self.opened else 0)
        if "getElementById" in script:                # the badge
            return self.badge
        if "scrollIntoView" in script:
            return None
        if ".click()" in script:
            self.opened += 1
            return None
        return None

    def find_elements(self, _by, _selector):
        return [object()] if self.tab else []


@pytest.fixture
def scraper(tmp_path):
    run = run_manager.create_run("bidnet", tmp_path, {"niche_label": "X"})
    instance = BidnetScraper(run["run_id"], ["kw"], None, "X")
    instance.count_documents = True
    return instance


def _fast(monkeypatch):
    """Collapse the anchor waits so the tests do not sleep."""
    monkeypatch.setattr(bidnet, "DOC_TAB_TIMEOUT", 0)
    monkeypatch.setattr(bidnet, "DOC_ZERO_TIMEOUT", 0)


# -- the count itself -------------------------------------------------------


def test_anchors_already_on_the_page_are_counted_without_opening_the_tab(scraper):
    """The free path. Some solicitations render their attachments outright, and
    a sweep should not pay a tab render for those."""
    scraper.driver = FakeDocsPage(visible=3, badge=3)

    assert scraper._count_documents() == 3
    assert scraper.driver.opened == 0, "the tab was opened needlessly"


def test_the_tab_is_opened_when_the_anchors_are_not_rendered_yet(scraper, monkeypatch):
    _fast(monkeypatch)
    page = FakeDocsPage(visible=0, hidden=4, badge=4)
    scraper.driver = page

    assert scraper._count_documents() == 4
    assert page.opened == 1


def test_a_bid_with_no_attachments_counts_zero(scraper, monkeypatch):
    _fast(monkeypatch)
    scraper.driver = FakeDocsPage(visible=0, hidden=0, badge=0)

    assert scraper._count_documents() == 0


def test_an_unreadable_page_is_none_not_zero(scraper, monkeypatch):
    """The distinction the export depends on: a bid we could not ask about is
    not a bid with no documents, and must not be reported as one."""
    _fast(monkeypatch)
    scraper.driver = FakeDocsPage(visible=0, hidden=0, badge=None, tab=False)

    assert scraper._count_documents() is None


def test_the_anchors_win_over_a_badge_that_disagrees(scraper, monkeypatch):
    """The badge is what the portal claims; the anchors are what it serves.
    Where they differ it has been the badge that was wrong."""
    _fast(monkeypatch)
    scraper.driver = FakeDocsPage(visible=0, hidden=7, badge=2)

    assert scraper._count_documents() == 7


def test_a_badge_standing_alone_is_still_reported(scraper, monkeypatch):
    """Tab opened, nothing countable came back — the portal's own number beats
    reporting a confident zero."""
    _fast(monkeypatch)
    scraper.driver = FakeDocsPage(visible=0, hidden=0, badge=5)

    assert scraper._count_documents() == 5


# -- nothing is downloaded --------------------------------------------------


def test_counting_writes_nothing_to_disk(scraper, monkeypatch, tmp_path):
    _fast(monkeypatch)
    scraper.driver = FakeDocsPage(visible=0, hidden=6, badge=6)
    before = sorted(p.name for p in tmp_path.rglob("*"))

    scraper._count_documents()

    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_downloading_stays_off():
    assert bidnet.DOWNLOAD_DOCUMENTS is False


def test_a_niche_run_does_not_pay_for_counting(tmp_path):
    """Counting costs a tab render per bid. A run whose sheet has no Documents
    column must not be charged for one."""
    run = run_manager.create_run("bidnet", tmp_path, {"niche_label": "X"})
    assert BidnetScraper(run["run_id"], ["kw"], None, "X").count_documents is False


def test_the_sweep_does_pay_for_it(tmp_path):
    from app.scrapers.bidnet.member_agencies import MemberAgencySweepScraper

    run = run_manager.create_run("bidnet", tmp_path, {"member_agency_sweep": True})
    assert MemberAgencySweepScraper(run["run_id"]).count_documents is True


# -- the sweep's column layout ----------------------------------------------


def test_the_sweep_sheet_drops_matched_keyword():
    """A sweep types nothing into the search box, so the column could only ever
    be blank — and a column blank in every row is noise the reader must learn
    to skip."""
    assert "matched_keyword" not in [attr for attr, _ in MEMBER_AGENCY_EXCEL_COLUMNS]
    # …while a niche run, which searches twenty-odd terms one at a time, keeps it.
    assert "matched_keyword" in [attr for attr, _ in EXCEL_COLUMNS]


def test_the_sweep_sheet_ends_documents_niche_status():
    headers = [header for _, header in MEMBER_AGENCY_EXCEL_COLUMNS]
    assert headers[-3:] == ["Documents", "Niche", "Status"]


def test_both_layouts_lead_with_the_bids_own_identity():
    for columns in (EXCEL_COLUMNS, MEMBER_AGENCY_EXCEL_COLUMNS):
        assert columns[0][0] == "reference_number"


def test_the_sweep_run_selects_the_sweep_layout(tmp_path):
    from app.scrapers.bidnet import export

    sweep = run_manager.create_run("bidnet", tmp_path, {"member_agency_sweep": True})
    niche = run_manager.create_run("bidnet", tmp_path, {"niche_label": "X"})

    assert export.columns_for_run(sweep["run_id"]) is MEMBER_AGENCY_EXCEL_COLUMNS
    assert export.columns_for_run(niche["run_id"]) is EXCEL_COLUMNS
    # A run the manager no longer holds falls back to the niche layout.
    assert export.columns_for_run("nonexistent") is EXCEL_COLUMNS


def test_the_written_sheet_matches_the_layout(tmp_path):
    from openpyxl import load_workbook

    from app.scrapers.bidnet import export

    out = tmp_path / "sweep.xlsx"
    export.generate_excel_from_records(
        [{"reference_number": "RFP-1", "documents_count": "4", "niche": "County of X",
          "status": "OK", "matched_keyword": "should not appear"}],
        out,
        MEMBER_AGENCY_EXCEL_COLUMNS,
    )

    sheet = load_workbook(out).active
    headers = [c.value for c in sheet[1]]
    assert headers[-3:] == ["Documents", "Niche", "Status"]
    assert "Matched Keyword" not in headers
    row = {h: c.value for h, c in zip(headers, sheet[2])}
    assert row["Documents"] == "4"
    assert row["Niche"] == "County of X"
