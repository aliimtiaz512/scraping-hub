"""Reading NAICS codes out of a spreadsheet somebody sent.

The files these have to survive are not tidy: a title row above the header, codes
with their titles stuck to them, Excel's numeric cells arriving as "541511.0",
five-digit groups meaning "everything under this", and the same code three
times. What matters as much as the codes that come out is the account of the
ones that did not — an import that quietly takes 40 rows of a 45-row file is a
run that searches less than it was given, and nobody finds out.

    server/.venv/bin/python -m pytest server/tests/test_naics_import.py
"""

import base64
import csv
import io
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.naics import importer  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

#: A stand-in reference table: the real 541511-519 family, plus neighbours.
CATALOGUE = [
    "541511", "541512", "541513", "541519",
    "541611", "541618", "541690", "541715",
    "518210", "622110", "561790",
]


def _csv(rows) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


def _parse(rows, filename="codes.csv", catalogue=CATALOGUE):
    return importer.parse(_csv(rows), filename, catalogue)


# =============================================================================
# Finding the codes
# =============================================================================


@pytest.mark.parametrize("header", [
    "naics", "NAICS", "naics_code", "NAICS Code", "naics code", "naics_codes",
    "NAICS-CODES", "Code", "codes",
])
def test_the_column_is_found_by_its_header_whatever_its_spelling(header):
    result = _parse([[header, "Title"], ["541511", "x"]])

    assert result.codes == ["541511"]
    assert header in result.source


def test_a_file_that_is_just_a_list_of_codes_is_read_from_the_first_column():
    result = _parse([["541511"], ["541512"]])

    assert result.codes == ["541511", "541512"]
    assert "first column" in result.source


def test_a_header_below_a_title_row_is_still_found():
    """Spreadsheets people have formatted by hand start with a title and a blank
    line more often than not."""
    result = _parse([
        ["City of Philadelphia — target codes", ""],
        ["", ""],
        ["NAICS Code", "Notes"],
        ["541511", "software"],
    ])

    assert result.codes == ["541511"]
    assert "NAICS Code" in result.source


def test_the_header_text_is_not_read_as_a_code():
    result = _parse([["naics"], ["541511"]])

    assert result.codes == ["541511"]
    assert not any(value == "naics" for value, _ in result.skipped)


def test_the_column_is_read_wherever_it_sits():
    result = _parse([["Agency", "NAICS Code", "Notes"], ["City", "541511", "x"]])

    assert result.codes == ["541511"]


# =============================================================================
# Normalising what is in the cells
# =============================================================================


@pytest.mark.parametrize("cell,expected", [
    ("541511", "541511"),
    ("  541511  ", "541511"),
    ("541511.0", "541511"),                 # Excel's numeric cell, exported
    (541511.0, "541511"),                   # ...and read straight from a sheet
    (541511, "541511"),
    ("'541511", "541511"),                  # Excel's text-format apostrophe
    ("541511 — Custom Computer Programming", "541511"),
    ('"541511"', "541511"),
])
def test_a_code_is_recovered_from_however_it_was_written(cell, expected):
    result = _parse([["naics"], [cell]])

    assert result.codes == [expected], f"{cell!r} did not yield {expected}"


def test_a_numeric_cell_is_not_mistaken_for_a_seven_digit_code():
    """"541511.0" run together is 5415110 — seven digits, which would be thrown
    away as too long if the decimal tail were not handled."""
    digits, why = importer.clean_entry("541511.0")

    assert (digits, why) == ("541511", "")


def test_duplicates_are_removed_and_counted():
    result = _parse([["naics"], ["541511"], ["541512"], ["541511"], [" 541511 "]])

    assert result.codes == ["541511", "541512"]
    assert result.duplicates == 2


def test_the_order_of_the_file_is_kept():
    result = _parse([["naics"], ["622110"], ["541511"], ["518210"]])

    assert result.codes == ["622110", "541511", "518210"]


def test_a_blank_cell_is_not_an_error():
    result = _parse([["naics"], ["541511"], [""], ["   "], ["541512"]])

    assert result.codes == ["541511", "541512"]
    assert result.skipped == []


# =============================================================================
# The leading-zero rule — why a short code is expanded, not padded
# =============================================================================


def test_no_naics_code_begins_with_a_zero():
    """The premise of padding. Every code in the catalogue starts 11 through 92,
    so a padded "054151" is a code that has never existed and a run filtered on
    it finds nothing."""
    assert not any(code.startswith("0") for code in CATALOGUE)


