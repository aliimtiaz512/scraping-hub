"""RideMetro's Euna Supplier Network sweep: agency filtering, row extraction,
and the agency-grouped report.

Pure logic and stubbed elements — no browser, no portal, no DB. The HTML the
stubs mimic is the live markup captured from the account (a MUI card list on My
Network, a Bootstrap tab pane per agency portal).

    server/.venv/bin/python -m pytest server/tests/test_ridemetro_network.py
"""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openpyxl import load_workbook  # noqa: E402

from app.scrapers.ridemetro import export, opportunities, workbook  # noqa: E402
from app.scrapers.ridemetro.models import SHEET_COLUMNS  # noqa: E402
from app.scrapers.ridemetro.network import Agency, go_to_selector  # noqa: E402

HEADERS_WITH_DEPARTMENT = ["Status", "Ref. #", "Project", "Department", "Close Date", "Days Left", "Action"]
HEADERS_WITHOUT_DEPARTMENT = ["Status", "Ref. #", "Project", "Close Date", "Days Left", "Action"]


# -- stubs -------------------------------------------------------------------


class FakeLink:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, name):
        return self._href if name == "href" else None


class FakeCell:
    """A <td>: text lives in textContent (the pane's cells often render as
    invisible, which is exactly why the scraper never reads `.text`)."""

    def __init__(self, text="", links=()):
        self._text = text
        self._links = list(links)

    def get_attribute(self, name):
        return self._text if name == "textContent" else None

    @property
    def text(self):  # what Selenium returns for a non-displayed element
        return ""

    def find_elements(self, by, value):
        return self._links


class FakeRow:
    def __init__(self, cells):
        self._cells = cells

    def find_elements(self, by, value):
        if value == "td":
            return self._cells
        # A row-level anchor search: flatten the cells' links.
        return [link for cell in self._cells for link in cell._links]


class FakeDriver:
    def __init__(self, headers, rows):
        self._headers = [FakeCell(h) for h in headers]
        self._rows = rows

    def find_elements(self, by, value):
        if value == opportunities.SEL["header_cells"][1]:
            return self._headers
        if value == opportunities.SEL["rows"][1]:
            return self._rows
        return []


def _row(values, url=None):
    cells = [FakeCell(v) for v in values]
    cells.append(FakeCell("View Opportunity", links=[FakeLink(url)] if url else []))
    return FakeRow(cells)


# -- agency filtering --------------------------------------------------------


def test_incomplete_is_not_mistaken_for_complete():
    """The whole filter turns on this: "Incomplete" contains "complete", so a
    substring test would sweep exactly the agencies that must be skipped."""
    assert Agency("A", "Complete", "https://a.bonfirehub.com").is_complete
    assert Agency("A", "complete", "https://a.bonfirehub.com").is_complete
    assert not Agency("A", "Incomplete", "https://a.bonfirehub.com").is_complete
    assert not Agency("A", "INCOMPLETE", "https://a.bonfirehub.com").is_complete
    assert not Agency("A", "", "https://a.bonfirehub.com").is_complete


def test_agency_record_reports_the_skip():
    record = Agency("Ada County", "Incomplete", "https://adacounty.bonfirehub.com/registration").as_record()
    assert record["skipped"] is True
    assert record["name"] == "Ada County"


def test_go_to_selector_quotes_names_with_parentheses_and_quotes():
    name = 'Metropolitan Transit Authority of Harris County (METRO)'
    assert go_to_selector(name)[1] == f'a[aria-label="Go to {name}"]'
    assert go_to_selector('The "Big" County')[1] == 'a[aria-label="Go to The \\"Big\\" County"]'


# -- reading one agency's open list ------------------------------------------


def test_rows_are_read_by_header_not_position():
    """Only some agencies publish a Department column, so the same extractor has
    to handle both widths without shifting Close Date into it."""
    with_dept = FakeDriver(HEADERS_WITH_DEPARTMENT, [_row(
        ["Open", "IFB 2026000025", "Auxiliary Power Supply Parts", "Purchasing",
         "Aug 19th 2026, 2:00 PM CDT", "14"],
        "https://ridemetro.bonfirehub.com/opportunities/246231",
    )])
    without_dept = FakeDriver(HEADERS_WITHOUT_DEPARTMENT, [_row(
        ["Open", "BCZ26-00030328", "Advanced Metering Infrastructure",
         "Aug 7th 2026, 1:00 PM CDT", "2"],
        "https://dallascityhall.bonfirehub.com/opportunities/224829",
    )])

    a = opportunities.read_rows(with_dept)[0]
    assert a["department"] == "Purchasing"
    assert a["close_date"] == "Aug 19th 2026, 2:00 PM CDT"
    assert a["days_left"] == "14"
    assert a["opportunity_url"].endswith("/opportunities/246231")

    b = opportunities.read_rows(without_dept)[0]
    assert "department" not in b
    assert b["close_date"] == "Aug 7th 2026, 1:00 PM CDT"
    assert b["days_left"] == "2"


def test_placeholder_row_is_not_an_opportunity():
    """An agency with nothing open still renders one row of prose."""
    driver = FakeDriver(HEADERS_WITHOUT_DEPARTMENT,
                        [FakeRow([FakeCell("There are no open projects at this time.")])])
    assert opportunities.read_rows(driver) == []


