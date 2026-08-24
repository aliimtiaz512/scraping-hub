"""Every keyword's bids must reach the export, not just the last one's.

Written against the reported symptom — 22 keywords, 6-8 with hits, 0 bids in the
final file — to establish where the loss is and to hold that boundary. The
accumulator itself (`link_keywords` in `run()`, one dict for the whole run,
`all_records` built from it) is driven here across several keywords rather than
inspected, so a later refactor that scopes either one per-iteration fails.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_accumulation.py
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import scraper as bidnet  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper, LinkHarvest  # noqa: E402

KEYWORDS = ["gasket", "valve", "bearing", "no-match", "hose"]

# What each keyword's search returns: (portal count, links collected).
RESULTS = {
    "gasket": (3, ["https://b/1", "https://b/2", "https://b/3"]),
    "valve": (2, ["https://b/4", "https://b/5"]),
    "bearing": (2, ["https://b/2", "https://b/6"]),   # /2 also found by "gasket"
    "no-match": (0, []),
    "hose": (1, ["https://b/7"]),
}
# 1,2,3,4,5,6,7 — seven distinct solicitations across five keywords.
EXPECTED_UNIQUE = 7


@pytest.fixture
def driven(monkeypatch, tmp_path):
    """A BidnetScraper with the browser replaced but `run()`'s own loop intact."""
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    instance = BidnetScraper(run["run_id"], list(KEYWORDS), None, "X")
    current = {"keyword": None}

    def search(keyword):
        current["keyword"] = keyword

    def result_count():
        return RESULTS[current["keyword"]][0]

    def collect_links():
        links = RESULTS[current["keyword"]][1]
        return LinkHarvest(
            links=list(links), rows_detected=len(links), rows_parsed=len(links)
        )

    def process_bid(link):
        return {"reference_number": link.rsplit("/", 1)[-1], "title": link}

    for name, value in {
        "start_driver": lambda *a, **k: None,
        "login": lambda: None,
        "reset_search_state": lambda: None,
        "ensure_logged_in": lambda: None,
        "open_filtered_session": lambda: None,
        "_ensure_filters_live": lambda: None,
        "_ensure_first_result_page": lambda: True,
        "search": search,
        "result_count": result_count,
        "filter_member_agency": lambda: None,
        "confirm_filters_active": lambda keyword="": None,
        "apply_sidebar_filters": lambda keyword="": {},
        "collect_links": collect_links,
        "process_bid": process_bid,
        "_write_master_excel": lambda records: exported.append(list(records)),
        "_save_run_row": lambda: None,
        "cleanup": lambda: None,
    }.items():
        monkeypatch.setattr(instance, name, value)

    exported: list[list[dict]] = []
    saved: list[list[dict]] = []
    monkeypatch.setattr(bidnet.export, "save_bids", lambda run, records: (saved.append(list(records)), len(records))[1])
    monkeypatch.setattr(bidnet, "archive_run", lambda run_id: None)
    monkeypatch.setattr(bidnet, "notify_scrape_completion", lambda *a, **k: None)
    monkeypatch.setattr(bidnet.run_manager, "remove_empty_folder", lambda run_id: None)

    return instance, exported, saved


def test_every_keywords_bids_reach_the_excel(driven):
    instance, exported, _ = driven
    instance.run()

    assert exported, "the master Excel was never written"
    records = exported[0]
    assert len(records) == EXPECTED_UNIQUE
    assert {r["reference_number"] for r in records} == {"1", "2", "3", "4", "5", "6", "7"}


def test_the_same_records_reach_the_database(driven):
    """The packaged spreadsheet is regenerated from these rows, so a record that
    reaches the Excel but not the DB is still missing from what the client gets."""
    instance, exported, saved = driven
    instance.run()

    assert saved, "save_bids was never called"
    assert len(saved[0]) == EXPECTED_UNIQUE
    assert [r["reference_number"] for r in saved[0]] == [r["reference_number"] for r in exported[0]]


def test_a_bid_found_by_two_keywords_is_exported_once_naming_both(driven):
    instance, exported, _ = driven
    instance.run()

    shared = [r for r in exported[0] if r["reference_number"] == "2"]
    assert len(shared) == 1, "deduplicated by link, not duplicated per keyword"
    assert shared[0]["matched_keyword"] == "gasket, bearing"


def test_an_empty_keyword_does_not_reset_the_accumulator(driven):
    """"no-match" sits in the middle of the run: the keyword that returns nothing
    must not disturb what earlier keywords already collected."""
    instance, exported, _ = driven
    instance.run()

    refs = {r["reference_number"] for r in exported[0]}
    assert {"1", "2", "3"} <= refs, "bids found before the empty keyword survived it"
    assert "7" in refs, "and the keyword after it still contributed"
    assert run_manager.get_run(instance.run_id)["keywords_without_results"] == ["no-match"]


def test_the_run_reports_the_funnel_per_keyword(driven, caplog):
    """The diagnostic stream itself: each keyword accounts for its rows from what
    the portal reported through to the running total."""
    caplog.set_level(logging.INFO)
    instance, _, _ = driven
    instance.run()

    log = "\n".join(r.getMessage() for r in caplog.records)
    assert '[SEARCH EXECUTING]: (1/5) Niche: X | Input Type: KEYWORD | Term: "gasket"' in log
    assert "[PORTAL DETECTED]: 3 matching bid(s)" in log
    assert "[PARSED SUCCESS]: 3/3 row(s)" in log
    assert "[POST-FILTER]: 3 retained" in log
    assert "[RESULT]: 3 total bids found (3 new, 0 duplicates skipped)" in log
    assert "3 unique solicitation(s) queued" in log
    # …and the run-wide reconciliation at the end.
    assert "[FUNNEL]" in log
    assert f"unique solicitations queued: {EXPECTED_UNIQUE}" in log
    assert f"exported: {EXPECTED_UNIQUE}" in log


def test_a_keyword_with_hits_and_no_links_is_reported_as_an_error(monkeypatch, driven):
    """The reported failure, reproduced: the portal says there are bids and the
    parse yields none. That must be an error naming the keyword, not silence."""
    instance, _, _ = driven
    monkeypatch.setattr(
        instance, "collect_links",
        lambda: LinkHarvest(links=[], rows_detected=9, rows_parsed=0, rows_failed=9),
    )
    instance.run()

    errors = run_manager.get_run(instance.run_id)["errors"]
    assert any("the portal reported 3 bid(s) but none were collected" in e for e in errors)
    assert any("Nothing was exported although BidNet rendered" in e for e in errors)