def test_a_five_digit_group_is_expanded_to_its_children_not_padded():
    """"54151" is not a damaged six-digit code — it is a real industry group,
    and what a reader means by writing it is everything underneath."""
    result = _parse([["naics"], ["54151"]])

    assert result.codes == ["541511", "541512", "541513", "541519"]
    assert result.expanded == [("54151", 4)]
    assert "054151" not in result.codes


@pytest.mark.parametrize("prefix,expected", [
    # Prefix, not "same family": 541611 begins 5416 and is correctly not under
    # 5415, however adjacent the two read to a person.
    ("5415", ["541511", "541512", "541513", "541519"]),
    ("54151", ["541511", "541512", "541513", "541519"]),
    ("5416", ["541611", "541618", "541690"]),
    ("54", ["541511", "541512", "541513", "541519", "541611", "541618",
            "541690", "541715"]),
    ("6221", ["622110"]),
])
def test_any_shorter_code_expands_to_what_sits_beneath_it(prefix, expected):
    result = _parse([["naics"], [prefix]])

    assert result.codes == expected


def test_expanding_does_not_reintroduce_a_code_already_listed():
    result = _parse([["naics"], ["541511"], ["54151"]])

    assert result.codes == ["541511", "541512", "541513", "541519"]
    assert result.duplicates == 1


def test_a_short_code_matching_nothing_is_reported_rather_than_guessed_at():
    result = _parse([["naics"], ["9999"]])

    assert result.codes == []
    assert result.skipped == [("9999", "4 digits, and no six-digit code in the "
                                       "catalogue begins with it")]


# =============================================================================
# What could not be used, and why
# =============================================================================


@pytest.mark.parametrize("cell,reason_fragment", [
    ("not a code", "no digits"),
    ("Total:", "no digits"),
    ("9999999", "too long"),
    ("5", "too short"),
])
def test_an_unusable_entry_is_reported_with_a_reason(cell, reason_fragment):
    result = _parse([["naics"], [cell]])

    assert result.codes == []
    assert len(result.skipped) == 1
    assert reason_fragment in result.skipped[0][1]


def test_a_six_digit_code_that_does_not_exist_is_rejected():
    """It would reach the portal, match nothing, and look like a quiet day."""
    result = _parse([["naics"], ["541511"], ["999999"]])

    assert result.codes == ["541511"]
    assert result.skipped == [("999999", "not a code in the NAICS catalogue")]


def test_without_a_catalogue_six_digit_codes_are_taken_at_face_value():
    """The picker still has to work when the reference table is unreachable."""
    result = importer.parse(_csv([["naics"], ["541511"], ["999999"]]), "c.csv", None)

    assert result.codes == ["541511", "999999"]


def test_an_empty_file_yields_nothing_rather_than_failing():
    result = importer.parse(b"", "empty.csv", CATALOGUE)

    assert result.codes == []


# =============================================================================
# The file formats a client actually sends
# =============================================================================


def test_a_semicolon_separated_csv_is_read():
    """Excel writes these on a machine with a European locale."""
    data = "naics;title\n541511;software\n541512;design\n".encode()
    result = importer.parse(data, "codes.csv", CATALOGUE)

    assert result.codes == ["541511", "541512"]


def test_a_csv_with_a_byte_order_mark_is_read():
    """Windows Excel puts one at the front of every CSV it saves as UTF-8, and
    it lands on the first header cell."""
    data = "﻿naics\n541511\n".encode("utf-8")
    result = importer.parse(data, "codes.csv", CATALOGUE)

    assert result.codes == ["541511"]
    assert "naics" in result.source


def test_a_real_xlsx_with_numeric_cells_is_read(tmp_path):
    """Excel stores a code as a number, not a string — which is where the
    "541511.0" problem comes from in the first place."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["NAICS Code", "Title"])
    for code in (541511, 541512, 541511, 622110):
        sheet.append([code, "x"])
    path = tmp_path / "codes.xlsx"
    workbook.save(path)

    result = importer.parse(path.read_bytes(), "codes.xlsx", CATALOGUE)

    assert result.codes == ["541511", "541512", "622110"]
    assert result.duplicates == 1


def test_a_legacy_xls_is_read():
    """The old binary format openpyxl cannot open. The fixture is a real file
    written by Excel's own format, not a renamed CSV."""
    data = (FIXTURES / "naics_codes.xls").read_bytes()

    result = importer.parse(data, "naics_codes.xls", CATALOGUE)

    assert result.codes[:3] == ["541511", "541512", "622110"]
    assert ("54151", 4) in result.expanded


