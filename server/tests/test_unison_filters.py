"""Unison's filter switchboard (app/scrapers/unison/filters).

All three filters are off for the testing phase. What these pin down is both
halves of that: that nothing narrows a run while they are off, and that flipping
a flag back on restores the behaviour — the point of keeping the code rather
than deleting it.

    server/.venv/bin/python -m pytest server/tests/test_unison_filters.py
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE  # noqa: E402
from app.scrapers.unison import filters  # noqa: E402

# An end date far enough out to survive the close-date rule, and one long past.
FAR = "12/31/2099"
PAST = "01/01/2020"

RECORDS = [
    {"buyer_number": "1", "buyer_description": "Survey of market research", "end_date": PAST},
    {"buyer_number": "2", "buyer_description": "Aircraft parts", "end_date": FAR},
    {"buyer_number": "3", "buyer_description": "Meal service", "end_date": "not a date"},
]


def close_dates(records):
    return [r["end_date"] for r in records]


# -- off, which is the shipped state -----------------------------------------


def test_the_scrapers_own_filters_are_off_for_the_testing_phase():
    """The two the scraper decides. The third — the portal's Filter By — is now
    the user's choice per run, and defaults to no filter."""
    assert filters.EXCLUDE_KEYWORDS is False
    assert filters.APPLY_CLOSE_DATE_FILTER is False
    assert filters.summary() == "none (unfiltered)"
    assert filters.describe() == {
        "portal_filter": False, "keyword_exclusions": False, "close_date": False,
    }


def test_the_default_criterion_is_the_portals_own_no_filter_option():
    """"Select Criteria" (-1) leaves the dropdown alone — its "Posted Today"
    sibling is what used to hide everything posted before today."""
    assert filters.DEFAULT_FILTER_ID == "-1"
    assert filters.filter_label("-1") == "Select Criteria"
    assert filters.summary("-1") == "none (unfiltered)"
    assert filters.describe("-1")["portal_filter"] is False


def test_choosing_a_criterion_is_reported_as_a_filter():
    assert filters.filter_label("3") == "Posted Last 7 Days"
    assert filters.summary("3") == "portal filter (Posted Last 7 Days)"
    assert filters.describe("3")["portal_filter"] is True


def test_the_catalog_matches_the_portals_dropdown():
    """Values are the option values the engine selects on; labels are display."""
    catalog = filters.catalog()
    assert [f["value"] for f in catalog] == ["-1", "1", "2", "3", "4", "5", "6"]
    assert [f["label"] for f in catalog] == [
        "Select Criteria", "Posted Today", "Posted Last 3 Days", "Posted Last 7 Days",
        "Closing Today", "Closing Next 3 Days", "Closing Next 7 Days",
    ]


def test_an_unknown_criterion_is_rejected():
    assert filters.is_valid_filter("3")
    assert filters.is_valid_filter(None)      # -> the default
    assert not filters.is_valid_filter("99")
    assert not filters.is_valid_filter("Posted Today")  # label, not value


def test_the_listing_is_always_read_a_hundred_at_a_time():
    assert filters.PAGE_SIZE == "100"


def test_no_keywords_are_excluded_so_no_request_is_dropped():
    assert filters.excluded_keywords() == []


def test_the_close_date_rule_passes_every_record_through():
    kept, skipped, unreadable, applied = filters.apply_close_date_filter(
        RECORDS, lambda r: r["end_date"]
    )
    assert applied is False
    assert close_dates(kept) == close_dates(RECORDS)  # including the 2020 one
    assert (skipped, unreadable) == (0, 0)


def test_the_pass_through_does_not_hand_back_the_callers_own_list():
    kept, *_ = filters.apply_close_date_filter(RECORDS, lambda r: r["end_date"])
    kept.append({"buyer_number": "4"})
    assert len(RECORDS) == 3


# -- back on, which is the point of keeping them -----------------------------


def test_re_enabling_the_close_date_rule_restores_the_filtering(monkeypatch):
    monkeypatch.setattr(filters, "APPLY_CLOSE_DATE_FILTER", True)
    kept, skipped, unreadable, applied = filters.apply_close_date_filter(
        RECORDS, lambda r: r["end_date"]
    )
    assert applied is True
    # The 2020 date is dropped; the far-future one is kept, and the unreadable
    # one is kept and counted (see app/core/closing_filter).
    assert close_dates(kept) == [FAR, "not a date"]
    assert (skipped, unreadable) == (1, 1)


def test_re_enabling_the_keyword_exclusions_restores_the_original_list(monkeypatch):
    monkeypatch.setattr(filters, "EXCLUDE_KEYWORDS", True)
    active = filters.excluded_keywords()
    assert "market research" in active and "survey" in active
    # A copy, so a caller handing it to the engine cannot mutate the source.
    active.clear()
    assert filters.excluded_keywords()


