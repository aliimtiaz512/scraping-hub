"""Attachments are never downloaded, and no per-bid folder is ever created.

The scraper used to open each solicitation's documents tab and fetch every
`.pdf` / `.docx` / `.xlsx` / addendum into `<Reference> - <Title>/`. That is
retired: a run reads metadata and writes one spreadsheet.

These tests hold the boundary from both ends, because a half-reverted version
of this change is worse than either state — a run that still opens the documents
tab pays the whole cost and produces nothing with it. So: nothing is fetched,
nothing is written beside the sheet, and the export no longer carries a column
whose only source was the tab that is no longer opened.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_no_documents.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import scraper as bidnet  # noqa: E402
from app.scrapers.bidnet.models import EXCEL_COLUMNS  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper  # noqa: E402

DETAIL = {
    "reference_number": "0000431640",
    "solicitation_number": "2026-008",
    "solicitation_type": "RFP",
    "title": "Destination Dutchess Strategic Planning Consultant",
    "publication_date": "08/01/2026",
    "question_acceptance_deadline": "09/01/2026",
    "closing_date": "09/18/2026 05:00 PM EDT",
}


@pytest.fixture
def scraper(monkeypatch, tmp_path):
    run = run_manager.create_run("bidnet", tmp_path, {"niche_label": "X"})
    instance = BidnetScraper(run["run_id"], ["kw"], None, "X")
    monkeypatch.setattr(instance, "_scrape_detail", lambda link: dict(DETAIL))
    monkeypatch.setattr(instance, "_acknowledgement_gate", lambda: None)
    return instance, tmp_path


# -- nothing is fetched -----------------------------------------------------


def test_the_switch_is_off():
    """A named constant, so a future reader finds the decision rather than an
    absence of code."""
    assert bidnet.DOWNLOAD_DOCUMENTS is False


def test_the_documents_module_is_gone():
    """Retired with the feature. Left in place it would be dead weight that
    reads as "downloading still works, it just isn't called"."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.scrapers.bidnet.documents")


def test_processing_a_bid_writes_nothing_to_disk(scraper):
    """The per-bid folder was created by the download path. No downloads, no
    folder — checked against the workspace rather than against the code."""
    instance, workspace = scraper
    before = sorted(p.name for p in workspace.rglob("*"))

    record = instance.process_bid("https://b/1")

    assert record["reference_number"] == DETAIL["reference_number"]
    assert sorted(p.name for p in workspace.rglob("*")) == before


def test_a_scraped_record_carries_no_document_fields(scraper):
    """`documents`, `documents_downloaded` and `documents_count` were the
    download path's own output. A record still carrying them would mean the
    path is still being walked."""
    instance, _ = scraper
    record = instance.process_bid("https://b/1")

    assert "documents" not in record
    assert "documents_count" not in record
    assert "documents_downloaded" not in record


def test_the_scraper_has_no_document_collection_left(scraper):
    instance, _ = scraper
    for gone in ("_collect_documents", "_http_session"):
        assert not hasattr(instance, gone), gone


# -- the export -------------------------------------------------------------


def test_documents_count_is_not_an_exported_column():
    """Its only source was the documents tab's badge, so keeping the column
    would have meant keeping the render it exists to count."""
    assert "documents_count" not in [attr for attr, _ in EXCEL_COLUMNS]
    assert "Documents Count" not in [header for _, header in EXCEL_COLUMNS]


def test_the_column_order_ends_with_niche_then_status():
    """The layout both modes share: everything the portal published about the
    solicitation, then which search surfaced it, then how completely we read it.

    Asserted by position because that is what the requirement is about — a
    reader opening either sheet finds the same two columns in the same two
    places."""
    attrs = [attr for attr, _ in EXCEL_COLUMNS]
    assert attrs[-2:] == ["niche", "status"]
    # …and the bid's own identity leads, rather than the bookkeeping.
    assert attrs[0] == "reference_number"
    assert "detail_url" == attrs[-3]