def test_a_file_that_is_not_a_spreadsheet_says_so():
    with pytest.raises(ValueError) as excinfo:
        importer.parse(b"%PDF-1.4", "notes.pdf", CATALOGUE)

    assert ".csv" in str(excinfo.value) and ".xlsx" in str(excinfo.value)


# =============================================================================
# The endpoint
# =============================================================================


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from main import app

    return TestClient(app)


def _post(client, rows, filename="codes.csv"):
    return client.post("/naics/import", json={
        "filename": filename,
        "content": base64.b64encode(_csv(rows)).decode(),
    })


def test_the_endpoint_returns_the_codes_and_the_accounting(client):
    response = _post(client, [["NAICS Code"], ["541511"], ["541511"], ["not a code"]])

    assert response.status_code == 200
    body = response.json()
    assert body["codes"] == ["541511"]
    assert body["count"] == 1
    assert body["duplicates"] == 1
    assert body["skipped"] == [{"value": "not a code", "reason": "no digits in it"}]


#: The data-URL prefixes a browser actually produces. The .xlsx one is 78
#: characters — long enough that a fixed-width scan for the comma misses it,
#: which is precisely how every .xlsx upload came back as a base64 error while
#: .csv and .xls worked.
BROWSER_MIME_TYPES = [
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "",
]


@pytest.mark.parametrize("mime", BROWSER_MIME_TYPES)
def test_the_endpoint_accepts_the_browsers_data_url(client, mime):
    """FileReader.readAsDataURL gives "data:<mime>;base64,XXXX" — the client
    sends what it has rather than stripping the prefix itself, so the prefix has
    to come off however long the MIME type is."""
    payload = base64.b64encode(_csv([["naics"], ["541511"]])).decode()
    response = client.post("/naics/import", json={
        "filename": "codes.csv",
        "content": f"data:{mime};base64,{payload}",
    })

    assert response.status_code == 200, response.json()
    assert response.json()["codes"] == ["541511"]


def test_a_real_xlsx_survives_the_round_trip_the_browser_puts_it_through(client, tmp_path):
    """The reported failure, end to end: a genuine .xlsx, base64'd behind the
    long Office MIME type, exactly as the upload control sends it."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["NAICS Code", "Title"])
    for code in (541511, 541512, 541511, 622110):
        sheet.append([code, "x"])
    path = tmp_path / "codes.xlsx"
    workbook.save(path)

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    payload = base64.b64encode(path.read_bytes()).decode()
    response = client.post("/naics/import", json={
        "filename": "codes.xlsx",
        "content": f"data:{xlsx_mime};base64,{payload}",
    })

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["codes"] == ["541511", "541512", "622110"]
    assert body["duplicates"] == 1


def test_base64_wrapped_across_lines_is_accepted(client):
    """Some encoders break the payload at 76 columns; the decoder wants it
    unbroken."""
    payload = base64.b64encode(_csv([["naics"], ["541511"]])).decode()
    wrapped = "\n".join(payload[i:i + 76] for i in range(0, len(payload), 76))
    response = client.post("/naics/import", json={
        "filename": "codes.csv", "content": wrapped,
    })

    assert response.status_code == 200
    assert response.json()["codes"] == ["541511"]


def test_an_unreadable_file_is_a_bad_request_not_a_crash(client):
    response = client.post("/naics/import", json={
        "filename": "notes.pdf",
        "content": base64.b64encode(b"%PDF-1.4").decode(),
    })

    assert response.status_code == 400
    assert "spreadsheet" in response.json()["detail"]


def test_an_empty_upload_is_refused(client):
    response = client.post("/naics/import", json={"filename": "x.csv", "content": ""})

    assert response.status_code == 400


def test_an_oversized_upload_is_refused(client):
    from app.scrapers.naics.router import MAX_IMPORT_BYTES

    oversized = base64.b64encode(b"0" * (MAX_IMPORT_BYTES + 1)).decode()
    response = client.post("/naics/import", json={"filename": "big.csv", "content": oversized})

    assert response.status_code == 413
