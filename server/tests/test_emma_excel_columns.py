"""The EMMA Excel carries exactly seven columns, in the client's order.

Notice ID · Title · Due / Close Date · Publish Date · Solicitation Type · Bid Link ·
Matched Filters. Nothing else — not the other grid fields and not the varying
detail-page fields the scraper also captures (those stay in the DB). The
/emma/bids API is not cut down with it.

    server/.venv/bin/python -m pytest server/tests/test_emma_excel_columns.py
"""

import os
import sys
from types import SimpleNamespace

from openpyxl import load_workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.emma import export  # noqa: E402
from app.scrapers.emma import router as emma_router  # noqa: E402

HEADERS = ["Notice ID", "Title", "Due / Close Date", "Publish Date", "Solicitation Type", "Bid Link", "Matched Filters"]

RECORD = {
    "emma_id": "123",
    "bpm_code": "BPM059112",
    "title": "Office Supplies",
    "status": "Open",
    "close_date": "11/20/2026 2:00:00 PM",
    "publish_date": "10/19/2026 9:00:00 AM",
    "main_category": "Office",
    "solicitation_type": "Invitation for Bid",
    "issuing_agency": "Department of General Services",
    "time_remaining": "17 days",
    "award_status": "",
    "procurement_officer": "Jane Doe",
    "detail_url": "https://emma.maryland.gov/page.aspx/en/bpm/process_manage_extranet/123",
    "matched_filters": "published 2026-10-19 to 2026-10-20",
    "detail": {"Pre-Bid Conference": "None", "Contact Email": "buyer@example.gov"},
}
EXPECTED_ROW = [
    "BPM059112", "Office Supplies", "11/20/2026 2:00:00 PM", "10/19/2026 9:00:00 AM",
    "Invitation for Bid", RECORD["detail_url"], "published 2026-10-19 to 2026-10-20",
]


def _sheet(path):
    rows = list(load_workbook(path).active.iter_rows(values_only=True))
    return [list(r) for r in rows]


def test_the_fallback_excel_has_exactly_the_client_columns(tmp_path):
    out = tmp_path / "emma.xlsx"
    assert export.generate_excel_from_records([RECORD], out) == 1
    header, row = _sheet(out)
    assert header == HEADERS
    assert row == EXPECTED_ROW


def test_the_db_excel_has_exactly_the_client_columns(tmp_path, monkeypatch):
    bid = SimpleNamespace(**{k: v for k, v in RECORD.items() if k != "detail"}, raw_data={"detail": RECORD["detail"]})
    monkeypatch.setattr(export, "_rows_for_run", lambda run_id: [bid])
    out = tmp_path / "emma.xlsx"
    assert export.generate_excel("run1", out) == 1
    header, row = _sheet(out)
    assert header == HEADERS
    assert row == EXPECTED_ROW


def test_detail_page_fields_no_longer_become_columns(tmp_path):
    out = tmp_path / "emma.xlsx"
    export.generate_excel_from_records([RECORD], out)
    header = _sheet(out)[0]
    for dropped in ("ID", "Status", "Issuing Agency", "Pre-Bid Conference", "Contact Email"):
        assert dropped not in header


def test_the_bids_api_still_returns_every_field():
    bid = SimpleNamespace(**{k: v for k, v in RECORD.items() if k != "detail"}, id=7, run_id="run1", documents=[])
    data = emma_router._bid_to_dict(bid)
    for field in ("bpm_code", "status", "main_category", "issuing_agency", "procurement_officer",
                  "title", "close_date", "detail_url", "matched_filters"):
        assert field in data
    assert data["id"] == 7 and data["emma_id"] == "123"