def test_raw_data_keeps_the_portal_headers():
    driver = FakeDriver(HEADERS_WITH_DEPARTMENT, [_row(
        ["Open", "2026000018", "Precast Concrete Waste Receptacles", "Purchasing",
         "Aug 26th 2026, 2:00 PM CDT", "21"],
        "https://ridemetro.bonfirehub.com/opportunities/245158",
    )])
    raw = opportunities.read_rows(driver)[0]["raw_data"]
    assert raw["Ref. #"] == "2026000018"
    assert raw["Days Left"] == "21"


# -- the report --------------------------------------------------------------


ROSTER = [
    {"name": "Ada County", "url": "https://adacounty.bonfirehub.com/registration",
     "status": "Incomplete", "skipped": True, "opportunities": 0, "error": None},
    {"name": "Ada County Highway District", "url": "https://achdidaho.bonfirehub.com/",
     "status": "Complete", "skipped": False, "opportunities": 0, "error": None},
    {"name": "City of Dallas", "url": "https://dallascityhall.bonfirehub.com/",
     "status": "Complete", "skipped": False, "opportunities": 2, "error": None},
    {"name": "Houston City College", "url": "https://hccs.bonfirehub.com/",
     "status": "Complete", "skipped": False, "opportunities": 0,
     "error": "Timed out waiting for the opportunities list"},
]

RECORDS = [
    {"agency": "City of Dallas", "status": "Open", "ref_number": "BCZ26-00030328",
     "project": "Advanced Metering Infrastructure", "department": None,
     "close_date": "Aug 7th 2026, 1:00 PM CDT", "days_left": "2",
     "opportunity_url": "https://dallascityhall.bonfirehub.com/opportunities/224829"},
    {"agency": "City of Dallas", "status": "Open", "ref_number": "IFS AVI B700005",
     "project": "Purchase of Public Opinion Surveys", "department": None,
     "close_date": "Aug 10th 2026, 3:00 PM CDT", "days_left": "6",
     "opportunity_url": "https://dallascityhall.bonfirehub.com/opportunities/215486"},
]


def _report():
    tmp = TemporaryDirectory()
    out = Path(tmp.name) / "report.xlsx"
    written = export.generate_excel_from_records(RECORDS, out, ROSTER)
    return tmp, load_workbook(out).active, written


def test_report_is_grouped_by_agency_in_roster_order():
    tmp, sheet, written = _report()
    with tmp:
        assert written == 2
        banners = [
            sheet.cell(row=r, column=1).value
            for r in range(1, sheet.max_row + 1)
            if sheet.cell(row=r, column=1).font.size == 14
        ]
        assert banners == [a["name"] for a in ROSTER]


def test_each_block_is_banner_then_headers_then_rows_then_a_blank():
    tmp, sheet, _ = _report()
    with tmp:
        headers = [header for _, header in SHEET_COLUMNS]
        # Ada County: skipped, so a note stands in for its rows.
        assert sheet["A1"].value == "Ada County"
        assert [c.value for c in sheet[2]] == headers
        assert sheet["A3"].value == export.NOTE_SKIPPED
        assert all(c.value is None for c in sheet[4])
        # Ada County Highway District: visited, nothing open.
        assert sheet["A7"].value == export.NOTE_EMPTY
        # City of Dallas: two real rows under its own header row.
        assert sheet["A9"].value == "City of Dallas"
        assert [c.value for c in sheet[10]] == headers
        assert sheet["B11"].value == "BCZ26-00030328"
        assert sheet["B12"].value == "IFS AVI B700005"
        assert all(c.value is None for c in sheet[13])
        # Houston City College: reached, but the read failed — said so, not dropped.
        assert sheet["A14"].value == "Houston City College"
        assert sheet["A16"].value.startswith("Could not be read:")


def test_banner_spans_every_data_column():
    tmp, sheet, _ = _report()
    with tmp:
        merged = {str(r) for r in sheet.merged_cells.ranges}
        last = chr(ord("A") + len(SHEET_COLUMNS) - 1)
        assert f"A1:{last}1" in merged


def test_bid_urls_are_clickable():
    tmp, sheet, _ = _report()
    with tmp:
        assert sheet["G11"].hyperlink.target.endswith("/opportunities/224829")


def test_an_agency_with_rows_but_no_roster_entry_is_still_reported():
    """A roster read that came up short must not silently drop scraped rows."""
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.xlsx"
        assert export.generate_excel_from_records(RECORDS, out, []) == 2
        sheet = load_workbook(out).active
        assert sheet["A1"].value == "City of Dallas"


def test_records_without_a_reference_or_url_are_not_written():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.xlsx"
        junk = [{"agency": "City of Dallas", "status": "Open", "project": "no id at all"}]
        assert export.generate_excel_from_records(junk, out, ROSTER[:3]) == 0


def test_illegal_characters_do_not_break_the_workbook():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.xlsx"
        rows = [{"agency": "A", "ref_number": "R1", "project": "bell\x07char",
                 "opportunity_url": "https://a.bonfirehub.com/opportunities/1"}]
        workbook.build([("A", rows)], out)
        assert load_workbook(out).active["C3"].value == "bellchar"