def test_the_summary_names_whatever_is_on(monkeypatch):
    monkeypatch.setattr(filters, "APPLY_CLOSE_DATE_FILTER", True)
    summary = filters.summary("1")
    assert "Posted Today" in summary
    assert f"≥{MIN_DAYS_UNTIL_CLOSE}d" in summary
    assert "keyword" not in summary  # still off


# -- the run wires them through ----------------------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from main import app

    return TestClient(app)


def test_the_endpoint_defaults_to_the_portals_no_filter_option(client, monkeypatch):
    from app.core import run_manager

    monkeypatch.setattr("app.scrapers.unison.router.runner.execute_run", lambda run_id: None)
    body = client.post("/unison/scrape").json()

    run = run_manager.get_run(body["run_id"])
    assert body["search"] == "all requests"
    assert body["filter_id"] == filters.DEFAULT_FILTER_ID
    assert run["filter_label"] == "Select Criteria"
    assert run["filters_summary"] == "none (unfiltered)"


def test_the_endpoint_records_a_chosen_criterion(client, monkeypatch):
    from app.core import run_manager

    monkeypatch.setattr("app.scrapers.unison.router.runner.execute_run", lambda run_id: None)
    body = client.post("/unison/scrape", params={"filter_id": "3"}).json()

    run = run_manager.get_run(body["run_id"])
    assert body["search"] == "Posted Last 7 Days"
    assert (run["filter_id"], run["filter_label"]) == ("3", "Posted Last 7 Days")
    assert run["filters_active"]["portal_filter"] is True


def test_an_unknown_criterion_is_refused_before_a_run_exists(client):
    from app.core import run_manager

    before = len(run_manager.list_runs("unison"))
    response = client.post("/unison/scrape", params={"filter_id": "99"})
    assert response.status_code == 400
    assert "Unknown Filter By value" in response.json()["detail"]
    assert len(run_manager.list_runs("unison")) == before


def test_the_filters_endpoint_serves_the_dropdown(client):
    body = client.get("/unison/filters").json()
    assert body["default"] == filters.DEFAULT_FILTER_ID
    assert {"value": "3", "label": "Posted Last 7 Days"} in body["filters"]


# -- the run wires the choice through ----------------------------------------


class FakeDriver:
    """Just enough driver for the runner: a page to parse and cookies to reuse."""

    def __init__(self, page_source=""):
        self.page_source = page_source
        self.visited = []

    def get(self, url):
        self.visited.append(url)

    def get_cookies(self):
        return [{"name": "JSESSIONID", "value": "x", "domain": "marketplace.unisonglobal.com"}]

    def execute_script(self, script):
        return "test-agent"

    def quit(self):
        self.quit_called = True


def _fake_engine(monkeypatch, runner, rows, page_source=""):
    """Install a scraper double and stub everything the runner does around it."""
    seen: dict = {}

    class FakeScraper:
        def __init__(self):
            self.keywords_to_exclude = ["should be replaced"]
            self.headless = True
            self.pages_scraped = 0
            self.driver = FakeDriver(page_source)

        def open_listing(self, filter_id="-1", page_size="100"):
            seen["filter_id"] = filter_id
            seen["page_size"] = page_size
            seen["keywords"] = list(self.keywords_to_exclude)
            self.pages_scraped = 2
            return rows

    monkeypatch.setattr(runner, "UnisonMarketplaceScraper", FakeScraper)
    monkeypatch.setattr(runner, "_verify_credentials", lambda run_id: None)
    monkeypatch.setattr(runner, "archive_run", lambda run_id: None)
    monkeypatch.setattr(runner, "notify_scrape_completion", lambda *a, **k: None)
    monkeypatch.setattr(runner.export, "save_bids", lambda run, records: len(records))
    monkeypatch.setattr(runner, "_save_run_row", lambda run_id: None)
    return seen


def test_the_runner_passes_the_chosen_criterion_and_page_size_to_the_engine(monkeypatch, tmp_path):
    from app.core import run_manager
    from app.scrapers.unison import runner

    seen = _fake_engine(monkeypatch, runner, rows=[])
    run = run_manager.create_run("unison", tmp_path, {"filter_id": "3"})
    runner.execute_run(run["run_id"])

    assert seen["filter_id"] == "3"
    assert seen["page_size"] == "100"     # Show: 100, every run
    assert seen["keywords"] == []          # exclusions still off


def test_a_run_that_names_no_criterion_sweeps_the_whole_listing(monkeypatch, tmp_path):
    from app.core import run_manager
    from app.scrapers.unison import runner

    seen = _fake_engine(monkeypatch, runner, rows=[])
    run = run_manager.create_run("unison", tmp_path)
    runner.execute_run(run["run_id"])
    assert seen["filter_id"] == filters.DEFAULT_FILTER_ID


