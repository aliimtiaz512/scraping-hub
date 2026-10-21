"""EMMA's keyword-screen tally: how many bids reached the screen, and where the
rejected ones went.

"Bids found" drops to the passed count when screening ends, so without these
numbers a run that found 66 and kept 18 cannot say what happened to the other
48. Pure logic — the detail page and its documents are stubbed out.

    server/.venv/bin/python -m pytest server/tests/test_emma_screen_tally.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.emma.scraper import EmmaScraper  # noqa: E402


@pytest.fixture(autouse=True)
def _no_db_persistence(monkeypatch):
    """Runs created here must not land in the real run_state table."""
    monkeypatch.setattr(run_manager, "_persist", lambda run: None)


def _bid(emma_id, title, detail=None):
    return {
        "emma_id": emma_id,
        "bpm_code": f"BPM{emma_id}",
        "title": title,
        "detail_url": f"https://emma.example/{emma_id}",
        "documents": [],
        "_detail": detail or {},
    }


def test_the_tally_splits_rejections_by_stage(tmp_path, monkeypatch):
    run = run_manager.create_run("emma", tmp_path, {"search": "test"})
    scraper = EmmaScraper(run["run_id"])
    scraper._records = [
        _bid("1", "Office Supplies"),                                  # passes
        _bid("2", "Building Construction"),                            # rejected on its title
        _bid("3", "Request for Information: Fleet"),                   # rejected on its title
        _bid("4", "IT Support", {"Notes": "Only Master Contracts"}),   # rejected after opening
        _bid("5", "Laboratory Equipment"),                             # passes
    ]
    opened: list[str] = []

    def fake_open(url, rec):
        # What the real detail page does: fill the record's detail fields.
        opened.append(rec["emma_id"])
        rec["detail"] = rec.pop("_detail")
        return []

    monkeypatch.setattr(scraper, "_download_solicitation_docs", fake_open)
    scraper.download_all_documents()

    state = run_manager.get_run(run["run_id"])
    assert opened == ["1", "4", "5"], "a bid rejected on its own text is never opened"
    assert state["bids_keyword_checked"] == 5
    assert state["bids_rejected_by_keyword"] == 3
    assert state["bids_rejected_before_opening"] == 2
    assert state["bids_found"] == 2
    assert [r["emma_id"] for r in scraper._records] == ["1", "5"]