def test_the_runner_reads_the_detail_page_and_records_the_verdict(monkeypatch, tmp_path):
    """The whole pipeline over the fixture page, with the network stubbed out."""
    from app.core import run_manager
    from app.scrapers.unison import runner

    page = (Path(__file__).parent / "fixtures" / "unison_buy_details.html").read_text(encoding="utf-8")
    rows = [{
        "Buyer#": "1210780_01", "Buyer Description": "IT Items Open Market",
        "Buyer": "U.S. Embassy Buenos Aires", "End Date": "08/06/2026",
        "Detail URL": "https://marketplace.unisonglobal.com/fbweb/buyDetails.do?buy_id=1210979",
    }]
    _fake_engine(monkeypatch, runner, rows=rows, page_source=page)
    # No real downloads: the attachment fetch is the one part that leaves the box.
    monkeypatch.setattr(runner.documents, "download", lambda s, a, d: [
        {**item, "file": item["name"]} for item in a
    ])
    monkeypatch.setattr(runner.documents, "extract_text", lambda folder: "NDAA certification")

    captured: dict = {}
    monkeypatch.setattr(runner.export, "save_bids",
                        lambda run, records: captured.setdefault("records", records) and 0 or len(records))

    run = run_manager.create_run("unison", tmp_path)
    runner.execute_run(run["run_id"])

    record = captured["records"][0]
    # Buy # kept whole, with its suffix broken out beside it.
    assert record["buyer_number"] == "1210780_01"
    assert record["bid_upload_count"] == "01"
    # General Information reached the row.
    assert record["solicitation_number"] == "PR16173540"
    assert record["naics"].startswith("541519")
    assert record["shipping_city"] == "Buenos Aires"
    assert record["line_item_count"] == 5
    assert record["attachment_count"] == 1
    # The detail page's clean Buy Description replaced the listing's.
    assert record["buyer_description"] == "IT Items"
    # …and the verdict: a reseller-NAICS supply, pursued under Rule A.
    assert record["decision"] == "PURSUE"
    assert record["rule"] == "A"
    assert record["requirement_hinted"] is True

    stored = run_manager.get_run(run["run_id"])
    assert stored["decisions"] == {"PURSUE": 1}
    assert stored["pages_scraped"] == 2


def test_a_run_does_not_claim_a_close_date_filter_it_did_not_apply(monkeypatch, tmp_path):
    """The console renders a closing-filter note whenever a run reports one, so
    a run that filtered nothing must report nothing."""
    from app.core import run_manager
    from app.scrapers.unison import runner

    rows = [{"Buyer#": "B-1", "Buyer Description": "Long closed", "Buyer": "An agency",
             "End Date": PAST, "Detail URL": ""}]
    _fake_engine(monkeypatch, runner, rows=rows)

    run = run_manager.create_run("unison", tmp_path)
    runner.execute_run(run["run_id"])

    stored = run_manager.get_run(run["run_id"])
    assert "min_days_until_close" not in stored
    # The long-closed row survived, which is the whole point of the change.
    assert stored["bids_found"] == 1


def test_a_run_delivers_the_sheet_and_keeps_no_documents(monkeypatch, tmp_path):
    """Attachments are read into the decision, then discarded: nothing may be
    left in the run's workspace for the archive to package."""
    from app.core import exports, run_manager
    from app.scrapers.unison import runner

    page = (Path(__file__).parent / "fixtures" / "unison_buy_details.html").read_text(encoding="utf-8")
    rows = [{
        "Buyer#": "1210780_01", "Buyer Description": "IT Items",
        "Buyer": "U.S. Embassy", "End Date": "08/06/2026",
        "Detail URL": "https://marketplace.unisonglobal.com/fbweb/buyDetails.do?buy_id=1",
    }]
    _fake_engine(monkeypatch, runner, rows=rows, page_source=page)

    written: dict = {}

    def fake_download(session, attachments, target_dir):
        # A real download, minus the network: the file lands where the runner
        # pointed it, which is what the assertion below is about.
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "attachment.pdf").write_bytes(b"%PDF-1.4 ...")
        written["dir"] = target_dir
        return [{**a, "file": "attachment.pdf"} for a in attachments]

    monkeypatch.setattr(runner.documents, "download", fake_download)
    monkeypatch.setattr(runner.documents, "extract_text", lambda folder: "document text")

    run = run_manager.create_run("unison", tmp_path)
    runner.execute_run(run["run_id"])

    # It was fetched, read, and is gone — along with the scratch directory.
    assert written["dir"].name == "1210780_01"
    assert tmp_path not in written["dir"].parents   # never inside the workspace
    assert not written["dir"].exists()
    assert list(tmp_path.rglob("*.pdf")) == []
    # The names still reach the report.
    stored = run_manager.get_run(run["run_id"])
    assert stored["documents_downloaded"] == 1
    # …and the run is delivered as a bare sheet, not a ZIP.
    assert "unison" in exports.EXCEL_ONLY_PORTALS
